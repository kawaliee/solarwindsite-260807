"""
검토 프로젝트·배치안 API
---------------------------------------------------------------
배치선을 찍어 검토한 결과를 사업명 아래 '배치안'으로 남기고, 나중에 다시
불러오거나 서로 견주기 위한 얇은 CRUD다.

■ 무엇을 저장하고 무엇을 저장하지 않는가

저장하는 것은 **호기 좌표와 검토 조건**이다. 62개 항목의 판정 전문은
담지 않는다. 규제와 조례는 개정되므로 몇 달 뒤에 옛 판정을 그대로 펼쳐
보이면 지금도 그런 줄 알게 된다. 좌표만 남기면 언제 불러도 그 시점의
규제로 다시 판정할 수 있다.

다만 점수·등급·가용면적 같은 **요약은 산출 시점과 함께** 남긴다.
목록에서 배치안을 견줄 때 매번 몇 분씩 재검토할 수는 없기 때문이다.
그 값이 언제 것인지 화면에 함께 적어 오해를 막는다.
"""
from __future__ import annotations

import logging

from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status as http
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import energy as energy_mod
from .models import SitePlan, SiteProject

logger = logging.getLogger(__name__)

MAX_TURBINES = 200
MAX_NAME = 120


def _user(request):
    return request.user if getattr(request.user, 'is_authenticated', False) else None


def project_dict(p: SiteProject, with_plans: bool = False) -> dict:
    d = {
        'id': str(p.id), 'name': p.name, 'description': p.description,
        'energy_type': p.energy_type,
        'sido': p.sido, 'sigungu': p.sigungu,
        'plan_count': p.plans.count(),
        'created_at': _stamp(p.created_at),
        'updated_at': _stamp(p.updated_at),
    }
    if with_plans:
        d['plans'] = [plan_dict(x) for x in p.plans.all()]
    return d


def _stamp(dt) -> str:
    """
    화면·보고서에 쓸 시각. **한국시간으로 낸다.**

    USE_TZ=True라 DB에는 UTC로 담기고 `isoformat()`도 UTC를 돌려준다. 그대로
    보내면 화면에 오후 1시 검토가 오전 4시로 찍힌다 — 같은 날 여러 번 검토한
    것을 시각으로 가리려는 것인데 그 시각이 9시간 어긋나면 쓸모가 없다.
    """
    return timezone.localtime(dt).isoformat(timespec='seconds') if dt else ''


def plan_dict(x: SitePlan) -> dict:
    return {
        'id': str(x.id),
        'project_id': str(x.project_id), 'project_name': x.project.name,
        'name': x.name, 'note': x.note,
        # 검토자 — 로그인 계정과 다를 수 있어 따로 남긴다
        'reviewer': x.reviewer,
        'mode': x.mode,
        'turbines': x.turbines, 'turbine_count': len(x.turbines or []),
        'turbine_radius_m': x.turbine_radius_m,
        'corridor_radius_m': x.corridor_radius_m,
        'capacity_mw': x.capacity_mw, 'permit_date': x.permit_date,
        'sido': x.sido, 'sigungu': x.sigungu,
        'summary': x.summary or {},
        # 요약이 언제 것인지 반드시 함께 낸다 — 규제는 개정된다.
        'evaluated_at': _stamp(x.evaluated_at),
        'created_at': _stamp(x.created_at),
        'updated_at': _stamp(x.updated_at),
    }


# ── 프로젝트 ──────────────────────────────────────────────────────────
@api_view(['GET', 'POST'])
def project_list(request):
    if request.method == 'GET':
        # 에너지원별로 가른다. 풍력 배치안과 태양광 후보가 한 목록에 섞이면
        # 무엇을 견주는 것인지 알 수 없다.
        qs = SiteProject.objects.prefetch_related('plans').all()
        if e := request.query_params.get('energy'):
            qs = qs.filter(energy_type=energy_mod.normalize(e))
        return Response({'count': qs.count(),
                         'results': [project_dict(p, with_plans=True) for p in qs]})

    d = request.data or {}
    name = (d.get('name') or '').strip()[:MAX_NAME]
    if not name:
        return Response({'detail': '사업명을 입력하십시오.'},
                        status=http.HTTP_400_BAD_REQUEST)
    try:
        p = SiteProject.objects.create(
            name=name, description=(d.get('description') or '').strip(),
            energy_type=energy_mod.normalize(d.get('energy')),
            sido=(d.get('sido') or '')[:50], sigungu=(d.get('sigungu') or '')[:50],
            created_by=_user(request))
    except IntegrityError:
        return Response({'detail': f'"{name}" 사업이 이미 있습니다.'},
                        status=http.HTTP_409_CONFLICT)
    return Response(project_dict(p, with_plans=True), status=http.HTTP_201_CREATED)


@api_view(['GET', 'PATCH', 'DELETE'])
def project_detail(request, pk):
    p = get_object_or_404(SiteProject, pk=pk)
    if request.method == 'GET':
        return Response(project_dict(p, with_plans=True))
    if request.method == 'DELETE':
        # 배치안도 함께 사라진다. 되돌릴 수 없으므로 화면에서 한 번 더 묻는다.
        n = p.plans.count()
        p.delete()
        return Response({'ok': True, 'deleted_plans': n})

    d = request.data or {}
    for f in ('name', 'description', 'sido', 'sigungu'):
        if f in d:
            setattr(p, f, (d.get(f) or '').strip()[:MAX_NAME if f == 'name' else 2000])
    if not p.name:
        return Response({'detail': '사업명은 비울 수 없습니다.'},
                        status=http.HTTP_400_BAD_REQUEST)
    try:
        p.save()
    except IntegrityError:
        return Response({'detail': f'"{p.name}" 사업이 이미 있습니다.'},
                        status=http.HTTP_409_CONFLICT)
    return Response(project_dict(p, with_plans=True))


# ── 배치안 ────────────────────────────────────────────────────────────
@api_view(['POST'])
def plan_create(request):
    """
    배치안 저장.

    사업은 id로 지목하거나 이름으로 준다. 이름이 처음 보는 것이면 사업을
    함께 만든다 — 저장하려면 사업부터 만들라고 되돌려 보내면, 지도를 찍어
    둔 상태에서 화면을 두 번 오가야 한다.
    """
    d = request.data or {}
    turbines = _clean_turbines(d.get('turbines'))
    if not turbines:
        return Response({'detail': '저장할 좌표가 없습니다.'},
                        status=http.HTTP_400_BAD_REQUEST)

    project, err = _resolve_project(d, request)
    if err:
        return err

    mode = d.get('mode') if d.get('mode') in dict(SitePlan.MODE_CHOICES) else 'layout'
    name = (d.get('name') or '').strip()[:MAX_NAME] or _next_plan_name(project, mode)
    if project.plans.filter(name=name).exists():
        return Response({'detail': f'"{project.name}"에 "{name}"이 이미 있습니다.'},
                        status=http.HTTP_409_CONFLICT)

    x = SitePlan.objects.create(
        project=project, name=name, note=(d.get('note') or '').strip(),
        reviewer=(d.get('reviewer') or '').strip()[:60],
        mode=mode, turbines=turbines,
        turbine_radius_m=_int(d.get('turbine_radius_m'), 200),
        corridor_radius_m=_int(d.get('corridor_radius_m'), 100),
        capacity_mw=_float(d.get('capacity_mw')),
        permit_date=(d.get('permit_date') or '')[:10],
        sido=(d.get('sido') or '')[:50], sigungu=(d.get('sigungu') or '')[:50],
        summary=d.get('summary') or {},
        evaluated_at=timezone.now() if d.get('summary') else None,
        created_by=_user(request))
    # 사업 목록을 최근 손댄 순으로 보이게 한다.
    project.save(update_fields=['updated_at'])
    return Response(plan_dict(x), status=http.HTTP_201_CREATED)


@api_view(['GET', 'PATCH', 'DELETE'])
def plan_detail(request, pk):
    x = get_object_or_404(SitePlan.objects.select_related('project'), pk=pk)
    if request.method == 'GET':
        return Response(plan_dict(x))
    if request.method == 'DELETE':
        x.delete()
        return Response({'ok': True})

    d = request.data or {}
    if 'name' in d:
        new = (d.get('name') or '').strip()[:MAX_NAME]
        if not new:
            return Response({'detail': '배치안명은 비울 수 없습니다.'},
                            status=http.HTTP_400_BAD_REQUEST)
        if x.project.plans.filter(name=new).exclude(pk=x.pk).exists():
            return Response({'detail': f'"{new}"이 이미 있습니다.'},
                            status=http.HTTP_409_CONFLICT)
        x.name = new
    if 'note' in d:
        x.note = (d.get('note') or '').strip()
    if 'reviewer' in d:
        x.reviewer = (d.get('reviewer') or '').strip()[:60]
    if 'turbines' in d:
        t = _clean_turbines(d.get('turbines'))
        if not t:
            return Response({'detail': '저장할 호기 좌표가 없습니다.'},
                            status=http.HTTP_400_BAD_REQUEST)
        x.turbines = t
    for f, cast, dflt in (('turbine_radius_m', _int, 200),
                          ('corridor_radius_m', _int, 100)):
        if f in d:
            setattr(x, f, cast(d.get(f), dflt))
    if 'capacity_mw' in d:
        x.capacity_mw = _float(d.get('capacity_mw'))
    if 'permit_date' in d:
        x.permit_date = (d.get('permit_date') or '')[:10]
    if 'summary' in d:
        x.summary = d.get('summary') or {}
        x.evaluated_at = timezone.now() if x.summary else None
    x.save()
    x.project.save(update_fields=['updated_at'])
    return Response(plan_dict(x))


# ── 보조 ──────────────────────────────────────────────────────────────
def _resolve_project(d: dict, request):
    """project_id 또는 project(이름)로 사업을 찾거나 만든다. → (project, 오류응답)"""
    pid = d.get('project_id')
    if pid:
        try:
            return SiteProject.objects.get(pk=pid), None
        except (SiteProject.DoesNotExist, ValueError, TypeError):
            return None, Response({'detail': '사업을 찾을 수 없습니다.'},
                                  status=http.HTTP_404_NOT_FOUND)

    pname = (d.get('project') or '').strip()[:MAX_NAME]
    if not pname:
        return None, Response({'detail': '사업명을 입력하십시오.'},
                              status=http.HTTP_400_BAD_REQUEST)
    p, _ = SiteProject.objects.get_or_create(
        name=pname,
        defaults={'energy_type': energy_mod.normalize(d.get('energy')),
                  'sido': (d.get('sido') or '')[:50],
                  'sigungu': (d.get('sigungu') or '')[:50],
                  'created_by': _user(request)})
    return p, None


def _next_plan_name(project: SiteProject, mode: str = 'layout') -> str:
    """'배치안 1' / '후보 1'… 이미 쓴 번호는 건너뛴다."""
    # 태양광 후보 필지를 '배치안'이라 부르면 무엇을 저장한 것인지 어긋난다.
    stem = '후보' if mode == 'parcel' else '배치안'
    used = set(project.plans.values_list('name', flat=True))
    for i in range(1, 999):
        cand = f'{stem} {i}'
        if cand not in used:
            return cand
    return '배치안'


def _clean_turbines(raw) -> list[list[float]]:
    """[[위도, 경도], …]만 남긴다. 대한민국 범위를 벗어난 값은 버린다."""
    out: list[list[float]] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for p in raw[:MAX_TURBINES]:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        try:
            lat, lng = float(p[0]), float(p[1])
        except (TypeError, ValueError):
            continue
        if 33.0 <= lat <= 39.0 and 124.0 <= lng <= 132.0:
            out.append([round(lat, 6), round(lng, 6)])
    return out


def _int(v, dflt: int) -> int:
    try:
        return max(1, int(float(v)))
    except (TypeError, ValueError):
        return dflt


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
