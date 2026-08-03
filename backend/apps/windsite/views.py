"""풍력 입지·인허가 검토 API"""
from __future__ import annotations

import logging

from django.conf import settings
from rest_framework import status as http
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .engine import evaluate
from .geocode import geocode, reverse_geocode
from .models import LawReference, LocalOrdinance, PermitStep, SiteEvaluation
from .permits import build_roadmap, collect_laws

logger = logging.getLogger(__name__)


@api_view(['POST'])
def evaluate_site(request):
    """
    입지타당성 검토 실행

    POST body:
      { "lat": 36.1234, "lng": 128.5678, "radius_m": 500,
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
        radius_m = int(d.get('radius_m') or 500)
    except (TypeError, ValueError):
        radius_m = 500
    radius_m = max(50, min(20000, radius_m))

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
        return Response({'detail': 'V-World 역지오코딩에 실패했습니다(인증키 미설정).'},
                        status=http.HTTP_404_NOT_FOUND)
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
        rows.append({
            'item_name': p.item_name,
            'category': p.category,
            'data_source': p.data_source,
            'required_settings': list(p.required_settings),
            'configured': p.is_configured() if p.required_settings else None,
            'missing': p.missing_settings() if p.required_settings else [],
        })
    return Response({
        'results': rows,
        'note': '공개 API가 없는 항목(군사·비행안전, 전력계통, KIER 풍황)은 키를 넣어도 '
                '자동 판정되지 않으며 기관 협의가 필요합니다.',
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
