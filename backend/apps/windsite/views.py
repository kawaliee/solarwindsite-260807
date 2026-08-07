"""풍력 입지·인허가 검토 API"""
from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote

from rest_framework import status as http
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .engine import DEFAULT_RADIUS_M, MAX_RADIUS_M, MIN_RADIUS_M, compare, evaluate
from .geocode import geocode, reverse_geocode
from .models import LawReference, LocalOrdinance, SiteEvaluation
from .permits import build_roadmap, collect_laws

logger = logging.getLogger(__name__)


@api_view(['POST'])
def evaluate_site(request):
    """
    입지타당성 검토 실행

    POST body:
      { "lat": 36.1234, "lng": 128.5678, "radius_m": 100,
        "address": "...", "capacity_mw": 60,
        "sido": "경상북도", "sigungu": "청도군" }
    """
    d = request.data or {}
    # 1) 좌표 파싱 — 없으면 address 를 V-World 지오코딩해서 좌표 확보
    lat = lng = None
    try:
        lat = float(d['lat'])
        lng = float(d['lng'])
    except (KeyError, TypeError, ValueError):
        addr = (d.get('address') or '').strip()
        if addr:
            g = geocode(addr)
            if g:
                lat, lng = g['lat'], g['lng']

    if lat is None or lng is None:
        return Response(
            {'detail': 'lat/lng 또는 지오코딩 가능한 address 가 필요합니다. '
                       '(address 지오코딩은 V-World 인증키가 필요합니다.)'},
            status=http.HTTP_400_BAD_REQUEST)

    if not (33.0 <= lat <= 38.7 and 124.5 <= lng <= 132.0):
        return Response({'detail': '대한민국 영역 밖의 좌표입니다.'},
                        status=http.HTTP_400_BAD_REQUEST)

    try:
        radius_m = int(d.get('radius_m') or DEFAULT_RADIUS_M)
    except (TypeError, ValueError):
        radius_m = DEFAULT_RADIUS_M
    radius_m = max(MIN_RADIUS_M, min(MAX_RADIUS_M, radius_m))

    capacity = d.get('capacity_mw')
    try:
        capacity = float(capacity) if capacity not in (None, '') else None
    except (TypeError, ValueError):
        capacity = None

    sido = (d.get('sido') or '').strip()
    sigungu = (d.get('sigungu') or '').strip()
    address = (d.get('address') or '').strip()

    # 2) 행정구역 미입력 시 좌표를 역지오코딩해 자동 채움 → 지자체 이격거리 조례 조회가 자동으로 걸린다
    if not sido or not sigungu:
        rg = reverse_geocode(lat, lng)
        if rg:
            sido = sido or rg.get('sido', '')
            sigungu = sigungu or rg.get('sigungu', '')
            if not address:
                address = rg.get('address', '')

    result = evaluate(
        lat=lat, lng=lng, radius_m=radius_m,
        address=address,
        capacity_mw=capacity,
        sido=sido,
        sigungu=sigungu,
    )
    payload = result.to_dict()
    # 자동 판별된 행정구역/좌표를 응답에 함께 실어 프론트가 표시할 수 있게 한다
    if isinstance(payload.get('site_info'), dict):
        payload['site_info'].setdefault('sido', sido)
        payload['site_info'].setdefault('sigungu', sigungu)

    # 이력 저장 (실패해도 응답은 정상 반환)
    try:
        SiteEvaluation.objects.create(
            address=result.site_info.address, lat=lat, lng=lng, radius_m=radius_m,
            capacity_mw=capacity, sido=sido, sigungu=sigungu,
            score=result.overall_feasibility.score,
            grade=result.overall_feasibility.grade,
            summary=result.overall_feasibility.summary,
            result_json=payload,
            created_by=request.user if getattr(request.user, 'is_authenticated', False) else None,
        )
    except Exception:                                   # noqa: BLE001
        logger.exception('입지 검토 이력 저장 실패')

    return Response(payload)


@api_view(['POST'])
def compare_sites(request):
    """
    복수 후보지 비교 검토

    POST body:
      { "candidates": [
          {"label":"A안", "lat":..., "lng":..., "radius_m":100, "capacity_mw":60},
          {"label":"B안", "address":"전남 화순군 ..."}
        ] }
    """
    d = request.data or {}
    raw = d.get('candidates') or []
    if not isinstance(raw, list) or not raw:
        return Response({'detail': 'candidates 배열이 필요합니다.'},
                        status=http.HTTP_400_BAD_REQUEST)
    if len(raw) > 5:
        return Response({'detail': '한 번에 비교 가능한 후보는 최대 5곳입니다.'},
                        status=http.HTTP_400_BAD_REQUEST)

    prepared: list[dict] = []
    for idx, c in enumerate(raw, start=1):
        try:
            lat, lng = _resolve_point(c)
        except ValueError as e:
            return Response({'detail': f'후보 {idx}: {e}'}, status=http.HTTP_400_BAD_REQUEST)
        sido, sigungu, address = _resolve_admin(c, lat, lng)
        prepared.append({
            'label': c.get('label') or address or f'후보 {idx}',
            'lat': lat, 'lng': lng,
            'radius_m': max(MIN_RADIUS_M,
                            min(MAX_RADIUS_M, int(c.get('radius_m') or DEFAULT_RADIUS_M))),
            'address': address, 'sido': sido, 'sigungu': sigungu,
            'capacity_mw': _as_float(c.get('capacity_mw')),
        })

    return Response(compare(prepared))


@api_view(['POST'])
def evaluation_report(request):
    """
    검토 보고서(docx) 생성 — 이미 저장된 이력 또는 즉석 검토 결과로 만든다.

    POST body: { "evaluation_id": "<uuid>" }  또는  evaluate 와 동일한 좌표 파라미터
    """
    from django.http import HttpResponse

    from .report import build_report
    from .schemas import (
        AnalysisItem, Confidence, Coordinates, Difficulty, EvaluationResult,
        OverallFeasibility, SiteInfo, Status,
    )

    d = request.data or {}
    eval_id = d.get('evaluation_id')

    if eval_id:
        try:
            row = SiteEvaluation.objects.get(pk=eval_id)
        except (SiteEvaluation.DoesNotExist, ValueError, TypeError):
            return Response({'detail': '검토 이력을 찾을 수 없습니다.'},
                            status=http.HTTP_404_NOT_FOUND)
        payload = row.result_json or {}
        result = _result_from_payload(
            payload, AnalysisItem, Confidence, Coordinates, Difficulty,
            EvaluationResult, OverallFeasibility, SiteInfo, Status)
        sido, sigungu = row.sido, row.sigungu
        stem = row.address or f'{row.lat:.5f}_{row.lng:.5f}'
    else:
        try:
            lat, lng = _resolve_point(d)
        except ValueError as e:
            return Response({'detail': str(e)}, status=http.HTTP_400_BAD_REQUEST)
        sido, sigungu, address = _resolve_admin(d, lat, lng)
        result = evaluate(
            lat=lat, lng=lng,
            radius_m=max(MIN_RADIUS_M,
                         min(MAX_RADIUS_M, int(d.get('radius_m') or DEFAULT_RADIUS_M))),
            address=address, capacity_mw=_as_float(d.get('capacity_mw')),
            sido=sido, sigungu=sigungu,
        )
        stem = address or f'{lat:.5f}_{lng:.5f}'

    try:
        blob = build_report(result, sido=sido, sigungu=sigungu,
                            with_maps=bool(d.get('with_maps', True)))
    except ImportError as e:
        return Response(
            {'detail': f'보고서 생성 의존성이 없습니다 ({e}). '
                       'requirements.txt 반영 후 backend 이미지를 재빌드하십시오.'},
            status=http.HTTP_501_NOT_IMPLEMENTED)

    filename = f'풍력입지검토_{stem}_{datetime.now():%Y%m%d}.docx'.replace('/', '_')
    res = HttpResponse(
        blob,
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    res['Content-Disposition'] = \
        f"attachment; filename*=UTF-8''{quote(filename)}"
    return res


# ----------------------------------------------------------------------
def _as_float(v):
    try:
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _resolve_point(d: dict) -> tuple[float, float]:
    """lat/lng 또는 address(지오코딩)에서 좌표를 확정한다."""
    try:
        lat, lng = float(d['lat']), float(d['lng'])
    except (KeyError, TypeError, ValueError):
        addr = (d.get('address') or '').strip()
        g = geocode(addr) if addr else None
        if not g:
            raise ValueError('lat/lng 또는 지오코딩 가능한 address 가 필요합니다.')
        lat, lng = g['lat'], g['lng']
    if not (33.0 <= lat <= 38.7 and 124.5 <= lng <= 132.0):
        raise ValueError('대한민국 영역 밖의 좌표입니다.')
    return lat, lng


def _resolve_admin(d: dict, lat: float, lng: float) -> tuple[str, str, str]:
    """행정구역 미입력 시 역지오코딩으로 자동 채움."""
    sido = (d.get('sido') or '').strip()
    sigungu = (d.get('sigungu') or '').strip()
    address = (d.get('address') or '').strip()
    if not sido or not sigungu or not address:
        rg = reverse_geocode(lat, lng) or {}
        sido = sido or rg.get('sido', '')
        sigungu = sigungu or rg.get('sigungu', '')
        address = address or rg.get('address', '')
    return sido, sigungu, address


def _result_from_payload(payload, AnalysisItem, Confidence, Coordinates, Difficulty,
                         EvaluationResult, OverallFeasibility, SiteInfo, Status):
    """저장된 result_json → EvaluationResult 복원 (보고서 재생성용)."""
    si = payload.get('site_info') or {}
    coord = si.get('coordinates') or {}
    of = payload.get('overall_feasibility') or {}

    items = []
    for raw in payload.get('analysis_items') or []:
        items.append(AnalysisItem(
            category=raw.get('category', ''), item_name=raw.get('item_name', ''),
            status=Status(raw.get('status', 'UNKNOWN')), reason=raw.get('reason', ''),
            difficulty=Difficulty(raw.get('difficulty', 'MEDIUM')), law=raw.get('law', ''),
            article=raw.get('article', ''),
            confidence=Confidence(raw.get('confidence', 'LOW')),
            source_url=raw.get('source_url', ''), data_source=raw.get('data_source', ''),
            raw=raw.get('raw', {}), action_required=raw.get('action_required', ''),
        ))

    from .schemas import PermitStepResult

    roadmap = []
    for s in payload.get('permit_roadmap') or []:
        try:
            roadmap.append(PermitStepResult(
                order=s.get('order', 0), phase=s.get('phase', ''), name=s.get('name', ''),
                authority=s.get('authority', ''), law=s.get('law', ''),
                article=s.get('article', ''), statutory_days=s.get('statutory_days'),
                depends_on=s.get('depends_on') or [], applicable=s.get('applicable', True),
                applicability_reason=s.get('applicability_reason', ''),
                confidence=Confidence(s.get('confidence', 'LOW')),
                source_url=s.get('source_url', ''), note=s.get('note', ''),
            ))
        except (TypeError, ValueError):
            continue

    return EvaluationResult(
        site_info=SiteInfo(
            address=si.get('address', ''),
            coordinates=Coordinates(lat=coord.get('lat', 0.0), lng=coord.get('lng', 0.0)),
            # 저장된 이력을 되살리는 자리다. 기본 반경이 500m이던 시절의
            # 기록이 남아 있으므로 DEFAULT_RADIUS_M로 바꾸지 않는다.
            radius_m=si.get('radius_m', 500), total_area_m2=si.get('total_area_m2', 0.0),
        ),
        overall_feasibility=OverallFeasibility(
            score=of.get('score', 0), grade=of.get('grade', 'UNKNOWN'),
            summary=of.get('summary', '')),
        analysis_items=items,
        permit_roadmap=roadmap,
        applicable_laws=payload.get('applicable_laws') or [],
        data_gaps=payload.get('data_gaps') or [],
        evaluated_at=payload.get('evaluated_at', ''),
    )


@api_view(['POST'])
def geocode_view(request):
    """지오코딩 — 주소↔좌표 + 행정구역.

    body: { "address": "..." }  → 좌표 + 시도/시군구
       또는 { "lat": .., "lng": .. } → 시도/시군구
    """
    d = request.data or {}
    addr = (d.get('address') or '').strip()
    if addr:
        g = geocode(addr)
        if not g:
            return Response(
                {'detail': 'V-World 지오코딩에 실패했습니다(인증키 미설정 또는 주소 미매칭).'},
                status=http.HTTP_404_NOT_FOUND)
        rg = reverse_geocode(g['lat'], g['lng']) or {}
        return Response({
            'lat': g['lat'], 'lng': g['lng'], 'matched': g['matched'],
            'sido': rg.get('sido', ''), 'sigungu': rg.get('sigungu', ''),
        })

    try:
        lat = float(d['lat'])
        lng = float(d['lng'])
    except (KeyError, TypeError, ValueError):
        return Response({'detail': 'address 또는 lat/lng 가 필요합니다.'},
                        status=http.HTTP_400_BAD_REQUEST)
    rg = reverse_geocode(lat, lng)
    if not rg:
        # 실패 원인을 뭉뚱그리지 않는다. 바다·비주소 지역을 찍은 경우와
        # 인증키가 없는 경우는 사용자가 할 일이 완전히 다르다.
        from django.conf import settings
        if not getattr(settings, 'VWORLD_API_KEY', ''):
            detail = 'VWORLD_API_KEY가 설정되지 않아 주소를 조회할 수 없습니다.'
        else:
            detail = ('해당 좌표에서 주소를 찾지 못했습니다. '
                      '해상이거나 주소가 부여되지 않은 지역일 수 있습니다.')
        return Response({'detail': detail}, status=http.HTTP_404_NOT_FOUND)
    return Response({'lat': lat, 'lng': lng, **rg})


@api_view(['GET'])
def permit_roadmap(request):
    """인허가 로드맵 조회 (?capacity_mw=60&flags=FOREST,MILITARY)"""
    cap = request.query_params.get('capacity_mw')
    try:
        cap = float(cap) if cap else None
    except ValueError:
        cap = None
    flags = {f.strip() for f in (request.query_params.get('flags') or '').split(',') if f.strip()}
    steps = build_roadmap(capacity_mw=cap, site_flags=flags or None)
    return Response({'count': len(steps), 'results': [s.to_dict() for s in steps]})


@api_view(['GET'])
def law_list(request):
    """관련 법령 목록"""
    return Response({'count': LawReference.objects.count(), 'results': collect_laws()})


@api_view(['GET'])
def ordinance_list(request):
    """지자체 이격거리 조례 (?sigungu=청도군)"""
    qs = LocalOrdinance.objects.all()
    if s := request.query_params.get('sido'):
        qs = qs.filter(sido=s)
    if g := request.query_params.get('sigungu'):
        qs = qs.filter(sigungu=g)
    return Response({
        'count': qs.count(),
        'results': [{
            'sido': o.sido, 'sigungu': o.sigungu,
            'energy_type': o.energy_type,
            'target': o.get_target_display(), 'target_detail': o.target_detail,
            'distance_m': o.distance_m,
            'ordinance_name': o.ordinance_name, 'article': o.article,
            'exemption': o.exemption, 'difficulty': o.difficulty,
            'confidence': o.confidence, 'source_url': o.source_url,
            'verified_at': o.verified_at.isoformat() if o.verified_at else None,
            'note': o.note,
        } for o in qs],
    })


@api_view(['GET'])
def provider_config(request):
    """
    데이터 연동 상태 — 어떤 API 키가 설정됐고 무엇이 비었는지 보여준다.
    화면에서 '무엇을 발급받아야 하는지' 안내하는 데 쓴다.
    """
    from .engine import build_providers

    rows = []
    for p in build_providers():
        keys = [*p.required_settings, *p.optional_settings]
        missing = p.missing_settings()
        rows.append({
            'item_name': p.item_name,
            'category': p.category,
            'data_source': p.data_source,
            'required_settings': list(p.required_settings),
            'optional_settings': list(p.optional_settings),
            # 필수 키가 비면 조회 자체를 못 한다. 선택 키는 없어도 동작하되 판정이 얕아진다.
            'configured': (not missing) if keys else None,
            'missing': missing,
            'missing_optional': p.missing_optional(),
            'active_keys': p.configured_settings(),
        })
    return Response({
        'results': rows,
        'note': '인증키가 필요 없는 항목도 자동 판정됩니다(내부 적재 공간데이터·OSM 등). '
                '군사기지·비행안전구역과 계통 접속 가능 용량 확정은 공개 API가 없어 '
                '기관 협의가 필요합니다.',
    })


@api_view(['GET'])
def evaluation_history(request):
    """최근 검토 이력"""
    qs = SiteEvaluation.objects.all()[:50]
    return Response({
        'count': qs.count(),
        'results': [{
            'id': str(e.id), 'address': e.address,
            'lat': e.lat, 'lng': e.lng, 'radius_m': e.radius_m,
            'capacity_mw': e.capacity_mw,
            'score': e.score, 'grade': e.grade, 'summary': e.summary,
            'created_at': e.created_at.isoformat(),
        } for e in qs],
    })
