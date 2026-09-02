"""입지·인허가 검토 API — 풍력·태양광 공용

요청 본문(또는 질의)의 ``energy`` 로 에너지원을 가른다. 값이 없으면 풍력이다
(종전 클라이언트가 그대로 동작하도록). 갈라지는 것은 이격거리 조례·인허가
절차·적용 법령·자원 항목뿐이며, 규제 레이어 62종은 양쪽이 공유한다.
"""
from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime
from urllib.parse import quote

from django.http import HttpResponse
from rest_framework import status as http
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import (area_report, available, energy as energy_mod, geo, jobs,
               jurisdiction, parcels, screening)
from .engine import DEFAULT_RADIUS_M, MAX_RADIUS_M, MIN_RADIUS_M, compare, evaluate
from .geocode import geocode, reverse_geocode
from .models import LawReference, LocalOrdinance, SiteEvaluation
from .permits import build_roadmap, collect_laws

logger = logging.getLogger(__name__)


#: 사업명 최대 길이 — 파일명이 길어지면 내려받기가 실패하는 브라우저가 있다.
MAX_PROJECT_NAME = 60

#: 파일명에 쓸 수 없는 글자. 윈도·맥·리눅스에서 금지되는 것을 모두 뺀다.
_BAD_FILENAME = str.maketrans(
    {c: '-' for c in list(r'\\/:*?"<>|') + ['\r','\n','\t']})


def _report_filename(nrg: str, project: str) -> str:
    """
    보고서 파일명 — **사내 표기를 하나로 통일한다.**

        태양광 입지타당성 검토 보고서_장흥 염해농지 태양광_260825.docx

    종전에는 「태양광구역검토_2026-08-25.docx」였다. 검토 방식(구역·필지·
    배치)이 이름에 들어가 있어 같은 사업지의 문서가 서로 다른 이름으로
    쌓였고, 정작 **어느 사업의 보고서인지**는 파일명만 보고 알 수 없었다.
    사업명이 들어가야 폴더에서 문서를 찾을 수 있다.

    사업명이 없으면 그 자리를 비운다 — 「미지정」 같은 말을 지어내면 그것이
    사업명인 줄 알고 인용된다.
    """
    from .energy import profile
    title = profile(nrg).report_title          # 태양광 입지타당성 검토 보고서
    name = (project or '').strip().translate(_BAD_FILENAME)[:MAX_PROJECT_NAME].strip()
    stamp = datetime.now().strftime('%y%m%d')
    return '_'.join(x for x in (title, name, stamp) if x) + '.docx'


def _project_name(d: dict) -> str:
    """요청이 실어 보낸 사업명. 화면의 저장 양식이 쓰는 값과 같다."""
    return str(d.get('project_name') or d.get('project') or '').strip()

#: 필지 항목 평가에 쓰는 반경(m).
#: 면적은 필지 경계로 재지만, 규제 62개 항목은 지점 기준이라 반경이 필요하다.
#: 필지 중심에서 이 반경으로 주변 규제를 훑는다 — 값이 작으면 경계 밖 규제를
#: 놓치고, 크면 남의 필지 사정이 딸려 온다.
PARCEL_ITEM_RADIUS_M = 100

#: 사업구역 폴리곤 상한. 국내 최대급 육상풍력 단지도 이 아래다.
#: 잘못 그린 구역이 수십 번의 타일 조회로 번지는 것을 입구에서 막는다.
MAX_AREA_KM2 = 500
#: 꼭짓점 상한 — 지나치게 잘게 그린 도형은 공간연산을 느리게만 만든다.
MAX_AREA_POINTS = 500


@api_view(['POST'])
def parcel_lookup(request):
    """
    클릭 좌표 → 그 좌표가 놓인 필지.

    POST { "lat": 34.5735, "lng": 126.5990 }

    필지가 없는 것과 조회하지 못한 것을 구분해 응답한다. 둘을 뭉뚱그리면
    화면이 '여기는 필지가 없다'고 말해 버리는데, 실제로는 인증키가
    빠졌거나 API가 죽은 것일 수 있다.
    """
    d = request.data or {}
    try:
        lat, lng = float(d.get('lat')), float(d.get('lng'))
    except (TypeError, ValueError):
        return Response({'detail': 'lat·lng 가 필요합니다.'},
                        status=http.HTTP_400_BAD_REQUEST)
    if not (33.0 <= lat <= 38.7 and 124.5 <= lng <= 132.0):
        return Response({'detail': '대한민국 영역 밖의 좌표입니다.'},
                        status=http.HTTP_400_BAD_REQUEST)

    try:
        p = parcels.lookup(lat, lng)
    except parcels.ParcelLookupError as e:
        return Response(
            {'detail': f'연속지적을 조회하지 못했습니다: {e}',
             'reason': 'FETCH'},
            status=http.HTTP_502_BAD_GATEWAY)

    if not p:
        return Response({'found': False, 'reason': 'NO_PARCEL',
                         'detail': '해당 좌표에 연속지적 필지가 없습니다. '
                                   '도로·하천 등 미등록 구역일 수 있습니다.'})
    return Response({'found': True, 'parcel': p})


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
    d = request.data or {}
    parsed = _parse_area_request(d)
    if isinstance(parsed, Response):
        return parsed
    ring, mode, permit_date, radii = parsed
    job_id = str(d.get('job_id') or '')[:64]
    jobs.clear(job_id)
    nrg = energy_mod.normalize(d.get('energy'))
    # 규제 62개 항목은 지점에서만 성립한다. 화면에도 보여줘야 하지만 지점이
    # 많으면 수 분이 걸리므로, 요청한 경우에만 함께 낸다.
    want_items = bool(d.get('with_items'))

    try:
        result = _compute_for(mode, ring, permit_date, job_id, nrg, radii)
    except parcels.ParcelLookupError as e:
        return Response({'detail': f'필지를 조회하지 못했습니다: {e}'},
                        status=http.HTTP_502_BAD_GATEWAY)
    except jobs.Cancelled:
        return _cancelled()
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

    evals = []
    if want_items:
        try:
            evals = _point_evals(result, ring, mode, _capacity(d), job_id, nrg)
        except jobs.Cancelled:
            return _cancelled()
        except Exception:                                       # noqa: BLE001
            logger.exception('항목 평가 실패')
    jobs.clear(job_id)
    return Response(_area_payload(result, ring, evals, _screen_area(result, mode, nrg)))


def _rings_sig(rings) -> tuple | None:
    """경계 링 목록의 지문 — (정점 수 합, 면적 합 m²). 비교용."""
    try:
        n = sum(len(r) for r in rings)
        a = 0.0
        for r in rings:
            if len(r) >= 4:
                g = geo.polygon_metric(r)
                if g is not None:
                    a += abs(float(g.area))
        return (n, round(a))
    except Exception:                                           # noqa: BLE001
        return None


def _screen_matches(d: dict, result: dict) -> bool:
    """
    화면이 보낸 사업구역 경계(site_rings)와 서버 재계산이 같은가.

    화면이 경계를 보내지 않았으면(구버전 클라이언트·풍력 등) 비교할 수
    없으므로 참으로 본다 — 가드는 어긋남이 **확인될 때만** 작동한다.
    """
    client = d.get('site_rings')
    if not client:
        # 화면이 경계를 보내지 않았다 = 세대를 검증할 수 없는 캡처다.
        # 4차 실측 — 검토 실행 시점과 보고서 생성 시점 사이에 경계 정의가
        # 바뀌면, 캡처에는 옛 경계가 픽셀로 박힌 채 문서에 실려 PART 1과
        # PART 2의 사업구역이 서로 다르게 나갔다. 검증 불가 캡처는 버리고
        # 서버 렌더로 통일한다 — 문서 안 정합이 캡처 보존보다 우선이다.
        logger.warning('화면이 사업구역 경계를 보내지 않았습니다(구버전 화면) '
                       '— 캡처를 버리고 서버 렌더로 통일합니다.')
        return False
    geoms = result.get('geoms') or {}
    server = geo.rings_4326(
        geoms.get('site_outline') if geoms.get('site_outline') is not None
        else geoms.get('area'))
    cs, ss = _rings_sig(client), _rings_sig(server)
    if cs is None or ss is None:
        return True
    if cs == ss:
        return True
    logger.warning('사업구역 경계 불일치 — 화면 %s vs 서버 %s', cs, ss)
    return False


def _screen_area(result: dict, mode: str, energy: str,
                 shapes: str = screening.SHAPES_CANDIDATES) -> dict | None:
    """
    구역 검토에 **필지별 채색**을 얹는다.

    제약도는 규제 레이어를 면적으로 칠하므로 필지 경계와 무관하다. '이 구역의
    30%가 조건부'는 알려 주지만 '이 필지가 되는가'는 답하지 못한다. 태양광은
    필지가 사업 단위라 그 답이 있어야 후보를 고를 수 있다.

    구역 검토에만 붙인다. 배치선·필지 모드는 대상이 이미 정해져 있어 나눌
    필요가 없다. 실패해도 구역 검토 결과를 버리지 않는다 — 채색이 빠질 뿐이다.

    shapes — 화면(evaluate-area)은 기본값(candidates)을 써서 배제·대상아님
    필지의 도형을 빼 응답을 가볍게 한다. 보고서 제약도는 그 배제 필지도
    색칠해야 하므로 area_report_download가 SHAPES_ALL로 다시 부른다.
    """
    if mode != 'area' or not energy_mod.profile(energy).has_screening:
        return None
    geoms = (result.get('geoms') or {})
    # 채색은 **그린 구역(drawn)** 기준이다. 정제된 사업구역(area)은 도로·
    # 구거 같은 '대상 아님' 필지를 이미 뺀 도형이라, 그걸로 채색하면 화면에서
    # 그 필지들이 통째로 사라져 '왜 저 필지는 무색인가'를 설명할 수 없다.
    # 판정·면적·지도 경계는 area(필지 기반)를 쓰고, 채색만 drawn으로 훑는다.
    area = geoms.get('drawn') or geoms.get('area')
    if area is None:
        return None
    try:
        # 경과규정 대상 여부를 함께 넘긴다. 넘기지 않으면 구역 검토는
        # 조건부(주황)로 칠하는데 필지만 배제(빨강)로 나와 어긋난다.
        return screening.screen_area(
            area, energy, shapes=shapes,
            grandfathered=bool(geoms.get('ordinance_grandfathered')))
    except screening.ScreenTooWide as e:
        return {'too_wide': True, 'detail': str(e)}
    except Exception:                                           # noqa: BLE001
        logger.exception('구역 내 필지 채색 실패')
        return None


#: 클라이언트가 요청을 접었을 때 쓰는 상태. 표준 코드가 없어 널리 쓰이는
#: 499(Client Closed Request)를 따른다. 오류가 아니므로 화면에 빨간 문구를
#: 띄우지 않도록 프런트에서 따로 다룬다.
HTTP_CLIENT_CLOSED = 499


def _cancelled():
    return Response({'detail': '사용자 요청으로 중단했습니다.', 'cancelled': True},
                    status=HTTP_CLIENT_CLOSED)


@api_view(['GET'])
def area_report_progress(request):
    """진행 상황 조회. 화면이 몇 초 간격으로 폴링한다."""
    job_id = str(request.GET.get('job_id') or '')[:64]
    p = jobs.get_progress(job_id)
    total = p.get('total') or 1
    done = p.get('done') or 0
    return Response({
        'job_id': job_id,
        'done': done,
        'total': total,
        'percent': round(min(100, done * 100 / total)),
        'stage': p.get('stage') or '',
        'running': bool(p),
    })


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


def _compute_for(mode: str, ring: list, permit_date, job_id: str, nrg: str,
                 radii: dict) -> dict:
    """
    검토 모드에 맞는 계산을 고른다.

    검토 실행과 보고서가 **같은 함수**를 부르게 해 둔다. 두 벌로 두면
    한쪽만 고쳐져 화면 숫자와 문서 숫자가 어긋난다.
    """
    if mode == 'parcel':
        # 클릭 좌표를 필지로 되돌린다. 화면이 보낸 도형을 믿지 않고
        # 원본(연속지적)에서 다시 받는다 — 판정 근거는 출처가 하나여야 한다.
        found, misses = parcels.resolve(ring)
        if not found:
            reasons = {m['reason'] for m in misses}
            if 'FETCH' in reasons:
                raise parcels.ParcelLookupError(
                    next(m['detail'] for m in misses if m['reason'] == 'FETCH'))
            raise ValueError('선택한 좌표에서 필지를 찾지 못했습니다.')
        result = available.compute_parcels(found, permit_date=permit_date,
                                           job_id=job_id, energy=nrg)
        # 빠진 좌표를 조용히 넘기지 않는다. 필지 하나가 통째로 빠진 채
        # 면적이 나오면 그 숫자로 사업 규모를 잡게 된다.
        result['parcel']['misses'] = misses
        return result
    if mode == 'layout':
        return available.compute_layout(ring, permit_date=permit_date,
                                        job_id=job_id, energy=nrg, **radii)
    return available.compute(ring, permit_date=permit_date,
                             job_id=job_id, energy=nrg)


def _capacity(d: dict):
    try:
        v = d.get('capacity_mw')
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


#: 캡처 이미지 상한 — 디코딩 후 바이트 기준. 화면 지도 캡처는 대개 1~3MB다.
#: 상한 없이 base64를 그대로 받으면 악의적 요청이 메모리를 먹일 수 있다.
MAX_MAP_IMAGE_BYTES = 12 * 1024 * 1024


#: 받아들일 이미지 시그니처. 위성영상 지도는 사진이라 JPEG가 PNG보다
#: 5~10배 작다 — 화면 캡처는 JPEG로 오고, 서버가 그린 지도는 PNG다.
_IMAGE_MAGIC = (b'\x89PNG', b'\xff\xd8\xff')


def _decode_image(raw: object, label: str) -> bytes | None:
    """data URL 또는 순수 base64 문자열 → PNG/JPEG bytes. 잘못됐으면 None(경고만)."""
    if not raw or not isinstance(raw, str):
        return None
    if ',' in raw[:64]:                     # 'data:image/jpeg;base64,....'
        raw = raw.split(',', 1)[1]
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        logger.warning('%s 캡처 이미지 디코딩 실패 — 서버 렌더링으로 대체합니다.', label)
        return None
    if not blob.startswith(_IMAGE_MAGIC) or len(blob) > MAX_MAP_IMAGE_BYTES:
        logger.warning('%s 캡처 이미지가 PNG/JPEG가 아니거나 너무 큽니다 — '
                       '서버 렌더링으로 대체합니다.', label)
        return None
    return blob


def _decode_map_image(d: dict) -> bytes | None:
    """
    화면(SitePicker) 지도를 캡처한 PNG — data URL 또는 순수 base64로 온다.

    보고서 제약도를 서버에서 다시 그리면 정부 원본 데이터의 단순화 방식
    차이로 화면과 미세하게 달라 보일 수 있다(좁은 물길 근처 등). 화면을
    그대로 캡처해 쓰면 그 문제 자체가 성립하지 않는다.
    """
    return _decode_image(d.get('map_image'), '제약도')


#: 항목별 캡처 상한 — env_layers()의 ENV_MAP_MAX(+용도지역 1장)와 맞춘다.
#: 이보다 많이 오면 조작된 요청으로 보고 자른다.
MAX_ENV_IMAGES = available.ENV_MAP_MAX + 1


def _decode_env_images(d: dict) -> dict[str, bytes]:
    """
    화면에서 항목별로 캡처한 지도들 — {항목명: PNG}.

    화면의 "지도 보기" 선택지 이름과 `available.env_layers()`가 주는 이름이
    같아야 짝이 맞는다(용도지역은 'zoning' 고정 키를 쓴다).
    """
    raw = d.get('env_images')
    if not isinstance(raw, dict):
        return {}
    out: dict[str, bytes] = {}
    for name, val in list(raw.items())[:MAX_ENV_IMAGES]:
        blob = _decode_image(val, f'환경성 항목({name})')
        if blob:
            out[str(name)] = blob
    return out


def _parse_area_request(d: dict):
    """
    구역/배치선 요청 본문을 검증한다.
    → (ring, is_layout, permit_date, radii) 또는 오류 Response.

    검토 실행과 보고서 생성이 같은 입력을 받으므로 파싱을 한 곳에 둔다.
    두 벌로 두면 한쪽만 고쳐져 화면 값과 문서 값이 어긋난다.
    """
    turbines_raw = d.get('turbines')
    parcels_raw = d.get('parcels')
    if isinstance(parcels_raw, list) and parcels_raw:
        mode, raw, label, min_n = 'parcel', parcels_raw, '필지', 1
    elif isinstance(turbines_raw, list) and turbines_raw:
        mode, raw, label, min_n = 'layout', turbines_raw, '발전기 위치', 1
    else:
        mode, raw, label, min_n = 'area', (d.get('ring') or []), '꼭짓점', 3
    is_layout = mode == 'layout'

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
    return ring, mode, permit_date, radii


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
    ring, mode, permit_date, radii = parsed
    # 클라이언트가 만든 작업 id. 취소 요청과 실행 중인 작업을 잇는 유일한 끈이다.
    job_id = str(d.get('job_id') or '')[:64]
    jobs.clear(job_id)      # 같은 id의 지난 취소 플래그가 남아 있으면 즉시 죽는다
    nrg = energy_mod.normalize(d.get('energy'))
    project = _project_name(d)

    try:
        result = _compute_for(mode, ring, permit_date, job_id, nrg, radii)
    except parcels.ParcelLookupError as e:
        return Response({'detail': f'필지를 조회하지 못했습니다: {e}'},
                        status=http.HTTP_502_BAD_GATEWAY)
    except jobs.Cancelled:
        return _cancelled()
    except ValueError as e:
        return Response({'detail': str(e)}, status=http.HTTP_400_BAD_REQUEST)
    except jurisdiction.BoundaryUnavailable as e:
        return Response({'detail': f'행정경계를 조회하지 못했습니다: {e}'},
                        status=http.HTTP_502_BAD_GATEWAY)

    # 규제 62개 항목·풍황·계통은 지점에서만 성립하므로 호기마다 따로 돌린다.
    # 면적 분포만으로는 '어느 호기가 무엇에 걸리는지'를 알 수 없어 배치를 고칠 수 없다.
    capacity = _capacity(request.data or {})
    evals = []
    try:
        evals = _point_evals(result, ring, mode, capacity, job_id, nrg)
    except jobs.Cancelled:
        return _cancelled()
    except Exception:                                           # noqa: BLE001
        # 항목 평가가 실패해도 면적 보고서는 나와야 한다. 빠졌다는 사실은
        # 보고서 '한계'에 남는다.
        logger.exception('호기별 항목 평가 실패')

    try:
        jobs.check(job_id)
        jobs.set_progress(job_id, len(evals) or 1, (len(evals) or 1) + 1, '보고서 작성')
        # 보고서의 '해당 필지' 열이 이 값을 쓴다. 화면 응답과 같은 계산을
        # 다시 도는 셈이지만, 캐시가 살아 있어 수 초다.
        # 제약도(필지별)는 배제 필지도 화면과 같은 도형으로 칠해야 하므로
        # 화면용 응답과 달리 SHAPES_ALL로 도형을 전부 받는다.
        result['screening'] = _screen_area(result, mode, nrg,
                                           shapes=screening.SHAPES_ALL)
        # 풍력 종합판정의 '예상 설비용량' 카드가 쓴다. 태양광은 면적으로
        # 어림하지만 풍력은 호기 수 × 대당 용량이라 이 값이 필요하다.
        result['capacity_mw_per_turbine'] = capacity
        map_image = _decode_map_image(request.data or {})
        env_images = _decode_env_images(request.data or {})
        # ── 화면-서버 도형 정합 가드 ──────────────────────────────────
        # 캡처(map_image·env_images)에는 화면이 검토 실행 때 받은 사업구역
        # 경계가 픽셀로 박혀 있다. 그 사이 코드나 원천 데이터가 바뀌어
        # 서버 재계산 도형이 달라지면, 같은 문서 안에서 캡처 지도와 서버
        # 렌더 지도의 사업구역이 서로 다르게 나간다(2026-08 실측 — 코드
        # 수정 직후 받은 보고서에서 PART 1 캡처와 ③·④ 서버 지도가 어긋남).
        # 화면이 보낸 경계와 서버 재계산이 다르면 **캡처를 버리고 전부
        # 서버 렌더로 통일**한다. 문서 안 불일치보다 캡처 포기가 낫다.
        if map_image and not _screen_matches(d, result):
            logger.warning('화면 사업구역과 서버 재계산이 다릅니다 — '
                           '캡처를 버리고 모든 지도를 서버 렌더로 냅니다. '
                           '검토를 다시 실행하면 화면과 문서가 다시 맞습니다.')
            map_image = None
            env_images = {}
        blob = area_report.build_area_report(result, evals, energy=nrg,
                                             map_image=map_image,
                                             env_images=env_images,
                                             project_name=project)
    except jobs.Cancelled:
        return _cancelled()
    except Exception as e:                                      # noqa: BLE001
        logger.exception('구역 보고서 생성 실패')
        return Response({'detail': f'보고서 생성에 실패했습니다: {type(e).__name__}'},
                        status=http.HTTP_500_INTERNAL_SERVER_ERROR)

    _save_history(result, evals, request)
    jobs.clear(job_id)
    res = HttpResponse(
        blob,
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    name = _report_filename(nrg, project)
    res['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(name)}"
    return res


def _save_history(result: dict, evals: list, request) -> None:
    """
    검토 이력 저장. 실패해도 보고서 반환을 막지 않는다.

    종전에는 지점 검토를 실행할 때마다 남겼는데, 화면을 오가며 수십 건이
    쌓여도 쓰이지 않았다. 보고서를 뽑은 시점만 남기면 '실제로 문서로 나간
    검토'가 이력이 되어 감사 추적으로 쓸모가 있다.
    """
    ok = [e for e in evals if e.get('result')]
    if not ok:
        return
    first, best = ok[0], min(ok, key=lambda e: e['result'].overall_feasibility.score)
    layout = result.get('layout') or {}
    try:
        SiteEvaluation.objects.create(
            energy_type=energy_mod.normalize(result.get('energy_type')),
            address=first.get('address') or '',
            lat=first['lat'], lng=first['lng'],
            radius_m=layout.get('turbine_radius_m') or 0,
            # 필지 검토면 필지 수를, 배치선이면 호기 수를 요약에 남긴다.
            sido=first.get('sido', ''), sigungu=first.get('sigungu', ''),
            score=best['result'].overall_feasibility.score,
            grade=best['result'].overall_feasibility.grade,
            summary=f"지점 {len(ok)}곳 · 검토면적 {result['total_area_m2'] / 1e4:,.1f}ha",
            result_json={'total_area_m2': result['total_area_m2'],
                         'free_m2': result['free_m2'],
                         'points': [{'no': e['no'], 'address': e['address'],
                                     'score': e['result'].overall_feasibility.score}
                                    for e in ok]},
            created_by=(request.user
                        if getattr(request.user, 'is_authenticated', False) else None),
        )
    except Exception:                                           # noqa: BLE001
        logger.exception('검토 이력 저장 실패')


def _point_evals(result: dict, ring: list, mode: str, capacity, job_id: str,
                 energy: str = energy_mod.DEFAULT):
    # 매장유산 확인은 **사업가능 면적**(제약없음 + 조건부) 기준이다.
    # 배제 면적은 애초에 사업 대상이 아니라 분모에서 뺀다.
    usable = float(result.get('available_with_consultation_m2') or 0)
    """
    지점별 62개 항목 평가.

      배치선 → 호기마다     필지 → 필지마다     폴리곤 → 구역 대표점 한 곳

    필지를 필지마다 도는 이유는 호기와 같다. 합쳐서 한 번만 보면 '어느
    필지가 무엇에 걸리는지'를 알 수 없어, 뺄 필지를 고를 수 없다.

    검토 실행과 보고서가 같은 값을 써야 화면과 문서가 어긋나지 않는다.
    """
    parcel = result.get('parcel')
    if mode == 'parcel' and parcel:
        pts = [(p['lat'], p['lng']) for p in parcel['parcels']]
        # 필지마다 **그 필지 경계**를 검토 도형으로 넘긴다. 반경 원으로 재면
        # 경사도·면적 판정에 옆 필지 지형이 섞인다.
        rings = [(r[0] if r else None) for r in (result.get('_parcel_rings') or [])]
        return available.evaluate_points(
            pts, radius_m=PARCEL_ITEM_RADIUS_M, capacity_mw=capacity,
            label='필지', job_id=job_id, energy=energy, rings=rings,
            usable_m2=usable)

    layout = result.get('layout')
    if layout:
        pts = [(a, o) for a, o in layout['turbines']]
        return available.evaluate_points(
            pts, radius_m=layout['turbine_radius_m'],
            capacity_mw=capacity, job_id=job_id, energy=energy,
            usable_m2=usable)
    # 폴리곤 검토에는 호기가 없다. 구역 대표점 한 곳에서 항목 평가를 내되,
    # **사업구역 도형을 함께 넘긴다**(SiteQuery.area_ring).
    #
    # ⚠️ 종전에는 중심점 + 반경 100m만 넘겼다. 216ha 구역을 지름 200m 원
    #    하나로 대표시킨 셈이라, 구역 안에 있어도 중심에서 100m 밖이면 통째로
    #    못 봤다. 장흥 사례에서 생태자연도 1등급 43.5ha가 실재하는데도
    #    '조회되지 않았습니다'로 나온 것이 그 때문이다(실측 확인).
    # 판정 도형은 **정제된 사업구역**(필지 기반)이다. 그린 폴리곤(ring)을
    # 그대로 주면 구역 안 도로·구거·하천 위 규제까지 '사업구역 내 저촉'으로
    # 잡힌다 — 장흥에서 물길 위 생태자연도 2등급이 그렇게 오탐됐다.
    # 지도·면적·판정이 전부 geoms['area'] 한 도형을 본다.
    site = result['geoms']['area']
    c = site.centroid
    lng, lat = geo.to_geographic_xy(c.x, c.y)
    return available.evaluate_points(
        [(lat, lng)], radius_m=100, capacity_mw=capacity,
        label='지점', job_id=job_id, energy=energy, geoms=[site],
        usable_m2=usable)


def _env_layers_payload(r: dict) -> list[dict]:
    """
    환경성 항목 지도 미리보기용 — `available.env_layers()`의 shapely 도형을
    화면에 겹쳐 그릴 수 있게 위경도 링으로 바꾼다.

    화면의 "지도 보기" 선택지와 보고서 캡처가 **같은 목록·같은 도형**을
    쓴다 — 화면에서 고른 항목이 그대로 보고서에 실려야 한다.
    """
    return [
        {'kind': e['kind'], 'name': e['name'], 'status': e['status'],
         'area_m2': round(e['area_m2'], 1), 'ha': round(e['area_m2'] / 10_000, 2),
         'rings': geo.rings_4326(e['geom'])}
        for e in available.env_layers(r)
    ]


def _area_payload(r: dict, ring: list, evals: list | None = None,
                  screen: dict | None = None) -> dict:
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
        # 필지 검토일 때만 채워진다 (필지 목록·지목 구성·못 찾은 좌표).
        'parcel': r.get('parcel'),
        # 조례 경과규정 검토 — 시스템은 면제를 판정하지 않고 근거만 제시한다.
        'grandfathering': r.get('grandfathering'),
        'jurisdictions': r['jurisdictions'],
        'jurisdiction_meta': r['jurisdiction_meta'],
        'fetch_failures': r['fetch_failures'],
        'notes': r['notes'],
        # 화면에 겹쳐 그릴 수 있도록 위경도 링으로 돌려준다.
        # ordinance_house는 blocked에 이미 녹아 있지만 따로도 보낸다 —
        # 붉은 면만 보면 규제 레이어 때문인지 조례 이격 때문인지 알 수 없고,
        # 이 둘은 다음 행동이 다르다(부지 변경 / 이격 확보).
        'overlays': {k: geo.rings_4326(geoms.get(k))
                     for k in ('blocked', 'conditional', 'free',
                               'ordinance_house', 'ordinance_road',
                               'ordinance_road_uncertain')},
        # **사업구역 경계**(필지 기반) — 그린 폴리곤(ring)과 다르다.
        # 그린 구역 안 도로·구거 같은 '대상 아님' 필지를 뺀, 판정·면적·
        # 보고서 지도가 실제로 쓰는 도형이다. 화면이 이 경계를 그려야
        # 캡처와 서버 렌더 지도의 사업구역이 같은 도형이 된다.
        'site_rings': geo.rings_4326(geoms.get('site_outline')
                                     if geoms.get('site_outline') is not None
                                     else geoms.get('area')),
        # 그린 구역에서 무엇이 얼마나 빠졌는가 — 화면 안내용.
        'site_refine': r.get('site_refine') or {},
        # 조례 이격을 배제로 셌는지 조건부로 셌는지. 화면 범례가 이 값에
        # 따라 '사업 불가'와 '경과규정 검토 대상'을 가른다.
        'ordinance_grandfathered': bool(geoms.get('ordinance_grandfathered')),
        # 겹침 면에 커서를 올렸을 때 보여 줄 문구. {overlay 열쇠: 한 줄}
        'overlay_labels': geoms.get('overlay_labels') or {},
        # 도로별 선·이격 범위 — 어느 도로로부터 얼마만큼 침범되는지 지도에
        # 그린다. 합친 붉은 면 하나로는 그 답이 나오지 않는다.
        'road_detail': geoms.get('road_detail') or [],
        # 환경성 평가 항목별 지도(용도지역 구성·농업진흥지역도 등) 미리보기.
        # 화면에서 "지도 보기"로 고른 항목을 그대로 보고서 캡처에도 쓴다.
        'env_layers': _env_layers_payload(r),
        # 구역 안 필지별 채색 — 태양광 구역 검토에서만 채워진다.
        'screening': screen,
        # 규제 62개 항목 — with_items로 요청했을 때만 채워진다.
        'items': _items_payload(evals or []),
        # 발전시간·이용률 — 항목 안에 묻혀 있으면 후보 비교에서 꺼내 쓸 수
        # 없다. 사업 판단에 직접 쓰이는 값이라 상위로 올린다.
        'yield': _yield_payload(evals or []),
        'evaluated_at': datetime.now().isoformat(timespec='seconds'),
    }


def _yield_payload(evals: list) -> dict | None:
    """
    일사량 항목에서 발전시간·이용률만 꺼낸다.

    항목 목록 안에 묻어 두면 후보를 저장·비교할 때 문구를 파싱해야 하는데,
    그건 문구를 고칠 때마다 조용히 깨진다. 값 자체를 따로 올린다.
    """
    from .providers.solar_resource import SolarResourceProvider

    for e in evals:
        res = e.get('result')
        if not res:
            continue
        for it in res.analysis_items:
            if it.item_name == SolarResourceProvider.item_name:
                y = (it.raw or {}).get('yield')
                if y:
                    return y
    return None


def _items_payload(evals: list) -> dict | None:
    """
    호기별 결과를 화면이 쓸 형태로 — 항목별 최악값 + 지점별 요약.

    없을 때는 빈 dict가 아니라 None을 준다. 빈 dict는 JSON에서 {}가 되고
    자바스크립트에서 참으로 취급돼, '항목이 있다'고 판단한 화면이 없는
    필드를 읽다 통째로 죽는다(빈 화면). 없음은 null로 말해야 한다.
    """
    ok = [e for e in evals if e.get('result')]
    if not ok:
        return None
    merged = available.merge_items(ok)
    return {
        'merged': merged,
        'points': [{
            'no': e['no'], 'address': e['address'],
            'lat': e['lat'], 'lng': e['lng'],
            'grade': e['result'].overall_feasibility.grade,
            'score': e['result'].overall_feasibility.score,
            'summary': e['result'].overall_feasibility.summary,
        } for e in ok],
    }


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
        # 검색한 자리가 **어느 읍·면에 속하는지**를 면으로 함께 준다.
        # 점만 찍어 주면 부지가 행정구역 어디에 걸치는지 알 수 없어, 경계를
        # 넘긴 자리를 사업지로 잡아도 눈치채지 못한다. 실패해도 검색 자체는
        # 살린다 — 지도 보조 정보라 없으면 경계만 안 그려질 뿐이다.
        return Response({
            'lat': g['lat'], 'lng': g['lng'], 'matched': g['matched'],
            'sido': rg.get('sido', ''), 'sigungu': rg.get('sigungu', ''),
            'boundary': jurisdiction.emd_at(g['lat'], g['lng']),
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
    """인허가 로드맵 조회 (?capacity_mw=60&flags=FOREST,MILITARY&energy=SOLAR)"""
    cap = request.query_params.get('capacity_mw')
    try:
        cap = float(cap) if cap else None
    except ValueError:
        cap = None
    flags = {f.strip() for f in (request.query_params.get('flags') or '').split(',') if f.strip()}
    nrg = energy_mod.normalize(request.query_params.get('energy'))
    steps = build_roadmap(capacity_mw=cap, site_flags=flags or None, energy=nrg)
    return Response({'count': len(steps), 'energy_type': nrg,
                     'results': [s.to_dict() for s in steps]})


@api_view(['GET'])
def law_list(request):
    """관련 법령 목록 (?energy=SOLAR)"""
    nrg = energy_mod.normalize(request.query_params.get('energy'))
    rows = collect_laws(nrg)
    return Response({'count': len(rows), 'energy_type': nrg, 'results': rows})


@api_view(['GET'])
def ordinance_list(request):
    """지자체 이격거리 조례 (?sigungu=청도군&energy=SOLAR)"""
    qs = LocalOrdinance.objects.all()
    if e := request.query_params.get('energy'):
        qs = qs.filter(energy_type__in=[energy_mod.normalize(e), 'ALL'])
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


@api_view(['POST'])
def screen_parcels(request):
    """
    화면 범위 필지 스크리닝 — 4등급 채색 (기획서 R-06·R-07)

    POST { "bounds": [남, 서, 북, 동], "energy": "SOLAR" }

    ⚠️ 이 응답은 **예비 스크리닝**이다. 확정 검토는 필지를 눌러 돌리는
       정밀판정(evaluate-area)이며, 화면이 그 차이를 숨기지 않아야 한다.
    """
    d = request.data or {}
    try:
        b = [float(v) for v in (d.get('bounds') or [])]
        if len(b) != 4:
            raise ValueError
    except (TypeError, ValueError):
        return Response({'detail': 'bounds 는 [남, 서, 북, 동] 형식이어야 합니다.'},
                        status=http.HTTP_400_BAD_REQUEST)

    south, west, north, east = b
    if not (33.0 <= south < north <= 38.7 and 124.5 <= west < east <= 132.0):
        return Response({'detail': '대한민국 영역을 벗어난 화면 범위입니다.'},
                        status=http.HTTP_400_BAD_REQUEST)

    nrg = energy_mod.normalize(d.get('energy'))
    try:
        min_area = int(d.get('min_area_m2') or screening.DEFAULT_MIN_AREA_M2)
    except (TypeError, ValueError):
        min_area = screening.DEFAULT_MIN_AREA_M2
    shapes = (screening.SHAPES_ALL if d.get('shapes') == screening.SHAPES_ALL
              else screening.SHAPES_CANDIDATES)
    try:
        return Response(screening.screen((south, west, north, east), nrg,
                                         max(0, min(min_area, 100_000)), shapes))
    except screening.ScreenTooWide as e:
        # 오류가 아니다. 더 확대하면 되는 상태이므로 화면이 그렇게 안내한다.
        return Response({'too_wide': True, 'detail': str(e)})
    except ValueError as e:
        return Response({'detail': str(e)}, status=http.HTTP_400_BAD_REQUEST)
    except Exception as e:                                      # noqa: BLE001
        logger.exception('필지 스크리닝 실패')
        return Response({'detail': f'스크리닝에 실패했습니다: {type(e).__name__}'},
                        status=http.HTTP_500_INTERNAL_SERVER_ERROR)
