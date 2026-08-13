"""풍력 입지·인허가 검토 API"""
from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote

from django.http import HttpResponse
from rest_framework import status as http
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import area_report, available, geo, jobs, jurisdiction
from .engine import DEFAULT_RADIUS_M, MAX_RADIUS_M, MIN_RADIUS_M, compare, evaluate
from .geocode import geocode, reverse_geocode
from .models import LawReference, LocalOrdinance, SiteEvaluation
from .permits import build_roadmap, collect_laws

logger = logging.getLogger(__name__)


#: 사업구역 폴리곤 상한. 국내 최대급 육상풍력 단지도 이 아래다.
#: 잘못 그린 구역이 수십 번의 타일 조회로 번지는 것을 입구에서 막는다.
MAX_AREA_KM2 = 500
#: 꼭짓점 상한 — 지나치게 잘게 그린 도형은 공간연산을 느리게만 만든다.
MAX_AREA_POINTS = 500


@api_view(['POST'])
def evaluate_area(request):
    """
    사업구역(폴리곤) 제약도 검토

    POST body — 둘 중 하나
      { "ring": [[lat, lng], …] }                 사업구역 폴리곤 (꼭짓점 3개 이상)
      { "turbines": [[lat, lng], …],              발전기 배치선 (1기 이상, 순서대로)
        "turbine_radius_m": 500,
        "corridor_radius_m": 100 }

    점 검토(evaluate_site)와 달리 항목별 가부가 아니라 **면적 분포**를 낸다.
    수천 ha 구역은 어딘가 반드시 규제에 걸리므로 가부 판정이 성립하지 않는다.
    """
    parsed = _parse_area_request(request.data or {})
    if isinstance(parsed, Response):
        return parsed
    ring, is_layout, permit_date, radii = parsed

    try:
        if is_layout:
            result = available.compute_layout(ring, permit_date=permit_date, **radii)
        else:
            result = available.compute(ring, permit_date=permit_date)
    except ValueError as e:
        return Response({'detail': str(e)}, status=http.HTTP_400_BAD_REQUEST)
    except jurisdiction.BoundaryUnavailable as e:
        # 행정경계를 못 받으면 어느 조례를 적용할지 정할 수 없다.
        # 빈손으로 계산해 넘기면 조례 제약이 통째로 빠진 결과가 나온다.
        return Response({'detail': f'행정경계를 조회하지 못했습니다: {e}'},
                        status=http.HTTP_502_BAD_GATEWAY)

    if result['total_area_m2'] > MAX_AREA_KM2 * 1e6:
        return Response(
            {'detail': f'사업구역이 너무 넓습니다 '
                       f'({result["total_area_m2"] / 1e6:,.0f}km², 상한 {MAX_AREA_KM2}km²).'},
            status=http.HTTP_400_BAD_REQUEST)

    return Response(_area_payload(result, ring))


#: 클라이언트가 요청을 접었을 때 쓰는 상태. 표준 코드가 없어 널리 쓰이는
#: 499(Client Closed Request)를 따른다. 오류가 아니므로 화면에 빨간 문구를
#: 띄우지 않도록 프런트에서 따로 다룬다.
HTTP_CLIENT_CLOSED = 499


def _cancelled():
    return Response({'detail': '사용자 요청으로 중단했습니다.', 'cancelled': True},
                    status=HTTP_CLIENT_CLOSED)


@api_view(['POST'])
def area_report_cancel(request):
    """
    진행 중인 구역 보고서 생성을 중단 요청한다.

    플래그만 세운다. 작업은 다음 확인 지점에서 스스로 멈춘다 — 이미 나간
    HTTP 요청 하나는 타임아웃까지 기다리므로 즉시 멈추지는 않는다.
    """
    job_id = str((request.data or {}).get('job_id') or '')[:64]
    if not job_id:
        return Response({'detail': 'job_id가 필요합니다.'},
                        status=http.HTTP_400_BAD_REQUEST)
    jobs.request_cancel(job_id)
    return Response({'ok': True, 'job_id': job_id})


def _capacity(d: dict):
    try:
        v = d.get('capacity_mw')
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _parse_area_request(d: dict):
    """
    구역/배치선 요청 본문을 검증한다.
    → (ring, is_layout, permit_date, radii) 또는 오류 Response.

    검토 실행과 보고서 생성이 같은 입력을 받으므로 파싱을 한 곳에 둔다.
    두 벌로 두면 한쪽만 고쳐져 화면 값과 문서 값이 어긋난다.
    """
    turbines_raw = d.get('turbines')
    is_layout = isinstance(turbines_raw, list) and len(turbines_raw) > 0
    raw = turbines_raw if is_layout else (d.get('ring') or [])
    label = '발전기 위치' if is_layout else '꼭짓점'
    min_n = 1 if is_layout else 3

    if not isinstance(raw, list) or len(raw) < min_n:
        return Response({'detail': f'{label} {min_n}개 이상이 필요합니다.'},
                        status=http.HTTP_400_BAD_REQUEST)
    if len(raw) > MAX_AREA_POINTS:
        return Response({'detail': f'{label}는 {MAX_AREA_POINTS}개 이하여야 합니다.'},
                        status=http.HTTP_400_BAD_REQUEST)

    ring = []
    for p in raw:
        try:
            lat, lng = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            return Response({'detail': '좌표는 [[위도, 경도], …] 형식이어야 합니다.'},
                            status=http.HTTP_400_BAD_REQUEST)
        if not (33.0 <= lat <= 38.7 and 124.5 <= lng <= 132.0):
            return Response({'detail': '대한민국 영역 밖의 좌표가 포함되어 있습니다.'},
                            status=http.HTTP_400_BAD_REQUEST)
        ring.append((lat, lng))

    # 발전사업허가일 — 조례 시행일보다 앞서면 부칙 경과조치 검토 대상이 된다.
    # 형식이 어긋나면 조용히 무시하지 않고 400으로 돌려준다. 날짜를 잘못 넣은
    # 채 '경과규정 해당 없음'으로 읽히면 결론이 통째로 달라진다.
    permit_date = None
    raw_pd = (d.get('permit_date') or '').strip()
    if raw_pd:
        try:
            permit_date = datetime.strptime(raw_pd, '%Y-%m-%d').date()
        except ValueError:
            return Response({'detail': 'permit_date 는 YYYY-MM-DD 형식이어야 합니다.'},
                            status=http.HTTP_400_BAD_REQUEST)

    def radius(key: str, fallback: int) -> int:
        try:
            v = int(d.get(key) or fallback)
        except (TypeError, ValueError):
            return fallback
        return max(MIN_RADIUS_M, min(MAX_RADIUS_M, v))

    radii = {
        'turbine_radius_m': radius('turbine_radius_m',
                                   available.DEFAULT_TURBINE_RADIUS_M),
        'corridor_radius_m': radius('corridor_radius_m',
                                    available.DEFAULT_CORRIDOR_RADIUS_M),
    }
    return ring, is_layout, permit_date, radii


@api_view(['POST'])
def area_report_download(request):
    """
    사업구역 제약도 보고서(docx) 내려받기.

    evaluate-area와 같은 body를 받아 다시 계산한다. 결과를 세션에 들고 있다가
    쓰면 화면에 보이는 값과 문서가 어긋날 수 있고(입력을 바꾼 뒤 눌렀을 때),
    캐시가 걸려 있어 재계산 비용도 크지 않다.
    """
    d = request.data or {}
    parsed = _parse_area_request(d)
    if isinstance(parsed, Response):
        return parsed
    ring, is_layout, permit_date, radii = parsed
    # 클라이언트가 만든 작업 id. 취소 요청과 실행 중인 작업을 잇는 유일한 끈이다.
    job_id = str(d.get('job_id') or '')[:64]
    jobs.clear(job_id)      # 같은 id의 지난 취소 플래그가 남아 있으면 즉시 죽는다

    try:
        if is_layout:
            result = available.compute_layout(ring, permit_date=permit_date,
                                              job_id=job_id, **radii)
        else:
            result = available.compute(ring, permit_date=permit_date, job_id=job_id)
    except jobs.Cancelled:
        return _cancelled()
    except ValueError as e:
        return Response({'detail': str(e)}, status=http.HTTP_400_BAD_REQUEST)
    except jurisdiction.BoundaryUnavailable as e:
        return Response({'detail': f'행정경계를 조회하지 못했습니다: {e}'},
                        status=http.HTTP_502_BAD_GATEWAY)

    # 규제 62개 항목·풍황·계통은 지점에서만 성립하므로 호기마다 따로 돌린다.
    # 면적 분포만으로는 '어느 호기가 무엇에 걸리는지'를 알 수 없어 배치를 고칠 수 없다.
    evals = []
    try:
        layout = result.get('layout')
        pts = [(a, o) for a, o in layout['turbines']] if layout else [
            (result['geoms']['area'].centroid.y, result['geoms']['area'].centroid.x)]
        if layout:
            evals = available.evaluate_points(
                pts, radius_m=layout['turbine_radius_m'],
                capacity_mw=_capacity(d), job_id=job_id)
        else:
            # 폴리곤 검토에는 호기가 없다. 구역 대표점 한 곳에서 항목 평가를 낸다.
            c = result['geoms']['area'].centroid
            lat, lng = geo.to_geographic_xy(c.x, c.y)[1], geo.to_geographic_xy(c.x, c.y)[0]
            evals = available.evaluate_points(
                [(lat, lng)], radius_m=100,
                capacity_mw=_capacity(d), label='지점', job_id=job_id)
    except jobs.Cancelled:
        return _cancelled()
    except Exception:                                           # noqa: BLE001
        # 항목 평가가 실패해도 면적 보고서는 나와야 한다. 빠졌다는 사실은
        # 보고서 '한계'에 남는다.
        logger.exception('호기별 항목 평가 실패')

    try:
        jobs.check(job_id)
        blob = area_report.build_area_report(result, evals)
    except jobs.Cancelled:
        return _cancelled()
    except Exception as e:                                      # noqa: BLE001
        logger.exception('구역 보고서 생성 실패')
        return Response({'detail': f'보고서 생성에 실패했습니다: {type(e).__name__}'},
                        status=http.HTTP_500_INTERNAL_SERVER_ERROR)

    jobs.clear(job_id)
    name = f'풍력구역검토_{datetime.now():%Y-%m-%d}.docx'
    res = HttpResponse(
        blob,
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    res['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(name)}"
    return res


def _area_payload(r: dict, ring: list) -> dict:
    """계산 결과에서 도형을 걷어내고 화면이 쓸 형태로 만든다."""
    total = r['total_area_m2'] or 1.0

    def block(m2: float) -> dict:
        return {'area_m2': round(m2, 1), 'ha': round(m2 / 10_000, 2),
                'ratio': round(m2 / total, 4)}

    geoms = r.get('geoms') or {}
    return {
        'ring': [[round(a, 6), round(o, 6)] for a, o in ring],
        'total': block(r['total_area_m2']),
        'blocked': block(r['blocked_m2']),
        'conditional': block(r['conditional_m2']),
        'free': block(r['free_m2']),
        'pending': block(r['pending_m2']),
        'available_strict': block(r['available_strict_m2']),
        'available_with_consultation': block(r['available_with_consultation_m2']),
        'by_reason': [
            {**b, 'ha': round(b['area_m2'] / 10_000, 2),
             'area_m2': round(b['area_m2'], 1), 'ratio': round(b['ratio'], 4)}
            for b in r['by_reason']],
        'zoning': [
            {**z, 'ha': round(z['area_m2'] / 10_000, 2),
             'area_m2': round(z['area_m2'], 1), 'ratio': round(z['ratio'], 4)}
            for z in r['zoning']],
        'blanket': r['blanket'],
        # 배치선 검토일 때만 채워진다 (발전기 좌표·반경·구간별 면적).
        'layout': r.get('layout'),
        # 조례 경과규정 검토 — 시스템은 면제를 판정하지 않고 근거만 제시한다.
        'grandfathering': r.get('grandfathering'),
        'jurisdictions': r['jurisdictions'],
        'jurisdiction_meta': r['jurisdiction_meta'],
        'fetch_failures': r['fetch_failures'],
        'notes': r['notes'],
        # 화면에 겹쳐 그릴 수 있도록 위경도 링으로 돌려준다.
        'overlays': {k: geo.rings_4326(geoms.get(k))
                     for k in ('blocked', 'conditional', 'free')},
        'evaluated_at': datetime.now().isoformat(timespec='seconds'),
    }


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
