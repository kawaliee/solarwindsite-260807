"""
규제 레이어 정의 시드
---------------------------------------------------------------
레이어 ID·속성명을 코드에서 DB(RegulationLayer)로 옮긴다.

기술 정보(레이어 ID·명칭 속성·지오메트리 타입)는 **추측하지 않고**
`scripts/vworld_probe.py`가 남긴 서버 실측 결과에서 읽는다.
  근거 파일: backend/data/vworld/probe_result.json
  근거 문서: docs/WINDSITE_VWORLD_LAYERS.md

법적 판정 메타(근거 법령·조문·기본 판정)는 아래 SPECS에 두며,
`confidence`는 **법령 원문 검증 수준**을 뜻한다 (레이어 실측 여부와 별개).

사용: docker compose exec backend python manage.py seed_windsite_layers
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.windsite.models import RegulationLayer, RegulationRule

PROBE_PATH = Path(__file__).resolve().parents[4] / 'data' / 'vworld' / 'probe_result.json'

#: 명칭 속성 후보 — 실측 결과와 대조해 **실제 존재하는 것만** 고른다.
#:   1순위: GetFeature 표본 응답의 property_keys (값까지 확인된 것)
#:   2순위: DescribeFeatureType이 선언한 속성 (서버 스키마 — 표본 지점에
#:          피처가 없었을 뿐 속성 자체는 서버가 보증)
#: 어느 쪽에도 없으면 비워 두고 판정을 보류한다.
NAME_FIELD_CANDIDATES = (
    'uname', 'dgm_nm', 'park_name', 'mpa_nam', 'name',
    # 항공·군사 계열은 lbl_4(한글 구역종류) → lbl_1(구역기호) 순으로 쓸모가 있다
    'prh_lbl_4', 'res_lbl_1', 'ctr_lbl_1', 'atm_lbl_1', 'moa_lbl_1',
    'cat_lbl_1', 'acm_lbl_1', 'dng_lbl_1',
    'riv_nm', 'rn', 'buld_nm',
)

# ----------------------------------------------------------------------
# 레이어별 법적 판정 메타
#   default_status/difficulty = "이 레이어 피처가 검출됐을 때의 기본 판정"
#   proximity_m = 교차하지 않아도 이 거리 이내면 '근접'으로 보고 (0이면 교차만)
#   search_margin_m = 레이어 고유 보호범위를 조회 반경에 가산
#   confidence = 근거 법령·조문의 **원문 검증 수준** (LOW = 미대조)
# ----------------------------------------------------------------------
SPECS: list[dict] = [
    # ---------------- 용도지역 (국토계획법 제36조) ----------------
    dict(code='용도지역_도시', layer_id='lt_c_uq111', role='CONTEXT',
         category='규제/법령', default_status='POSSIBLE', default_difficulty='LOW',
         law='국토의 계획 및 이용에 관한 법률', article='제36조(용도지역의 지정) · 제76조(건축물의 건축 제한)',
         action_required='해당 용도지역의 건축제한과 지자체 도시·군계획조례를 확인하십시오.',
         display_order=11),
    dict(code='용도지역_관리', layer_id='lt_c_uq112', role='CONTEXT',
         category='규제/법령', default_status='POSSIBLE', default_difficulty='LOW',
         law='국토의 계획 및 이용에 관한 법률', article='제36조 · 제76조',
         action_required='보전관리·생산관리·계획관리 세부구분에 따라 허용행위가 달라집니다. 세부구분을 확인하십시오.',
         display_order=12),
    dict(code='용도지역_농림', layer_id='lt_c_uq113', role='REGULATION',
         category='규제/법령', default_status='CONDITIONAL', default_difficulty='MEDIUM',
         law='국토의 계획 및 이용에 관한 법률', article='제76조 · 시행령 제71조',
         action_required='농지법·산지관리법상 행위제한과 전용 절차를 병행 검토하십시오.',
         display_order=13),
    dict(code='용도지역_자연환경보전', layer_id='lt_c_uq114', role='REGULATION',
         category='규제/법령', default_status='CONDITIONAL', default_difficulty='CRITICAL',
         law='국토의 계획 및 이용에 관한 법률', article='제76조 · 시행령 제71조 [별표22]',
         action_required='자연환경보전지역은 허용행위가 극히 제한적입니다. 입지 변경을 우선 검토하십시오.',
         display_order=14),

    # ---------------- 용도지구·구역 ----------------
    dict(code='경관지구', layer_id='lt_c_uq121', role='REGULATION',
         category='규제/법령', default_status='CONDITIONAL', default_difficulty='HIGH',
         law='국토의 계획 및 이용에 관한 법률', article='제37조 · 제76조',
         action_required='경관지구는 높이·형태 제한이 부과됩니다. 지자체 조례의 구체적 제한을 확인하십시오.',
         display_order=21),
    dict(code='고도지구', layer_id='lt_c_uq123', role='REGULATION',
         category='규제/법령', default_status='CONDITIONAL', default_difficulty='CRITICAL',
         law='국토의 계획 및 이용에 관한 법률', article='제37조제1항제4호',
         action_required='고도지구의 높이 상한은 풍력발전기(통상 100m 이상)와 직접 충돌합니다. 상한값을 반드시 확인하십시오.',
         display_order=22),
    dict(code='방화지구', layer_id='lt_c_uq124', role='CONTEXT',
         category='규제/법령', default_status='CONDITIONAL', default_difficulty='LOW',
         law='국토의 계획 및 이용에 관한 법률', article='제37조',
         display_order=23),
    dict(code='방재지구', layer_id='lt_c_uq125', role='REGULATION',
         category='규제/법령', default_status='CONDITIONAL', default_difficulty='MEDIUM',
         law='국토의 계획 및 이용에 관한 법률', article='제37조',
         action_required='재해저감 대책 수립이 요구됩니다.', display_order=24),
    dict(code='보호지구', layer_id='lt_c_uq126', role='REGULATION',
         category='규제/법령', default_status='CONDITIONAL', default_difficulty='HIGH',
         law='국토의 계획 및 이용에 관한 법률', article='제37조',
         action_required='보호지구 유형(역사문화환경·중요시설물·생태계)에 따른 소관기관 협의가 필요합니다.',
         display_order=25),
    dict(code='취락지구', layer_id='lt_c_uq128', role='DISTANCE',
         category='규제/법령', default_status='CONDITIONAL', default_difficulty='MEDIUM',
         proximity_m=2000,
         law='국토의 계획 및 이용에 관한 법률', article='제37조',
         action_required='취락지구는 주거밀집지역에 해당할 수 있어 지자체 이격거리 조례의 기산점이 됩니다.',
         display_order=26),
    dict(code='개발진흥지구', layer_id='lt_c_uq129', role='CONTEXT',
         category='규제/법령', default_status='POSSIBLE', default_difficulty='LOW',
         law='국토의 계획 및 이용에 관한 법률', article='제37조', display_order=27),
    dict(code='특정용도제한지구', layer_id='lt_c_uq130', role='REGULATION',
         category='규제/법령', default_status='CONDITIONAL', default_difficulty='HIGH',
         law='국토의 계획 및 이용에 관한 법률', article='제37조제1항제8호',
         action_required='제한되는 용도에 발전시설이 포함되는지 지자체 조례로 확인하십시오.',
         display_order=28),
    # ⚠️ 실측 확인: lt_c_uq162는 제목이 '도시자연공원구역'이지만 실제 uname 값에는
    #    근린공원·어린이공원·공공공지·교통광장 등 **도시계획시설**이 함께 들어온다.
    #    따라서 검출만으로 '도시자연공원구역 저촉'으로 단정하면 오탐이 된다.
    #    기본 난이도를 낮추고, 실제 구역명을 그대로 제시해 사용자가 구분하게 한다.
    dict(code='도시자연공원구역', layer_id='lt_c_uq162', role='REGULATION',
         category='규제/법령', default_status='CONDITIONAL', default_difficulty='MEDIUM',
         law='도시공원 및 녹지 등에 관한 법률', article='제27조(행위제한)',
         action_required='조회된 구역명을 확인하십시오. 실제 "도시자연공원구역"이면 '
                         '행위제한이 강해 입지 변경 검토가 필요하고, 근린공원·어린이공원 등 '
                         '도시계획시설이면 해당 시설 결정 내용을 별도로 확인해야 합니다.',
         display_order=29),
    dict(code='개발제한구역', layer_id='lt_c_ud801', role='REGULATION',
         category='규제/법령', default_status='CONDITIONAL', default_difficulty='CRITICAL',
         law='개발제한구역의 지정 및 관리에 관한 특별조치법', article='제12조(행위제한)',
         action_required='개발제한구역 내 행위는 원칙적으로 금지되며 예외 허가 대상 여부를 확인해야 합니다.',
         display_order=30),
    dict(code='개발행위허가제한지역', layer_id='lt_c_upisuq171', role='REGULATION',
         category='규제/법령', default_status='CONDITIONAL', default_difficulty='HIGH',
         law='국토의 계획 및 이용에 관한 법률', article='제63조(개발행위허가의 제한)',
         action_required='제한 기간과 사유를 확인하십시오. 제한 해제 전에는 개발행위허가가 나지 않습니다.',
         display_order=31),

    # ---------------- 산림 ----------------
    dict(code='백두대간보호지역', layer_id='lt_c_uf901', role='REGULATION',
         category='산림', default_status='CONDITIONAL', default_difficulty='CRITICAL',
         law='백두대간 보호에 관한 법률', article='제7조(행위 제한)',
         action_required='핵심구역/완충구역 구분에 따라 허용행위가 크게 다릅니다. 구역 구분을 확인하십시오.',
         display_order=41),
    dict(code='산림보호구역', layer_id='lt_c_uf151', role='REGULATION',
         category='산림', default_status='CONDITIONAL', default_difficulty='HIGH',
         law='산림보호법', article='제9조(산림보호구역에서의 행위 제한)',
         action_required='산림보호구역 해제 또는 행위허가 가능 여부를 산림청·지자체와 협의하십시오.',
         display_order=42),
    dict(code='임업산촌진흥권역', layer_id='lt_c_uf602', role='CONTEXT',
         category='산림', default_status='POSSIBLE', default_difficulty='LOW',
         law='임업 및 산촌 진흥촉진에 관한 법률', article='제5조(산촌진흥지역의 지정)',
         action_required='산촌진흥지역이면 지역 주민 소득사업과의 조화 여부를 지자체와 협의하십시오.',
         display_order=45),
    dict(code='하천망', layer_id='lt_c_wkmstrm', role='DISTANCE',
         category='환경', default_status='CONDITIONAL', default_difficulty='MEDIUM',
         proximity_m=500,
         law='하천법', article='제33조(하천의 점용허가)',
         action_required='하천구역·홍수관리구역 저촉 시 하천점용허가가 필요합니다. '
                         '진입도로가 하천을 횡단하는 경우도 대상입니다.',
         display_order=46),
    dict(code='산림입지토양', layer_id='lt_c_fsdifrsts', role='CONTEXT',
         category='산림', default_status='POSSIBLE', default_difficulty='LOW',
         law='해당 없음 (참고 정보)',
         action_required='토양형은 사면 안정성·시공 조건 참고 자료입니다.', display_order=43),
    # ※ lt_c_kfdrssigugrade(산불위험예측지도)는 제외한다 — 속성이 시간대별 예측등급
    #    (class00h~class23h)이라 특정 시점의 예보일 뿐, 입지 검토의 상시 규제 근거가
    #    아니다. 상시 지표로 오인될 수 있어 규제 레이어에 넣지 않는다.

    # ---------------- 자연·환경 ----------------
    dict(code='국립자연공원', layer_id='lt_c_wgisnpgug', role='REGULATION',
         category='환경', default_status='CONDITIONAL', default_difficulty='CRITICAL',
         law='자연공원법', article='제23조(공원구역에서의 행위허가)',
         action_required='국립공원 구역 내 발전시설 설치는 사실상 불가에 가깝습니다. 입지 변경을 검토하십시오.',
         display_order=51),
    dict(code='도립자연공원', layer_id='lt_c_wgisnpdo', role='REGULATION',
         category='환경', default_status='CONDITIONAL', default_difficulty='CRITICAL',
         law='자연공원법', article='제23조', display_order=52),
    dict(code='군립자연공원', layer_id='lt_c_wgisnpgun', role='REGULATION',
         category='환경', default_status='CONDITIONAL', default_difficulty='CRITICAL',
         law='자연공원법', article='제23조', display_order=53),
    dict(code='습지보호지역', layer_id='lt_c_um901', role='REGULATION',
         category='환경', default_status='CONDITIONAL', default_difficulty='CRITICAL',
         law='습지보전법', article='제13조(행위제한)', display_order=54),
    dict(code='습지보호구역', layer_id='lt_c_wgisarwet', role='REGULATION',
         category='환경', default_status='CONDITIONAL', default_difficulty='CRITICAL',
         law='습지보전법', article='제13조', display_order=55),
    dict(code='야생생물보호구역', layer_id='lt_c_um221', role='REGULATION',
         category='환경', default_status='CONDITIONAL', default_difficulty='HIGH',
         law='야생생물 보호 및 관리에 관한 법률', article='제33조(야생생물 특별보호구역 등에서의 행위 제한)',
         action_required='조류 충돌 리스크와 함께 환경영향평가에서 중점 검토 대상이 됩니다.',
         display_order=56),
    # ⚠️ 실측 확인: lt_c_um710에는 상수원보호구역뿐 아니라
    #    '상수원보호기타'(= 상수원 상류 공장설립 승인·제한지역)가 함께 들어온다.
    #    후자는 「물환경보전법」상 **공장** 설립에 걸리는 규제라 풍력발전과 무관한데,
    #    레이어 기본값(CRITICAL)이 그대로 적용되면 종합판정이 통째로 왜곡된다.
    #    → 기본값을 낮추고, 진짜 보호구역만 아래 LAYER_RULES에서 CRITICAL로 올린다.
    dict(code='상수원보호구역', layer_id='lt_c_um710', role='REGULATION',
         category='환경', default_status='CONDITIONAL', default_difficulty='MEDIUM',
         law='수도법', article='제7조(상수원보호구역 지정 등)',
         action_required='조회된 구역 종류를 확인하십시오. 상수원보호구역이면 행위제한이 강하고, '
                         '공장설립 승인·제한지역이면 발전시설과는 규제 대상이 다릅니다.',
         display_order=57),
    dict(code='대기환경규제지역', layer_id='lt_c_um301', role='CONTEXT',
         category='환경', default_status='POSSIBLE', default_difficulty='LOW',
         law='대기환경보전법', article='제18조', display_order=58),
    dict(code='해양보호구역', layer_id='lt_c_tfismpa', role='REGULATION',
         category='환경', default_status='CONDITIONAL', default_difficulty='HIGH',
         law='해양생태계의 보전 및 관리에 관한 법률', article='제25조', display_order=59),

    # ---------------- 농지 ----------------
    dict(code='농업진흥지역', layer_id='lt_c_agrixue101', role='REGULATION',
         category='규제/법령', default_status='CONDITIONAL', default_difficulty='HIGH',
         law='농지법', article='제32조(용도구역에서의 행위 제한) · 제34조(농지의 전용허가)',
         action_required='농업진흥구역/보호구역 구분을 확인하고 농지전용 가능 여부를 협의하십시오.',
         display_order=61),
    dict(code='영농여건불리농지', layer_id='lt_c_agrixue102', role='CONTEXT',
         category='규제/법령', default_status='POSSIBLE', default_difficulty='LOW',
         law='농지법', article='제6조제2항제9호의2', display_order=62),

    # ---------------- 재해 ----------------
    dict(code='재해위험지구', layer_id='lt_c_up201', role='REGULATION',
         category='산림', default_status='CONDITIONAL', default_difficulty='HIGH',
         law='자연재해대책법', article='제12조(자연재해위험개선지구의 지정)', display_order=71),
    dict(code='급경사재해예방지역', layer_id='lt_c_up401', role='REGULATION',
         category='산림', default_status='CONDITIONAL', default_difficulty='HIGH',
         law='급경사지 재해예방에 관한 법률', article='제6조', display_order=72),

    # ---------------- 문화·교육 ----------------
    dict(code='국가유산보호구역', layer_id='lt_c_uo301', role='REGULATION',
         category='안전/문화재', default_status='CONDITIONAL', default_difficulty='HIGH',
         search_margin_m=500,
         law='문화유산의 보존 및 활용에 관한 법률', article='제13조(역사문화환경 보존지역의 보호)',
         action_required='관할 시·도의 현상변경 허용기준을 확인하고 필요 시 현상변경 허가를 신청하십시오.',
         display_order=81),
    dict(code='전통사찰보존', layer_id='lt_c_uo501', role='REGULATION',
         category='안전/문화재', default_status='CONDITIONAL', default_difficulty='MEDIUM',
         law='전통사찰의 보존 및 지원에 관한 법률', article='제6조', display_order=82),
    dict(code='교육환경보호구역', layer_id='lt_c_uo101', role='REGULATION',
         category='안전/문화재', default_status='CONDITIONAL', default_difficulty='MEDIUM',
         law='교육환경 보호에 관한 법률', article='제9조(교육환경보호구역에서의 금지행위 등)',
         action_required='교육환경보호구역 내 행위는 교육환경보호위원회 심의 대상이 될 수 있습니다.',
         display_order=83),

    # ---------------- 군사·항공 (풍력 높이 규제 직결) ----------------
    dict(code='비행금지구역', layer_id='lt_c_aisprhc', role='REGULATION',
         category='안전/문화재', default_status='IMPOSSIBLE', default_difficulty='CRITICAL',
         law='군사기지 및 군사시설 보호법', article='제10조(비행안전구역에서의 금지 또는 제한)',
         action_required='비행금지구역 내 고층 구조물 설치는 불가합니다. 입지를 변경하십시오.',
         display_order=91),
    dict(code='비행제한구역', layer_id='lt_c_aisresc', role='REGULATION',
         category='안전/문화재', default_status='CONDITIONAL', default_difficulty='CRITICAL',
         law='군사기지 및 군사시설 보호법', article='제10조 · 제13조(협의)',
         action_required='관할부대심의위원회 협의를 거쳐야 하며 표면높이 제한을 확인해야 합니다.',
         display_order=92),
    dict(code='관제권', layer_id='lt_c_aisctrc', role='REGULATION',
         category='안전/문화재', default_status='CONDITIONAL', default_difficulty='CRITICAL',
         law='공항시설법', article='제34조(장애물 제한표면)',
         action_required='관제권 내 장애물 제한표면 저촉 여부를 국토교통부·관할부대에 질의하십시오.',
         display_order=93),
    # ⚠️ 항공 공역(비행제한·비행장교통·군작전·훈련·공중전투기동)은 대부분
    #    **일정 고도 이상에만** 적용된다. 예) 영양 상공 MOA 10 = 하한 10,000ft.
    #    평면 중첩만 보면 전국 산간 대부분이 오탐이 되므로 하한고도 속성을 지정해
    #    발전기 최고높이와 비교한 뒤 판정한다.
    dict(code='비행장교통구역', layer_id='lt_c_aisatzc', role='REGULATION',
         category='안전/문화재', default_status='CONDITIONAL', default_difficulty='HIGH',
         altitude_floor_field='atm_lbl_3',
         law='공항시설법', article='제34조', display_order=94),
    dict(code='군작전구역', layer_id='lt_c_aismoac', role='REGULATION',
         category='안전/문화재', default_status='CONDITIONAL', default_difficulty='HIGH',
         altitude_floor_field='moa_lbl_3',
         law='군사기지 및 군사시설 보호법', article='제13조',
         action_required='관할부대 협의 대상입니다. 레이더 전파영향 검토를 함께 요청하십시오.',
         display_order=95),
    dict(code='훈련구역', layer_id='lt_c_aiscatc', role='REGULATION',
         category='안전/문화재', default_status='CONDITIONAL', default_difficulty='HIGH',
         altitude_floor_field='cat_lbl_3',
         law='군사기지 및 군사시설 보호법', article='제13조', display_order=96),
    dict(code='공중전투기동훈련장', layer_id='lt_c_aisacmc', role='REGULATION',
         category='안전/문화재', default_status='CONDITIONAL', default_difficulty='HIGH',
         altitude_floor_field='acm_lbl_3',
         law='군사기지 및 군사시설 보호법', article='제13조', display_order=97),
    dict(code='항공위험구역', layer_id='lt_c_aisdngc', role='REGULATION',
         category='안전/문화재', default_status='CONDITIONAL', default_difficulty='HIGH',
         altitude_floor_field='dng_lbl_3',
         law='항공안전법', article='제78조(공역 등의 지정)',
         action_required='위험구역(D)은 항공기에 위험을 미치는 활동이 이루어지는 공역입니다. '
                         '국토교통부·관할부대에 고층 구조물 설치 가능 여부를 질의하십시오.',
         display_order=98),

    # ---------------- 지적·이격 기초 ----------------
    dict(code='연속지적', layer_id='lp_pa_cbnd_bubun', role='PARCEL',
         category='규제/법령', default_status='POSSIBLE', default_difficulty='LOW',
         law='공간정보의 구축 및 관리 등에 관한 법률', article='제2조(정의) — 지목',
         action_required='소유구분(국·공유지 여부)과 토지사용승낙 확보 계획을 수립하십시오.',
         display_order=101),
    dict(code='건물', layer_id='lt_c_spbd', role='DISTANCE',
         category='지자체 조례', default_status='POSSIBLE', default_difficulty='LOW',
         proximity_m=2000,
         law='지자체 도시·군계획 조례 (이격거리)',
         action_required='주거·정온시설과의 실측 이격거리를 조례 기준과 대조하십시오.',
         display_order=102),
    dict(code='도로', layer_id='lt_l_sprd', role='DISTANCE',
         category='지자체 조례', default_status='POSSIBLE', default_difficulty='LOW',
         proximity_m=1000,
         law='지자체 도시·군계획 조례 (이격거리)',
         action_required='도로 이격거리 조례가 있는 지자체인지 확인하십시오.',
         display_order=103),
]


# ----------------------------------------------------------------------
# 구역명별 세부 판정 규칙 (RegulationRule)
#   레이어 하나에 성격이 다른 구역이 섞여 들어오는 경우, 레이어 기본값만으로는
#   오탐이 난다. 실측으로 확인된 구역명에 한해 규칙을 둔다. (추측 금지)
#   condition_key는 구역명(uname)에 부분일치하면 적용된다.
# ----------------------------------------------------------------------
LAYER_RULES: list[dict] = [
    dict(layer='상수원보호구역', condition_key='상수원보호구역',
         condition_desc='수도법상 상수원보호구역',
         status='CONDITIONAL', difficulty='CRITICAL',
         reason_template='상수원보호구역은 행위제한이 강해 발전시설 설치가 사실상 어렵습니다. '
                         '입지 변경을 우선 검토하십시오.',
         law='수도법', article='제7조(상수원보호구역 지정 등)'),
    # ── 필지 지역지구(토지이용계획) — 개별 레이어로 조회되지 않는 규제를 잡는다 ──
    dict(layer='필지지역지구', condition_key='수변구역',
         condition_desc='4대강 수계법상 수변구역',
         status='CONDITIONAL', difficulty='CRITICAL',
         reason_template='수변구역은 상수원 수질보전을 위해 지정되어 오염물질 배출시설 등의 '
                         '설치가 제한됩니다. 해당 수계법의 행위제한을 확인하십시오.',
         law='한강수계 상수원수질개선 및 주민지원 등에 관한 법률', article='제4조·제5조'),
    dict(layer='필지지역지구', condition_key='생태·경관보전지역',
         condition_desc='자연환경보전법상 생태·경관보전지역',
         status='CONDITIONAL', difficulty='CRITICAL',
         reason_template='생태·경관보전지역은 핵심구역에서 개발행위가 원칙적으로 금지됩니다. '
                         '구역 구분(핵심/완충/전이)을 확인하십시오.',
         law='자연환경보전법', article='제15조(생태·경관보전지역에서의 행위제한)'),
    dict(layer='필지지역지구', condition_key='대공방어협조구역',
         condition_desc='군사기지법상 대공방어협조구역 — 고도 제한 직결',
         status='CONDITIONAL', difficulty='HIGH',
         reason_template='대공방어협조구역은 일정 높이 이상 구조물 설치 시 관할부대 협의 '
                         '대상입니다. 풍력발전기는 높이가 커 해당될 가능성이 높습니다.',
         law='군사기지 및 군사시설 보호법', article='제13조(행정기관의 처분등에 관한 협의)'),
    dict(layer='필지지역지구', condition_key='수질보전특별대책지역',
         condition_desc='환경정책기본법상 특별대책지역',
         status='CONDITIONAL', difficulty='HIGH',
         reason_template='수질보전 특별대책지역은 오염총량 관리와 입지 제한이 강화됩니다.',
         law='환경정책기본법', article='제38조(특별종합대책의 수립)'),
    dict(layer='필지지역지구', condition_key='토지거래계약',
         condition_desc='토지거래계약 허가구역',
         status='CONDITIONAL', difficulty='MEDIUM',
         reason_template='토지거래계약 허가구역은 부지 매입 시 사전 허가가 필요해 '
                         '용지 확보 일정에 영향을 줍니다.',
         law='부동산 거래신고 등에 관한 법률', article='제11조(허가구역 내 토지거래에 대한 허가)'),

    dict(layer='상수원보호구역', condition_key='상수원보호기타',
         condition_desc='상수원 상류 공장설립 승인·제한지역 (실측 확인된 구역명)',
         status='CONDITIONAL', difficulty='LOW',
         reason_template='조회된 구역은 상수원 상류 「공장설립 승인·제한지역」으로, '
                         '규제 대상이 공장 설립입니다. 풍력발전시설은 공장에 해당하지 않아 '
                         '직접 저촉되지는 않으나, 상수원 상류라는 입지 특성상 '
                         '환경영향평가에서 수질 항목이 중점 검토됩니다.',
         law='물환경보전법', article='제33조(배출시설의 설치 허가 및 신고)'),
]


class Command(BaseCommand):
    help = '규제 레이어 정의(RegulationLayer)를 실측 결과 기반으로 시드합니다.'

    def add_arguments(self, parser):
        parser.add_argument('--probe', default=str(PROBE_PATH),
                            help='vworld_probe.py 산출 JSON 경로')

    @transaction.atomic
    def handle(self, *args, **opts):
        probe_path = Path(opts['probe'])
        probe = self._load_probe(probe_path)
        samples = probe.get('samples') or {}

        # 레이어 제목은 GetCapabilities 전체 목록(layers.json)에서 가져온다
        layer_titles: dict[str, str] = {}
        layers_json = probe_path.parent / 'layers.json'
        if layers_json.exists():
            for l in json.loads(layers_json.read_text(encoding='utf-8')):
                layer_titles[l['name']] = l.get('title', '')

        describe = probe.get('describe') or {}

        created = updated = 0
        for spec in SPECS:
            lid = spec['layer_id']
            s = samples.get(lid) or {}
            keys = s.get('property_keys') or []
            schema = [a['name'] for a in (describe.get(lid) or [])]

            # 명칭 속성 — 표본 응답 우선, 없으면 서버가 선언한 스키마에서 채택
            name_field = next((c for c in NAME_FIELD_CANDIDATES if c in keys), '')
            if name_field:
                probe_status = 'OK'                 # 값까지 확인
                note = ''
            else:
                name_field = next((c for c in NAME_FIELD_CANDIDATES if c in schema), '')
                if name_field:
                    probe_status = 'SCHEMA'
                    note = (f"표본 지점에 피처가 없어 속성값은 미확인 — "
                            f"DescribeFeatureType 선언 속성 사용. "
                            f"상태: {', '.join(s.get('statuses', []))[:150]}")
                elif schema:
                    # 스키마는 받았으나 명칭 속성 자체가 없는 레이어
                    # (예: 습지보호구역은 bcode만 제공) → 구역명 없이 저촉 여부만 판정
                    probe_status = 'NO_NAME'
                    note = (f'명칭 속성이 없는 레이어입니다. 선언 속성: '
                            f'{", ".join(schema[:12])}')
                else:
                    probe_status = 'UNRESOLVED'
                    note = 'DescribeFeatureType 응답을 받지 못했습니다.'

            if s and any('ERROR' in st or 'EXC' in st for st in s.get('statuses', [])):
                probe_status = 'ERROR'
                note = f"데이터 API 호출 오류 — {', '.join(s.get('statuses', []))[:150]}"

            defaults = dict(
                provider='VWORLD',
                title=layer_titles.get(lid, ''),
                role=spec.get('role', 'REGULATION'),
                category=spec.get('category', ''),
                name_field=name_field,
                extra_fields=[k for k in (keys or schema)
                              if k != name_field and not k.startswith('lt_')
                              and k != 'ag_geom'][:12],
                geometry_type=s.get('geometry_type', ''),
                default_status=spec.get('default_status', 'CONDITIONAL'),
                default_difficulty=spec.get('default_difficulty', 'MEDIUM'),
                proximity_m=spec.get('proximity_m', 0),
                search_margin_m=spec.get('search_margin_m', 0),
                law=spec.get('law', ''),
                article=spec.get('article', ''),
                action_required=spec.get('action_required', ''),
                altitude_floor_field=spec.get('altitude_floor_field', ''),
                probe_status=probe_status,
                probe_note=note,
                # 법령 원문 대조는 아직 수행되지 않았다 (docs/WINDSITE_API_KEYS.md §3 참조)
                confidence=spec.get('confidence', 'LOW'),
                source_url='https://www.vworld.kr',
                display_order=spec.get('display_order', 100),
                is_active=True,
            )
            obj, is_new = RegulationLayer.objects.update_or_create(
                code=spec['code'], defaults={**defaults, 'layer_id': lid})
            created += is_new
            updated += (not is_new)

        # 구역명별 세부 규칙
        rules = 0
        for r in LAYER_RULES:
            RegulationRule.objects.update_or_create(
                layer=r['layer'], condition_key=r['condition_key'],
                defaults={**{k: v for k, v in r.items()
                             if k not in ('layer', 'condition_key')},
                          'confidence': r.get('confidence', 'MEDIUM'),
                          'is_active': True},
            )
            rules += 1
        self.stdout.write(f'구역명별 판정 규칙 {rules}건 반영')

        ok = RegulationLayer.objects.filter(probe_status='OK').count()
        schema = RegulationLayer.objects.filter(probe_status='SCHEMA').count()
        noname = RegulationLayer.objects.filter(probe_status='NO_NAME').count()
        self.stdout.write(self.style.SUCCESS(
            f'RegulationLayer 시드 완료 — 신규 {created} / 갱신 {updated} '
            f'(표본값 확인 {ok}건 · 서버 스키마 기반 {schema}건 · 명칭속성 없음 {noname}건)'))
        blind = list(RegulationLayer.objects
                     .exclude(probe_status__in=['OK', 'SCHEMA', 'NO_NAME'])
                     .exclude(role__in=['PARCEL', 'DISTANCE'])
                     .values_list('code', 'probe_status'))
        if blind:
            self.stdout.write(self.style.WARNING(
                '명칭 속성을 확정하지 못해 판정이 보류되는 레이어: '
                + ', '.join(f'{c}({s})' for c, s in blind)))

    # ------------------------------------------------------------------
    def _load_probe(self, path: Path) -> dict:
        if not path.exists():
            self.stdout.write(self.style.WARNING(
                f'실측 결과가 없습니다 ({path}). 명칭 속성은 비워 둡니다 — '
                '`python scripts/vworld_probe.py`를 먼저 실행하십시오.'))
            return {}
        return json.loads(path.read_text(encoding='utf-8'))
