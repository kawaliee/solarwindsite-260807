"""풍력 입지·인허가 검토 API"""
from __future__ import annotations

import logging

from django.conf import settings
from rest_framework import status as http
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .engine import evaluate
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
    try:
        lat = float(d['lat'])
        lng = float(d['lng'])
    except (KeyError, TypeError, ValueError):
        return Response({'detail': 'lat, lng는 필수이며 숫자여야 합니다.'},
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

    result = evaluate(
        lat=lat, lng=lng, radius_m=radius_m,
        address=(d.get('address') or '').strip(),
        capacity_mw=capacity,
        sido=(d.get('sido') or '').strip(),
        sigungu=(d.get('sigungu') or '').strip(),
    )
    payload = result.to_dict()

    # 이력 저장 (실패해도 응답은 정상 반환)
    try:
        SiteEvaluation.objects.create(
            address=result.site_info.address, lat=lat, lng=lng, radius_m=radius_m,
            capacity_mw=capacity, sido=d.get('sido', ''), sigungu=d.get('sigungu', ''),
            score=result.overall_feasibility.score,
            grade=result.overall_feasibility.grade,
            summary=result.overall_feasibility.summary,
            result_json=payload,
            created_by=request.user if getattr(request.user, 'is_authenticated', False) else None,
        )
    except Exception:                                   # noqa: BLE001
        logger.exception('입지 검토 이력 저장 실패')

    return Response(payload)


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
