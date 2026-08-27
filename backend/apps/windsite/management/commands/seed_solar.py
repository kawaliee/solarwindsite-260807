"""
태양광 인허가 절차·관련 법령 시드
---------------------------------------------------------------
풍력 시드(`seed_windsite`)의 태양광 판이다. 절차와 법령은 에너지원마다
다르므로 섞지 않는다 — 발전사업허가 소관만 해도 용량 경계가 갈린다.

■ 조문은 원문으로 확인한 것만 적는다

2026-08 국가법령정보 OPEN API로 아래를 대조했다. 그 과정에서 두 가지가
드러났고, 대조하지 않았다면 그대로 틀린 근거가 실렸을 것이다.

  · `search_law('전기사업법')`의 첫 결과가 **전기공사업법**이었다.
    법령명이 정확히 일치하는 것을 골라야 한다.
  · **전기사업법 제62조는 2020-03-31 삭제**됐다. 종전 자료를 그대로
    옮겼다면 폐지된 조문을 공사계획 근거로 달 뻔했다.

confidence는 여기서 단정하지 않고 `verify_laws --apply`가 원문 대조로
부여한다. 이 명령은 **무엇을 검토해야 하는지의 목록**을 넣을 뿐이다.

■ 실무 검수 반영 (2026-08)

  1. **발전사업허가** — 3MW 이하는 **시·도 소관**으로 진행. 해당 시·도의
     사무위임 조례·규칙과 시행령을 따른다. (사내 확정)
  2. **농지** — **타용도 일시사용(염해간척농지)** 을 표준 경로로 삼는다.
     염도 평가를 먼저 두고, 해당하지 않으면 전용허가를 대안으로 둔다. (사내 확정)
  3. **환경영향평가** — 시행령 별표3 원문 대조 결과 **태양력ㆍ풍력은 10만kW
     (100MW) 이상**이 대상이다. 일반 발전소 기준(1만kW)의 10배로 따로 정해져
     있어, 일반 기준을 적용하면 10MW짜리가 대상으로 잡혀 일정이 어긋난다.
  4. **소규모환경영향평가** — 태양광은 **용량이 아니라 용도지역별 면적**으로
     갈린다(별표4). 별표4가 태양광을 직접 언급하는 곳은 오히려 제외 규정이라
     ("유휴토지에 단순한 공작물 설치"), 100MW 미만이라고 자동 대상이 되지 않는다.

■ ⚠️ 남은 검수 항목

  · 개발행위허가 운영 — 지자체별 편차, 사전 협의 관행
  · 공작물 축조신고 — 지자체별 운영 확인

사용
  python manage.py seed_solar            # 미리보기
  python manage.py seed_solar --apply    # DB 반영
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.windsite.models import LawReference, PermitStep


#: 순번의 시작. 풍력과 겹치지 않게 800번대를 쓴다 — 두 에너지원이 한 표에
#: 있고 order로 정렬되기 때문이다.
ORDER_BASE = 801

#: 절차가 실제로 밟히는 차례. **순번은 여기서만 정한다.**
#:
#: 종전에는 각 절차 dict가 `order=808` 꼴로 숫자를 들고 있었다. 절차를 하나
#: 끼워 넣을 때마다 뒤를 손으로 밀어야 했고, 실제로 807·808·812~815가
#: 두 번씩 쓰여 나중 것이 앞의 것을 덮었다. 이름 목록 하나로 두면 그런
#: 충돌이 구조적으로 생기지 않는다.
SEQUENCE: list[str] = [
    '사업 예비검토 · 부지 확보',
    '염도 평가 (염해간척농지 확인)',
    '발전사업허가',
    '소규모 환경영향평가',
    '환경영향평가 (100MW 이상)',
    '재해영향평가등의 협의',
    '매장유산 지표조사',
    '농지 타용도 일시사용허가·신고',
    '농지전용허가 · 협의 (당사 미사용 · 참고)',
    '산지전용허가',
    '산지일시사용허가·신고',
    '국유재산 사용허가 · 대부계약',
    '농업생산기반시설 목적 외 사용승인',
    '도로점용허가',
    '하천점용허가',
    '개발행위허가',
    '개발행위 도시계획위원회 심의',
    '공작물 축조신고 (구조물)',
    '전기사업용전기설비 공사계획 인가·신고',
    '계통 접속 신청 · 사용전점검',
    '사용전검사',
    '신·재생에너지 공급인증서(REC) 설비확인',
    '전력수급계약(PPA) 또는 전력시장 등록',
]

STEPS: list[dict] = [
    dict(phase='DEV', name='사업 예비검토 · 부지 확보',
         authority='사업자',
         law='', article='',
         note='토지사용승낙 또는 매매계약. 지목·소유구분·진입도로를 함께 확인한다.'),

    dict(phase='PERMIT', name='발전사업허가', depends_on=['염도 평가 (염해간척농지 확인)'],
         authority='설비용량 3MW 이하 시·도지사 / 3MW 초과 산업통상자원부(전기위원회)',
         law='전기사업법', article='제7조(전기사업의 허가)',
         capacity_rule='**3MW 이하는 시·도 소관**이며, 해당 시·도의 사무위임 조례·규칙과 '
                       '전기사업법 시행령이 정하는 바에 따라 진행한다(사내 확정 기준). '
                       '3MW를 넘으면 산업통상자원부 전기위원회 심의를 거친다.',
         note='허가일은 조례 부칙 경과조치의 기준일이 되므로 반드시 기록한다. '
              '3MW 이하라도 시·도마다 접수·심의 운영이 달라 사전 문의가 필요하다.'),

    dict(phase='PERMIT', name='개발행위허가', depends_on=['소규모 환경영향평가', '농지 타용도 일시사용허가·신고'],
         authority='시장·군수·구청장',
         law='국토의 계획 및 이용에 관한 법률', article='제56조(개발행위의 허가)',
         # **원문 대조 완료** — 시행령 제54조제1항: "법 제57조제2항에서
         # '대통령령으로 정하는 기간'이란 15일(도시계획위원회의 심의를 거쳐야
         # 하거나 관계 행정기관의 장과 협의를 하여야 하는 경우에는 심의 또는
         # 협의기간을 제외한다)을 말한다."
         statutory_days=15,
         note='지자체 조례의 이격거리·경사도 기준이 이 단계에서 적용된다. '
              '운영 편차가 커 사전 협의가 사실상 필수다. '
              '법정 15일에는 **도시계획위원회 심의·관계기관 협의 기간이 빠져 '
              '있다**(시행령 제54조제1항) — 실제 소요는 이보다 길다.'),

    dict(phase='DEV', name='염도 평가 (염해간척농지 확인)', depends_on=['사업 예비검토 · 부지 확보'],
         authority='한국농어촌공사 / 시·군 농정부서',
         law='농지법', article='제36조(농지의 타용도 일시사용허가 등)',
         conditional_on='FARMLAND',
         note='**당사 표준 경로의 출발점이다.** 타용도 일시사용은 염해간척농지 여부가 '
              '전제이므로 토양 염도를 먼저 측정한다. 기준은 농지법 시행규칙 제31조의2 — '
              '「사업구역 내 농지면적의 100분의 90 이상이 필지별 토양 염도 5.50 dS/m 이상」. '
              '측정 절차·기관·비용은 농림축산식품부장관 고시에 따른다.'),

    # ── 면적·부지 조건으로 갈리는 절차 (2026-08 원문 대조 후 추가) ──────
    dict(phase='PERMIT', name='재해영향평가등의 협의', depends_on=['발전사업허가'],
         authority='행정안전부장관 (관계행정기관의 장이 협의 요청)',
         law='자연재해대책법',
         article='제4조(재해영향평가등의 협의) · 제5조(재해영향평가등의 협의 대상)',
         conditional_on='AREA_DISASTER',
         statutory_basis='UNKNOWN',
         note='**원문 대조 완료** — 법 제5조제1항제3호가 「에너지 개발」을 협의 '
              '대상 종류로 든다. 다만 **대상 규모는 시행령 제6조제1항 별표1**에 '
              '있고, 별표 본문을 API로 받지 못해 면적 기준을 확정하지 못했다. '
              '별표1의 제목(「재해영향평가등의 협의 대상 행정계획 및 개발사업의 '
              '범위 및 협의시기」)까지만 확인했다. **인허가청에 대상 여부를 '
              '확인할 것.** 처리기간도 확인하지 못했다.'),

    dict(phase='PERMIT', name='매장유산 지표조사', depends_on=['발전사업허가'],
         authority='국가유산청 (매장유산 조사기관 수행)',
         law='매장유산 보호 및 조사에 관한 법률', article='',
         conditional_on='HERITAGE',
         statutory_basis='UNKNOWN',
         note='⚠️ **조문 번호를 확정하지 못했다.** 2024-02-13 개정으로 지표조사 '
              '조항이 재편되어(제6조 → 제6조의2 등) 종전 자료의 「제6조」는 현행 '
              '조문과 맞지 않는다. 확인한 것은 제6조의2가 **국가·지방자치단체의** '
              '지표조사라는 점까지이며, **사업시행자 지표조사의 현행 조문과 대상 '
              '사업면적 기준은 원문에서 확인하지 못했다.** 국가유산청에 확인할 것.'),

    dict(phase='PERMIT', name='국유재산 사용허가 · 대부계약', depends_on=['발전사업허가'],
         authority='중앙관서의 장 (행정재산) / 한국자산관리공사·기획재정부 (일반재산)',
         law='국유재산법',
         article='제30조(사용허가) · 제47조(대부료, 계약의 해제 등)',
         conditional_on='PUBLIC_LAND',
         statutory_basis='UNKNOWN',
         note='**원문 대조 완료** — 행정재산은 제30조 사용허가, 일반재산은 '
              '제47조가 준용하는 대부계약이다. 제31조제1항이 **경쟁입찰을 원칙**'
              '으로 하므로(시행령 제27조), 수의계약이 되는지를 먼저 확인해야 '
              '한다. 공유재산은 「공유재산 및 물품 관리법」이 따로 적용된다. '
              '처리기간 규정은 확인하지 못했다.'),

    dict(phase='PERMIT', name='농업생산기반시설 목적 외 사용승인',
         depends_on=['발전사업허가'],
         authority='시장·군수·구청장 (한국농어촌공사 관리 시설은 공사)',
         law='농어촌정비법', article='제23조(농업생산기반시설의 사용허가)',
         conditional_on='FARM_INFRA',
         statutory_basis='UNKNOWN',
         note='**원문 대조 완료** — 용·배수로(구거)·저수지(유지)가 부지에 물려 '
              '있으면 대상이다. 제23조제2항이 「본래의 목적 또는 사용에 방해가 '
              '되지 아니하는 범위」로 한정한다. 관리자가 한국농어촌공사면 공사에 '
              '사용신청서를 낸다(시행령 제31조제2항). 처리기간 규정은 확인하지 '
              '못했다.'),

    dict(phase='PERMIT', name='도로점용허가', depends_on=['발전사업허가'],
         authority='도로관리청 (국도 국토교통부 · 지방도 시·도 · 시군도 시·군)',
         law='도로법', article='제61조(도로의 점용 허가)',
         conditional_on='ROAD',
         statutory_basis='UNKNOWN',
         note='**원문 대조 완료** — 진입로 개설·전력케이블 매설로 도로를 '
              '점용하면 대상이다. 허가 기준은 대통령령이 정한다(제61조제3항). '
              '**경과지가 확정돼야 대상 도로와 관리청이 정해진다.** 조문에 '
              '처리기간 규정은 없고 시행령은 확인하지 못했다.'),

    dict(phase='PERMIT', name='하천점용허가', depends_on=['발전사업허가'],
         authority='하천관리청 (국가하천 환경부 · 지방하천 시·도지사)',
         law='하천법', article='제33조(하천의 점용허가 등)',
         conditional_on='RIVER',
         statutory_basis='UNKNOWN',
         note='**원문 대조 완료** — 하천구역 안에서 토지 점용·공작물 설치·'
              '형질변경을 하면 대상이다(제33조제1항제1호·제3호·제4호). '
              '제33조제4항제4호가 **콘크리트 등 고정구조물 설치를 원칙적으로 '
              '금지**하므로 구조물 형식이 쟁점이 된다. 조문에 처리기간 규정은 '
              '없고 시행령은 확인하지 못했다.'),

    dict(phase='PERMIT', name='개발행위 도시계획위원회 심의',
         depends_on=['개발행위허가'],
         authority='중앙도시계획위원회 또는 지방도시계획위원회',
         law='국토의 계획 및 이용에 관한 법률',
         article='제59조(개발행위에 대한 도시계획위원회의 심의)',
         conditional_on='AREA_CITY_COMMITTEE',
         # **원문 대조 완료** — 시행령 제54조제1항이 개발행위허가 15일에서
         # "심의 또는 협의기간을 제외한다"고 명시한다. 즉 심의 자체에는
         # 법정 처리기간이 없다. '확인 못 함'과 다르므로 NONE으로 적는다.
         statutory_basis='NONE',
         note='**원문 대조 완료** — 시행령 제57조제1항제1호는 형질변경 면적이 '
              '제55조제1항 규모 이상인 경우를 심의 대상으로 든다. 제55조제1항은 '
              '**관리지역·농림지역 3만m², 자연환경보전지역 5천m²**다. '
              '심의기간은 개발행위허가 법정 15일에서 제외되므로(시행령 '
              '제54조제1항) 실제 일정에 별도로 잡아야 한다.'),

    dict(phase='PERMIT', name='농지 타용도 일시사용허가·신고', depends_on=['염도 평가 (염해간척농지 확인)', '발전사업허가'],
         authority='시장·군수·구청장',
         law='농지법',
         article='제36조(농지의 타용도 일시사용허가 등) · 제36조의2(농지의 타용도 일시사용신고 등)',
         conditional_on='FARMLAND',
         note='**당사 표준 경로.** 염해간척농지 기준으로 진행한다. 농지를 전용하지 않아 '
              '농지보전부담금이 발생하지 않으나 **사용기간 제한**이 있어, 사업기간·PPA 기간과 '
              '맞는지 반드시 대조한다. 기간 만료 시 원상복구 의무를 검토할 것. '
              '**농업진흥구역이라도 시행령 제29조제7항제7호가 태양에너지 발전설비를 '
              '허용 행위로 두고 있어 구역만으로 불가가 되지 않는다.**'),

    dict(phase='PERMIT', name='농지전용허가 · 협의 (당사 미사용 · 참고)',
         authority='농림축산식품부장관 또는 시·도지사·시장·군수 (면적별)',
         law='농지법', article='제34조(농지의 전용허가ㆍ협의)',
         conditional_on='FARMLAND',
         is_active=False,
         note='**당사는 이 경로를 쓰지 않는다** — 농지 사업은 염도평가 기반 '
              '타용도 일시사용(805)으로 진행한다. 참고로 남기며, 전용을 택하더라도 '
              '농지법 시행령 제44조제6호에 따라 **부지 농지면적 3만㎡(3ha)를 '
              '초과하면 전용허가 제한대상**이라 대규모 사업에서는 성립하지 않는다.'),

    dict(phase='PERMIT', name='산지전용허가', depends_on=['발전사업허가'],
         authority='산림청장 또는 시·도지사·시장·군수 (면적별)',
         law='산지관리법', article='제14조(산지전용허가)',
         conditional_on='FOREST',
         note='평균경사도·표고 기준이 있다(시행령 별표4). 대체산림자원조성비가 발생한다.'),

    dict(phase='PERMIT', name='산지일시사용허가·신고', depends_on=['발전사업허가'],
         authority='산림청장 또는 시·도지사·시장·군수',
         law='산지관리법', article='제15조의2(산지일시사용허가ㆍ신고)',
         conditional_on='FOREST',
         note='전용 대신 일시사용으로 가는 경우의 근거다.'),

    dict(phase='PERMIT', name='소규모 환경영향평가', depends_on=['발전사업허가'],
         authority='유역·지방환경청',
         law='환경영향평가법',
         article='제43조(소규모 환경영향평가의 대상) · 제44조(소규모 환경영향평가서의 작성 및 협의 요청 등)',
         conditional_on='SMALL_EIA',
         note='대상 규모는 시행령 별표4로 확정해야 한다. 용도지역·면적에 따라 갈린다.'),

    dict(phase='PERMIT', name='공작물 축조신고 (구조물)', depends_on=['개발행위허가'],
         authority='시장·군수·구청장',
         law='건축법', article='제83조(옹벽 등의 공작물에의 준용)',
         note='모듈 지지구조물·옹벽이 대상이 될 수 있다. 지자체 운영을 확인한다.'),

    dict(phase='PERMIT', name='환경영향평가 (100MW 이상)', depends_on=['발전사업허가'],
         authority='유역·지방환경청',
         law='환경영향평가법', article='시행령 별표3',
         conditional_on='EIA',
         note='**원문 대조 완료** — 시행령 별표3은 태양력ㆍ풍력의 경우 발전시설용량 '
              '10만kW(100MW) 이상을 대상으로 한다. 일반 발전소 기준(1만kW)과 다르므로 '
              '그대로 적용하면 안 된다.'),

    dict(phase='BUILD', name='전기사업용전기설비 공사계획 인가·신고', depends_on=['개발행위허가'],
         authority='산업통상자원부 또는 시·도지사',
         law='전기사업법', article='제61조(전기사업용전기설비의 공사계획의 인가 또는 신고)',
         note='⚠️ 종전 자료에 보이는 제62조는 **2020-03-31 삭제**됐다. 제61조가 현행이다.'),

    dict(phase='BUILD', name='계통 접속 신청 · 사용전점검', depends_on=['발전사업허가'],
         authority='한국전력공사',
         law='전기사업법', article='제27조의2(전력계통의 운영)',
         note='접속 가능 용량과 보강 필요 여부가 사업 성패를 가른다. '
              '한전 분산전원 연계정보로 사전 확인한다.'),

    dict(phase='BUILD', name='사용전검사', depends_on=['전기사업용전기설비 공사계획 인가·신고'],
         authority='한국전기안전공사',
         law='전기안전관리법', article='제9조(사용전검사)',
         note='검사에 합격해야 전기설비를 사용할 수 있다.'),

    dict(phase='OPS', name='신·재생에너지 공급인증서(REC) 설비확인', depends_on=['사용전검사'],
         authority='한국에너지공단 신·재생에너지센터',
         law='신에너지 및 재생에너지 개발·이용·보급 촉진법',
         article='제12조의7(신ㆍ재생에너지 공급인증서 등)',
         note='REC 발급의 전제다. 설비확인을 받아야 공급인증서가 나온다.'),

    dict(phase='OPS', name='전력수급계약(PPA) 또는 전력시장 등록', depends_on=['사용전검사'],
         authority='한국전력공사 / 한국전력거래소',
         law='전기사업법', article='제31조(전력거래)',
         note='1MW 이하는 한전과의 PPA 경로가 열려 있다. 용량에 따라 갈린다.'),
]


#: 태양광 사업에 적용되는 법령. 풍력과 겹치는 것도 있으나 역할 설명이 달라
#: 에너지원별로 따로 둔다.
LAWS: list[dict] = [
    dict(name='전기사업법', category='전기',
         purpose='발전사업허가·공사계획·전력거래의 근거',
         key_articles='제7조(전기사업의 허가) · 제61조(공사계획 인가·신고) · 제31조(전력거래)'),
    dict(name='국토의 계획 및 이용에 관한 법률', category='국토',
         purpose='개발행위허가 — 지자체 조례의 이격거리·경사도 기준이 여기서 적용된다',
         key_articles='제56조(개발행위의 허가) · 제58조(개발행위허가의 기준)'),
    dict(name='농지법', category='농지',
         purpose='농지 전용 또는 타용도 일시사용 — 태양광 부지의 상당수가 농지다',
         key_articles='제34조(농지의 전용허가ㆍ협의) · 제36조 · 제36조의2(타용도 일시사용)'),
    dict(name='산지관리법', category='산림',
         purpose='산지전용·일시사용. 평균경사도 기준이 입지를 가른다',
         key_articles='제14조(산지전용허가) · 제15조의2(산지일시사용허가ㆍ신고)'),
    dict(name='환경영향평가법', category='환경',
         purpose='소규모 환경영향평가 대상 여부와 협의 절차',
         key_articles='제43조(소규모 환경영향평가의 대상) · 제44조(평가서 작성·협의)'),
    dict(name='전기안전관리법', category='전기',
         purpose='사용전검사 — 합격해야 설비를 사용할 수 있다',
         key_articles='제9조(사용전검사)'),
    dict(name='신에너지 및 재생에너지 개발·이용·보급 촉진법', category='전기',
         purpose='REC 설비확인과 공급인증서 발급',
         key_articles='제12조의7(신ㆍ재생에너지 공급인증서 등)'),
    dict(name='건축법', category='국토',
         purpose='모듈 지지구조물·옹벽의 공작물 축조신고',
         key_articles='제83조(옹벽 등의 공작물에의 준용)'),

    # ── 2026-08 원문 대조 후 추가 ─────────────────────────────────────
    dict(name='자연재해대책법', category='안전',
         purpose='재해영향평가등의 협의 — 「에너지 개발」이 협의 대상 종류다',
         key_articles='제4조(재해영향평가등의 협의) · 제5조(협의 대상) · '
                      '시행령 제6조제1항 별표1(대상 범위·협의시기)'),
    dict(name='국유재산법', category='국토',
         purpose='부지에 국유지가 섞이면 사용허가(행정재산) 또는 대부(일반재산)',
         key_articles='제30조(사용허가) · 제31조(사용허가의 방법) · 제47조(대부료 등)'),
    dict(name='농어촌정비법', category='농지',
         purpose='용·배수로(구거)·저수지(유지) 등 농업생산기반시설의 목적 외 사용',
         key_articles='제23조(농업생산기반시설의 사용허가)'),
    dict(name='도로법', category='국토',
         purpose='진입로 개설·케이블 매설 등 도로 점용',
         key_articles='제61조(도로의 점용 허가)'),
    dict(name='하천법', category='국토',
         purpose='하천구역 내 점용·공작물 설치. 고정구조물은 원칙적으로 금지된다',
         key_articles='제33조(하천의 점용허가 등)'),
    dict(name='매장유산 보호 및 조사에 관한 법률', category='안전',
         purpose='매장유산 지표조사 — ⚠️ 2024-02-13 개정으로 조문이 재편되어 '
                 '사업시행자 지표조사의 현행 조문을 확정하지 못했다',
         key_articles='◇ 확인 필요 (제6조는 현행 조문이 아님 · 제6조의2는 '
                      '국가·지방자치단체의 지표조사)'),
]


def ordered_steps() -> list[dict]:
    """
    `SEQUENCE` 차례대로 절차에 순번을 매긴다.

    이름이 어긋나면 조용히 넘어가지 않고 바로 세운다 — 오탈자 하나로 절차가
    로드맵에서 사라지면 그 사실을 아무도 모른다.
    """
    by_name = {s['name']: s for s in STEPS}
    missing = [n for n in SEQUENCE if n not in by_name]
    extra = [n for n in by_name if n not in SEQUENCE]
    if missing or extra:
        raise ValueError(
            'SEQUENCE와 STEPS의 절차명이 어긋납니다 — '
            f'SEQUENCE에만 있음: {missing} · STEPS에만 있음: {extra}')
    return [{**by_name[n], 'order': ORDER_BASE + i}
            for i, n in enumerate(SEQUENCE)]


class Command(BaseCommand):
    help = '태양광 인허가 절차·관련 법령을 등록합니다 (--apply 로 반영).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='DB에 반영')

    def handle(self, *args, **o):
        if not o['apply']:
            self.stdout.write(self.style.WARNING(
                '미리보기입니다. 반영하려면 --apply 를 붙이십시오.\n'))

        steps = ordered_steps()
        self.stdout.write(f'인허가 절차 {len(steps)}건')
        for s in steps:
            self.stdout.write('  %3d. [%s] %-30s %s' % (
                s['order'], s['phase'], s['name'][:30], (s.get('law') or '-')))
        self.stdout.write(f'\n관련 법령 {len(LAWS)}건')
        for l in LAWS:
            self.stdout.write('  %-34s %s' % (l['name'][:34], l['category']))

        if not o['apply']:
            return

        with transaction.atomic():
            n_step = n_law = 0
            for s in steps:
                # confidence는 여기서 단정하지 않는다. verify_laws가 원문을
                # 대조해 부여한다 — 이 명령은 '무엇을 검토하는가'만 넣는다.
                # ⚠️ 지정하지 않은 선택 필드를 **반드시 비운다.**
                #    update_or_create의 defaults에 없는 필드는 손대지
                #    않으므로, order를 재배치하면 그 자리에 있던 다른
                #    절차의 값이 남는다. 실제로 811(환경영향평가)의
                #    conditional_on='EIA'가 공작물 축조신고에 그대로
                #    붙어, 신고가 '환경영향평가 대상이 아니라 생략'으로
                #    나왔다.
                base = {'conditional_on': '', 'depends_on': [],
                        'capacity_rule': '', 'statutory_days': None,
                        'statutory_basis': 'UNKNOWN',
                        'note': '', 'law': '', 'article': ''}
                PermitStep.objects.update_or_create(
                    energy_type='SOLAR', order=s['order'],
                    defaults={**base,
                              **{k: v for k, v in s.items() if k != 'order'},
                              'confidence': 'LOW',
                              'is_active': s.get('is_active', True)})
                n_step += 1

            # 순번이 줄어든 경우 옛 자리에 남은 절차를 지운다. 남겨 두면
            # 이름은 예전 것인데 조건은 아무도 손대지 않은 유령 절차가
            # 로드맵에 낀다.
            stale = PermitStep.objects.filter(energy_type='SOLAR').exclude(
                order__in=[s['order'] for s in steps])
            n_stale = stale.count()
            if n_stale:
                self.stdout.write(self.style.WARNING(
                    '\n옛 순번에 남은 절차 %d건을 지웁니다 — %s'
                    % (n_stale, ' · '.join(f'{x.order} {x.name}' for x in stale))))
                stale.delete()

            for l in LAWS:
                LawReference.objects.update_or_create(
                    energy_type='SOLAR', name=l['name'],
                    defaults={**{k: v for k, v in l.items() if k != 'name'},
                              'confidence': 'LOW'})
                n_law += 1

        self.stdout.write(self.style.SUCCESS(
            f'\n반영 — 절차 {n_step}건 · 법령 {n_law}건'))
        self.stdout.write(self.style.WARNING(
            '조문 신뢰도는 아직 LOW입니다. '
            '`python manage.py verify_laws --apply` 로 원문을 대조하십시오.'))
