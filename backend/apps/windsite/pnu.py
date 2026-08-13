"""
필지고유번호(PNU) 코드 체계 불일치 처리
---------------------------------------------------------------
V-World의 **지적·지오코딩**과 **국가공간정보 연계(NED)** 는 같은 필지를
서로 다른 법정동코드로 부른다. 행정구역 개편이 반영된 시점이 다르기
때문이다. 실측(2026-08, 완도군 군외면 당인리 산106-1):

    지적 lp_pa_cbnd_bubun  → 1285031028201060001   (전남광주통합특별시)
    NED  getPossessionAttr → 1285031028201060001   0건
                             4689031028201060001   정상 응답 (전라남도 완도군)

앞 5자리(시도 2 + 시군구 3)만 다르고 뒤 14자리는 같다. 그래서 지적에서
받은 PNU를 그대로 NED에 넘기면 **소유구분·지역지구·산지구분·토지특성
네 항목이 한꺼번에 '확인 필요'로 떨어진다.**

■ 앞 5자리를 추측하지 않는 이유

뒤 14자리를 고정하고 앞 5자리를 훑으면 응답이 오는 조합이 여럿 나온다.
위 필지의 경우 46890(완도군)뿐 아니라 46770(고흥군)에서도 응답이 왔다 —
그 시군구에도 같은 읍면동 코드에 산106-1이 있기 때문이다. 응답이 왔다는
사실은 **맞는 필지라는 증거가 아니다.** 잘못 고르면 남의 땅 소유구분을
이 사업지 것으로 싣게 되므로, 확인된 대응표가 없으면 조회하지 않는다.

■ 그래서 이 모듈이 하는 일

  1) 운영자가 채워 넣은 대응표가 있으면 그대로 바꿔 준다
  2) 없으면 **왜 조회가 안 되는지**를 항목 사유로 정확히 돌려준다
     ('조회하지 못했습니다'로 뭉뚱그리면 재시도하면 될 일인 줄 안다)

대응표는 행정안전부 법정동코드 고시로 확정되면 아래 파일에 적으면 된다.
코드 수정 없이 다음 검토부터 반영된다.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

#: 운영자가 채우는 대응표. {신 시군구코드(5): 구 시군구코드(5)}
#: 예) {"12850": "46890"}  ← 전남광주통합특별시 완도군 = 전라남도 완도군
ALIAS_FILE = 'pnu_legacy_sigungu.json'

#: NED에 아직 적재되지 않은 것으로 확인된 시도 코드와 그 사연.
#: 여기에 걸리면 '조회 실패'가 아니라 '코드 체계 불일치'라고 말한다.
UNMAPPED_SIDO = {
    '12': '전남광주통합특별시(행정구역 개편)',
}


def _alias() -> dict[str, str]:
    override = getattr(settings, 'PNU_LEGACY_SIGUNGU', None)
    if isinstance(override, dict) and override:
        return {str(k): str(v) for k, v in override.items()}
    path = Path(settings.BASE_DIR) / 'data' / ALIAS_FILE
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding='utf-8'))
            return {str(k): str(v) for k, v in raw.items()
                    if str(k).isdigit() and str(v).isdigit()}
    except Exception as e:                                      # noqa: BLE001
        logger.warning('PNU 대응표를 읽지 못했습니다(%s): %s', path, e)
    return {}


#: 구 시도 코드 ↔ 시도명. 후보를 만드는 데만 쓴다 — 어느 후보가 맞는지는
#: 응답에 실린 소재지 주소로 대조해 확정한다.
LEGACY_SIDO = {
    '11': '서울', '26': '부산', '27': '대구', '28': '인천', '29': '광주',
    '30': '대전', '31': '울산', '36': '세종', '41': '경기', '42': '강원',
    '43': '충북', '44': '충남', '45': '전북', '46': '전남', '47': '경북',
    '48': '경남', '50': '제주', '51': '강원', '52': '전북',
}


def candidates(sido_name: str) -> list[str]:
    """
    구 시군구 코드 후보. 시도명에 이름이 걸리는 시도를 앞에 둔다.

    '전남광주통합특별시'처럼 통합된 이름은 옛 시도 여럿에 걸리므로 모두
    앞으로 당긴다. 시군구 3자리는 5의 배수로만 훑는다(법정동코드 관행).
    """
    name = sido_name or ''
    head = [c for c, n in LEGACY_SIDO.items() if n and n in name]
    rest = [c for c in LEGACY_SIDO if c not in head]
    return [f'{c}{n:03d}' for c in head + rest for n in range(100, 1000, 5)]


def save_alias(new_code: str, old_code: str) -> None:
    """확인된 대응을 파일에 적는다. 다음 검토부터 코드 수정 없이 쓰인다."""
    path = Path(settings.BASE_DIR) / 'data' / ALIAS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    cur = {}
    if path.exists():
        try:
            cur = json.loads(path.read_text(encoding='utf-8'))
        except Exception:                                       # noqa: BLE001
            cur = {}
    cur[str(new_code)] = str(old_code)
    path.write_text(json.dumps(cur, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding='utf-8')
    logger.info('PNU 대응표 갱신: %s → %s', new_code, old_code)


def for_ledger(sigungu_code: str) -> str:
    """건축물대장 sigunguCd. 대응표가 있으면 구 코드로."""
    return _alias().get(str(sigungu_code), str(sigungu_code))


def for_ned(pnu: str) -> str:
    """NED에 넘길 PNU. 대응표에 있으면 구 코드로 바꾸고, 없으면 그대로."""
    p = (pnu or '').strip()
    if len(p) < 5:
        return p
    old = _alias().get(p[:5])
    return (old + p[5:]) if old else p


def unmapped_reason(pnu: str) -> str:
    """
    조회가 0건일 때 붙일 사유. 코드 체계 문제가 아니면 빈 문자열.

    빈 문자열이면 호출부가 기존 문구(자료 없음)를 그대로 쓴다 — 원인을
    모르는데 아는 척하지 않기 위해서다.
    """
    p = (pnu or '').strip()
    if len(p) < 5 or p[:5] in _alias():
        return ''
    why = UNMAPPED_SIDO.get(p[:2])
    if not why:
        return ''
    return (f'{why}으로 지적 자료의 필지고유번호({p[:5]}…)와 국가공간정보 '
            f'연계(NED)에 적재된 법정동코드가 서로 달라 조회되지 않았습니다. '
            f'자료가 없는 것이 아니라 코드 체계가 어긋난 것이며, 재검토해도 '
            f'같습니다.')


def unmapped_action(default: str) -> str:
    """코드 불일치일 때의 조치 — 재시도가 아니라 사람이 직접 확인해야 한다."""
    return ('토지이음(eum.go.kr)에서 토지이용계획확인원을, 정부24에서 '
            '토지(임야)대장을 지번으로 직접 열람하십시오. ' + default)
