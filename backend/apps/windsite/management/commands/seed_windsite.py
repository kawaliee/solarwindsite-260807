"""
풍력 입지·인허가 기준 시드 데이터

⚠️ 검증 상태 안내
   본 시드는 2026-08 기준 웹 조사 결과를 담고 있으나, 조사 환경에서
   국가법령정보센터(law.go.kr) 등 정부 1차 사이트 접근이 차단되어
   상당수 조문을 **원문 대조하지 못했다.**
   각 레코드의 confidence 값이 그 검증 수준이며, LOW 항목은 실무 적용 전
   반드시 원문을 확인하고 verified_at을 갱신해야 한다.

   HIGH   = 법령 원문 또는 공식 발표로 직접 확인
   MEDIUM = 공신력 있는 2차 자료(로펌 뉴스레터·전문지 등)로 교차 확인
   LOW    = 미검증 — 원문 대조 필요
"""
from datetime import date

from django.core.management.base import BaseCommand

from apps.windsite.models import LawReference, LocalOrdinance, PermitStep, RegulationRule

TODAY = date(2026, 8, 3)


# ======================================================================
# 1. 관련 법령
# ======================================================================
LAWS = [
    dict(name='전기사업법', category='전기',
         purpose='발전사업허가, 공사계획 인가·신고, 사용전검사, 사업개시신고의 근거',
         key_articles='제7조(전기사업의 허가) / 제9조(전기설비의 설치 및 사업의 개시 의무) / '
                      '제61조(공사계획의 인가·신고) / 제63조(사용전검사)',
         confidence='MEDIUM',
         source_url='https://www.law.go.kr',
         note='제7조·제9조 원문에서 "기후에너지환경부장관" 표기 확인. '
              '3,000kW 소관 구분의 시행령 조번호는 미확인.'),
    dict(name='국토의 계획 및 이용에 관한 법률', category='국토',
         purpose='개발행위허가 및 용도지역별 행위제한의 근거',
         key_articles='제56조(개발행위의 허가) / 제76조(용도지역에서의 건축물의 건축 제한 등) / '
                      '시행령 제55조·제57조, 별표1의2',
         confidence='MEDIUM', source_url='https://www.law.go.kr',
         note='용도지역별 허가 면적 상한 및 도시계획위원회 심의 요건 수치는 미확인.'),
    dict(name='산지관리법', category='산림',
         purpose='산지전용허가·산지일시사용허가, 보전산지 행위제한, 경사도·표고 기준',
         key_articles='제4조(산지의 구분) / 제9조(산지전용·일시사용제한지역) / '
                      '제12조(보전산지에서의 행위제한) / 제14조(산지전용허가) / '
                      '제15조의2(산지일시사용허가·신고) / 시행령 별표3의2, 별표4',
         confidence='MEDIUM', source_url='https://www.law.go.kr',
         note='풍력이 산지일시사용허가 대상인지, 평균경사도 25도 기준이 풍력에도 예외 없이 '
              '적용되는지는 원문 미확인.'),
    dict(name='환경영향평가법', category='환경',
         purpose='환경영향평가 / 소규모환경영향평가 대상 판단 및 협의 절차',
         key_articles='시행령 별표3(환경영향평가 대상사업) / 별표4(소규모환경영향평가 대상사업)',
         confidence='LOW', source_url='https://www.law.go.kr',
         note='★ 최우선 검증 대상. 풍력 항목의 법정 규모 기준선을 확인하지 못함.'),
    dict(name='자연환경보전법', category='환경',
         purpose='생태·자연도 등급 및 생태·경관보전지역 행위제한',
         key_articles='제15조(생태·경관보전지역에서의 행위제한) / 제34조(생태·자연도의 작성·활용)',
         confidence='MEDIUM', source_url='https://www.law.go.kr'),
    dict(name='육상풍력 개발사업 환경성평가 지침', category='환경',
         purpose='육상풍력 고유의 환경성 검토 기준(생태자연도 등급별 회피, 소음 등)',
         key_articles='생태·자연도 1등급 회피 원칙 및 예외 / 야간 소음 기준',
         confidence='MEDIUM',
         source_url='https://me.go.kr/home/web/policy_data/read.do?menuId=10261&seq=7177',
         note='환경부 예규. 2020.1.7 제정, 2022.1.4 개정으로 1등급 예외 사유 추가. 원문(hwp) 미대조.'),
    dict(name='문화유산의 보존 및 활용에 관한 법률', category='문화재',
         purpose='역사문화환경 보존지역 내 현상변경 허가',
         key_articles='제13조(역사문화환경 보존지역의 보호)',
         confidence='MEDIUM', source_url='https://www.law.go.kr',
         note='지정유산 외곽 500m 원칙 및 시·도 조례 위임 구조. 원문 미대조.'),
    dict(name='매장유산 보호 및 조사에 관한 법률', category='문화재',
         purpose='매장유산 지표조사 대상 판단',
         key_articles='시행령 제4조(매장유산 지표조사의 대상 사업 등)',
         confidence='MEDIUM', source_url='https://www.law.go.kr',
         note='구 「매장문화재 보호 및 조사에 관한 법률」에서 법령명 개정. 대상 면적 기준 미확인.'),
    dict(name='군사기지 및 군사시설 보호법', category='군사',
         purpose='보호구역·비행안전구역 협의 및 표면높이 제한',
         key_articles='제10조(비행안전구역에서의 금지·제한) / 제13조(행정기관의 처분등에 관한 협의)',
         confidence='MEDIUM', source_url='https://www.law.go.kr'),
    dict(name='백두대간 보호에 관한 법률', category='산림',
         purpose='백두대간 핵심·완충구역 행위제한 및 재생에너지시설 예외',
         key_articles='제7조(행위 제한) — 제1항제6호에 재생에너지시설 예외 규정 존재로 조사됨',
         confidence='MEDIUM', source_url='https://www.law.go.kr',
         note='재생에너지시설 예외의 시행령상 세부 설치조건 미확인.'),
    dict(name='자연공원법', category='환경', purpose='국립·도립·군립공원 내 행위허가',
         key_articles='제23조(공원구역에서의 행위 제한)',
         confidence='MEDIUM', source_url='https://www.law.go.kr'),
    dict(name='습지보전법', category='환경', purpose='습지보호지역 내 행위 제한',
         key_articles='제13조(행위 제한) / 시행령 제13조(금지행위의 예외)',
         confidence='MEDIUM', source_url='https://www.law.go.kr'),
    dict(name='농지법', category='국토', purpose='부지에 농지 포함 시 농지전용허가·협의',
         key_articles='제32조(용도구역에서의 행위 제한) / 제34조(농지의 전용허가·협의)',
         confidence='MEDIUM', source_url='https://www.law.go.kr'),
    dict(name='환경정책기본법', category='환경', purpose='소음 환경기준',
         key_articles='제12조(환경기준의 설정) 및 시행령 별표 — '
                      '가지역 낮50/밤40, 나지역 낮55/밤45, 다지역 낮65/밤55, 라지역 낮70/밤65 dB(A)',
         confidence='HIGH', source_url='https://www.noiseinfo.or.kr/inform/standard.do'),
    dict(name='신에너지 및 재생에너지 개발·이용·보급 촉진법', category='전기',
         purpose='REC 발급 근거. 시행령 개정으로 전국 표준 이격거리 도입 예정',
         key_articles='공급인증서 발급 관련 조항(조번호 미확인)',
         confidence='LOW', source_url='https://www.law.go.kr',
         note='★ 2026-07-21~08-03 입법예고된 시행령 개정안: 풍력 최소 이격거리 하한을 '
              '발전기 높이의 2배, 상한을 주거지역 최대 1,500m·도로 최대 500m 범위에서 '
              '조례로 정하도록 하는 내용. 2026-09-18 시행 예정으로 보도되었으나 미확정. '
              '확정 시 다수 지자체 조례 재개정 예상 — 반드시 추적 필요.'),
    dict(name='해상풍력 보급 촉진 및 산업 육성에 관한 특별법', category='전기',
         purpose='해상풍력 전용 계획입지 제도 — 육상풍력에는 적용되지 않음',
         key_articles='발전지구 지정, 민관협의회, 실시계획 승인',
         confidence='HIGH', source_url='https://law.go.kr/LSW/lsInfoP.do?lsiSeq=270173',
         note='2025-03-25 공포, 2026-03-26 시행. 육상풍력은 적용 대상이 아니므로 '
              '기존 개별 인허가 체계를 그대로 따른다.'),
]


# ======================================================================
# 2. 입지 판정 규칙
# ======================================================================
RULES = [
    # --- 생태자연도 ---
    ('생태자연도', '1', '생태·자연도 1등급 권역 포함', 'CONDITIONAL', 'CRITICAL',
     '생태·자연도 1등급 권역은 개발사업 입지를 원칙적으로 지양합니다. 다만 이미 자연 상태를 '
     '벗어난 인공조림지이거나, 1등급 회피가 오히려 더 큰 산림 훼손을 유발하는 등 불가피성이 '
     '인정되면 예외적으로 사업대상지에 포함될 수 있습니다(2022.1.4 개정 지침).',
     '자연환경보전법 / 육상풍력 개발사업 환경성평가 지침', '제34조', 'MEDIUM',
     'https://www.khan.co.kr/environment/environment-general/article/202201031529001'),
    ('생태자연도', '2', '생태·자연도 2등급 권역 포함', 'CONDITIONAL', 'HIGH',
     '생태·자연도 2등급 권역이 포함되어 환경성 협의 과정에서 보전·저감 방안 제시가 요구됩니다. '
     '지침상 구체적 허용 요건은 원문 미확인 상태입니다.',
     '자연환경보전법', '제34조', 'LOW', ''),
    ('생태자연도', '3', '생태·자연도 3등급 권역', 'POSSIBLE', 'LOW',
     '생태·자연도 3등급 권역으로 개발과 보전의 조화가 가능한 지역으로 분류됩니다.',
     '자연환경보전법', '제34조', 'LOW', ''),
    ('생태자연도', '별도', '별도관리지역 포함', 'CONDITIONAL', 'HIGH',
     '별도관리지역(자연공원·습지보호지역·백두대간보호지역 등)은 해당 개별법의 행위제한이 '
     '우선 적용됩니다.', '자연환경보전법', '제34조', 'LOW', ''),

    # --- 산지 구분 ---
    ('산지구분', '준보전산지', '준보전산지', 'POSSIBLE', 'LOW',
     '준보전산지는 보전산지 외의 산지로 별도 행위제한이 없어 일반 산지 인허가 절차로 진행 가능합니다.',
     '산지관리법', '제4조·제14조', 'MEDIUM', ''),
    ('산지구분', '임업용산지', '보전산지 중 임업용산지', 'CONDITIONAL', 'MEDIUM',
     '임업용산지는 법령에 열거된 시설에 한해 산지전용·일시사용허가가 가능합니다. '
     '풍력발전시설의 해당 여부는 시행령 별표 원문 확인이 필요합니다.',
     '산지관리법', '제12조', 'MEDIUM', ''),
    ('산지구분', '공익용산지', '보전산지 중 공익용산지', 'CONDITIONAL', 'HIGH',
     '공익용산지는 임업용산지보다 행위제한이 엄격하며 법령에 열거된 예외적 행위만 허용됩니다.',
     '산지관리법', '제12조제2항', 'MEDIUM', ''),
    ('산지구분', '산지전용제한지역', '산지전용·일시사용제한지역', 'UNKNOWN', 'CRITICAL',
     '산지전용·일시사용제한지역은 원칙적으로 전용이 제한되며 시행령에 열거된 예외시설만 '
     '허용됩니다. 풍력(신재생에너지)시설이 예외 목록에 포함되는지 확인하지 못했습니다.',
     '산지관리법', '제9조', 'LOW', ''),

    # --- 백두대간 ---
    ('백두대간', '핵심구역', '백두대간 핵심구역', 'CONDITIONAL', 'CRITICAL',
     '핵심구역은 건축·인공구조물 설치·토지형질변경이 원칙 금지되나, 법률상 예외 행위에 '
     '재생에너지시설이 포함된 것으로 조사되었습니다. 시행령상 세부 설치조건 확인이 필요합니다.',
     '백두대간 보호에 관한 법률', '제7조제1항제6호', 'MEDIUM', ''),
    ('백두대간', '완충구역', '백두대간 완충구역', 'CONDITIONAL', 'HIGH',
     '완충구역은 핵심구역 허용행위에 더해 추가 행위가 허용되어 상대적으로 완화된 규제가 적용됩니다.',
     '백두대간 보호에 관한 법률', '제7조제2항', 'MEDIUM', ''),

    # --- 산사태 ---
    ('산사태위험등급', '1', '산사태위험 1등급지 포함', 'CONDITIONAL', 'HIGH',
     '산사태위험 1등급지는 산지 인허가 심사에서 재해영향 검토가 강화되어 실무상 허가가 '
     '어려운 경우가 많습니다. 이를 명시적으로 금지하는 단일 조문은 확인하지 못했습니다.',
     '산지관리법', '시행령 별표4(추정)', 'LOW', ''),
    ('산사태위험등급', '2', '산사태위험 2등급지 포함', 'CONDITIONAL', 'MEDIUM',
     '산사태위험 2등급지로 사면 안정성 검토 및 재해저감 대책 수립이 요구됩니다.',
     '산지관리법', '', 'LOW', ''),

    # --- 보호구역 ---
    ('보호구역', '자연공원', '국립·도립·군립공원 구역', 'CONDITIONAL', 'CRITICAL',
     '공원구역 내 공작물 설치·토지형질변경은 공원관리청 행위허가 대상이며, 대형 풍력발전시설의 '
     '허가 사례는 매우 드뭅니다. 법률상 절대 금지는 아니나 사실상 최고 난이도입니다.',
     '자연공원법', '제23조', 'MEDIUM', ''),
    ('보호구역', '습지보호지역', '습지보호지역', 'CONDITIONAL', 'CRITICAL',
     '습지보호지역 내 공작물 설치·습지훼손은 원칙 금지되며 시행령상 예외에 한해 허용됩니다. '
     '대형 발전시설의 예외 해당 가능성은 매우 낮습니다.',
     '습지보전법', '제13조', 'MEDIUM', ''),
    ('보호구역', '생태경관보전지역', '생태·경관보전지역', 'CONDITIONAL', 'CRITICAL',
     '핵심·완충·전이구역별로 행위가 제한되며 관리청 허가가 필요합니다. 핵심구역일수록 '
     '허가 가능성이 낮습니다.', '자연환경보전법', '제15조', 'MEDIUM', ''),
    ('보호구역', '야생생물보호구역', '야생생물보호구역', 'CONDITIONAL', 'HIGH',
     '보호구역 내 서식지 훼손·시설물 설치가 제한되며 관리청 허가를 통한 예외적 행위만 '
     '가능합니다. 정확한 행위제한 조문은 미확인입니다.',
     '야생생물 보호 및 관리에 관한 법률', '', 'LOW', ''),

    # --- 문화재 ---
    ('국가유산', '역사문화환경보존지역', '역사문화환경 보존지역 내', 'CONDITIONAL', 'HIGH',
     '역사문화환경 보존지역은 지정유산 외곽경계로부터 원칙적으로 500m 이내이며, 시·도 조례로 '
     '축소·확대될 수 있습니다. 해당 구역 내 개발행위는 현상변경 허가 대상입니다.',
     '문화유산의 보존 및 활용에 관한 법률', '제13조', 'MEDIUM', ''),

    # --- 군사 ---
    ('군사', '보호구역', '군사기지·군사시설 보호구역', 'CONDITIONAL', 'HIGH',
     '보호구역 내 일정 표면높이를 초과하는 시설물은 관할부대장과 사전 협의가 필요하며, '
     '관할부대심의위원회가 동의/조건부 동의/부동의를 결정합니다.',
     '군사기지 및 군사시설 보호법', '제13조', 'MEDIUM',
     'https://www.asi.or.kr/aamri/obs_evaluation.jsp'),
    ('군사', '비행안전구역', '비행안전구역(제1~6구역)', 'CONDITIONAL', 'HIGH',
     '표면높이 제한 초과 시 비행안전영향평가 및 관할부대심의위원회 심의를 거쳐야 하며, '
     '레이더 전파영향도 관할부대가 검토합니다.',
     '군사기지 및 군사시설 보호법', '제10조', 'MEDIUM',
     'https://www.asi.or.kr/aamri/obs_evaluation.jsp'),

    # --- 소음 ---
    ('소음', '야간주거지', '가장 가까운 주거지 야간 소음 예측', 'CONDITIONAL', 'CRITICAL',
     '「육상풍력 개발사업 환경성평가 지침」은 야간(22:00~05:00) 인근 주거지역 소음기준을 '
     '45dB(A) 이하로 두는 것으로 조사되었습니다. 초과 예측 시 배치 조정·저소음 기종 등 '
     '저감방안이 요구됩니다.',
     '육상풍력 개발사업 환경성평가 지침', '', 'MEDIUM',
     'https://www.greenpostkorea.co.kr/news/articleView.html?idxno=60196'),

    # --- 경사도 ---
    ('경사도', '평균경사도', '산지전용 대상지 평균경사도', 'CONDITIONAL', 'HIGH',
     '산지관리법 시행령 별표4상 평균경사도 기준(일반적으로 25도로 알려짐)을 초과하면 '
     '산지 인허가가 제한될 수 있습니다. 다만 이 수치와 풍력 적용 여부는 원문 대조를 '
     '완료하지 못했습니다.', '산지관리법', '시행령 별표4', 'LOW', ''),

    # --- 풍황 ---
    ('풍황', '연평균풍속', '허브고도 연평균 풍속', 'UNKNOWN', 'MEDIUM',
     '법령상 최소 풍속 규제 기준은 존재하지 않습니다(사업성 판단 영역). 국내 육상풍력단지 '
     '분석에서 대부분 연평균 6m/s 이상 지역에 입지한다는 보고가 있으나 이는 업계 참고치입니다.',
     '해당 없음', '', 'LOW', 'https://www.jccr.re.kr/xml/24626/24626.pdf'),
]


# ======================================================================
# 3. 지자체 이격거리 조례 (사례)
# ======================================================================
ORDINANCES = [
    dict(sido='전라남도', sigungu='화순군', energy_type='WIND', target='RESIDENTIAL',
         target_detail='10가구 이상 마을', distance_m=1200,
         ordinance_name='화순군 도시계획 조례', article='제20조의2',
         exemption='이격거리 내 거주 주민 일정 비율의 동의를 얻는 경우 적용 배제·완화 규정 존재',
         difficulty='HIGH', confidence='LOW',
         source_url='https://www.mdilbo.com/detail/k7XJJN/615828',
         note='신설 당시 2km였으나 개정으로 1.2km로 완화된 것으로 보도됨. 개정 이력이 있어 '
              '현행 조례 원문 재확인 필수.'),
    dict(sido='전라남도', sigungu='화순군', energy_type='WIND', target='RESIDENTIAL',
         target_detail='10가구 미만 마을', distance_m=800,
         ordinance_name='화순군 도시계획 조례', article='제20조의2',
         difficulty='HIGH', confidence='LOW',
         source_url='https://www.mdilbo.com/detail/k7XJJN/615828',
         note='개정 전 1.5km → 800m로 완화된 것으로 보도됨. 재확인 필수.'),
    dict(sido='전라남도', sigungu='해남군', energy_type='WIND', target='QUIET_FACILITY',
         target_detail='정온시설', distance_m=1000,
         ordinance_name='해남군 군계획 조례', article='(조문 미확인)',
         difficulty='HIGH', confidence='LOW',
         source_url='https://www.electimes.com/news/articleView.html?idxno=341370',
         note='개정 전 1,500m → 1,000m로 완화 보도. 시행일 기준 현행 조례 재확인 필요.'),
    dict(sido='전라남도', sigungu='해남군', energy_type='WIND', target='RESIDENTIAL',
         target_detail='자연취락지구·주거밀집지', distance_m=700,
         ordinance_name='해남군 군계획 조례', article='(조문 미확인)',
         difficulty='HIGH', confidence='LOW',
         source_url='https://www.electimes.com/news/articleView.html?idxno=341370',
         note='개정 전 1,000m → 700m로 완화 보도. 재확인 필요.'),
    dict(sido='경상북도', sigungu='청도군', energy_type='WIND', target='ROAD',
         target_detail='도로·철도', distance_m=2000,
         ordinance_name='청도군 군계획 조례', article='(조문 미확인)',
         difficulty='CRITICAL', confidence='LOW',
         source_url='https://www.electimes.com/news/articleView.html?idxno=341370',
         note='1,000m → 2,000m로 강화 보도. 전국 최고 수준의 엄격한 기준으로 알려짐.'),
    dict(sido='경상북도', sigungu='청도군', energy_type='WIND', target='RESIDENTIAL',
         target_detail='주거지', distance_m=1500,
         ordinance_name='청도군 군계획 조례', article='(조문 미확인)',
         difficulty='CRITICAL', confidence='LOW',
         source_url='https://www.electimes.com/news/articleView.html?idxno=341370',
         note='500m → 1,500m로 강화 보도. 재확인 필요.'),
]


# ======================================================================
# 4. 인허가 절차
# ======================================================================
STEPS = [
    dict(order=10, phase='DEV', name='입지 발굴·사업타당성 검토 및 풍황계측',
         authority='자체 수행 (계측기 설치 시 토지주·지자체 협의)',
         law='', article='', statutory_days=None, depends_on=[],
         conditional_on='', confidence='MEDIUM',
         note='법정 인허가는 아님. 통상 1년 이상 풍황계측이 권장되나 법정 의무는 아님. '
              '국공유림 계측기 설치 시 사용허가 필요.'),
    dict(order=20, phase='PERMIT', name='발전사업허가',
         authority='기후에너지환경부장관(3,000kW 초과) / 시·도지사(3,000kW 이하)',
         law='전기사업법', article='제7조', statutory_days=60,
         depends_on=['입지 발굴·사업타당성 검토 및 풍황계측'],
         capacity_rule='3,000kW 초과: 기후에너지환경부장관 허가(전기위원회 심의). '
                       '3,000kW 이하: 시·도지사 허가.',
         conditional_on='', confidence='MEDIUM',
         source_url='https://recloud.energy.or.kr/process/sub1_5_1_1.do',
         note='2025-10-01 기후에너지환경부 출범으로 에너지 인허가 기능 이관. '
              '3,000kW 기준의 시행령 조번호는 미확인.'),
    dict(order=30, phase='PERMIT', name='환경영향평가 협의',
         authority='기후에너지환경부(유역·지방환경청)',
         law='환경영향평가법', article='제29조 · 시행령 제50조(협의 내용의 통보기간)',
         statutory_days=45, statutory_basis='',
         depends_on=['발전사업허가'], conditional_on='EIA', confidence='HIGH',
         note='협의기관이 협의 내용을 통보하는 기간이 45일이다(부득이한 사유로 연장 시 60일). '
              '평가서 보완기간·전문위원회 검토기간(최장 45일)·공휴일은 산입하지 않으므로 '
              '실제 소요는 이보다 길다. 대상 여부를 가르는 별표3의 풍력 규모 기준선은 미확인.'),
    dict(order=31, phase='PERMIT', name='소규모환경영향평가 협의',
         authority='유역·지방환경청장',
         law='환경영향평가법', article='제45조 · 시행령 제62조(협의 내용의 통보기간)',
         statutory_days=30, statutory_basis='',
         depends_on=['발전사업허가'], conditional_on='SMALL_EIA', confidence='HIGH',
         note='협의 내용 통보기간 30일(연장 시 40일). 시행령 제60조제2항의 소규모 개발사업이면 '
              '20일(연장 시 30일)이다. 보완기간·전문위원회 검토기간·공휴일은 산입하지 않는다. '
              '대상 여부를 가르는 별표4 기준선은 미확인.'),
    dict(order=40, phase='PERMIT', name='재해영향평가 협의',
         authority='행정안전부 / 시·도',
         law='자연재해대책법', article='(조문 미확인)', statutory_days=None, statutory_basis='UNKNOWN',
         depends_on=['발전사업허가'], conditional_on='', confidence='LOW',
         note='대상 규모·조문 미확인. 산사태위험지 포함 시 중요도 상승.'),
    dict(order=50, phase='PERMIT', name='산지전용허가 또는 산지일시사용허가',
         authority='면적별 산림청장 / 시·도지사 / 시장·군수·구청장',
         law='산지관리법', article='제14조 · 제15조의2', statutory_days=None,
         statutory_basis='NONE',
         depends_on=['환경영향평가 협의'], conditional_on='FOREST', confidence='MEDIUM',
         note='법·시행령·시행규칙 원문을 대조했으나 **허가 처리기간 규정이 없다**(법 제14조제2항의 '
              '25일은 변경신고 수리 통지 기간이지 본 허가 기간이 아니다). 실무 처리기간은 '
              '민원처리 기준에 따른다. 발전시설은 사용기간 종료 후 원상복구를 전제로 '
              '산지일시사용허가 대상으로 파악되나 시행령 별표3의2 원문은 미확인.'),
    dict(order=51, phase='PERMIT', name='농지전용허가·협의',
         authority='농림축산식품부장관 / 시·도지사 / 시장·군수·구청장 (면적별)',
         law='농지법', article='제34조 · 시행령 제33조제1항', statutory_days=None,
         statutory_basis='NONE',
         depends_on=['환경영향평가 협의'], conditional_on='FARMLAND', confidence='HIGH',
         note='법·시행령·시행규칙에 **전체 처리기간 규정이 없다.** 시행령 제33조제1항이 정한 것은 '
              '경유기관 기간뿐이다 — 시장·군수가 10일 이내에 시·도지사에게, 시·도지사가 다시 '
              '10일 이내에 장관에게 보낸다. 최종 허가권자의 심사기간은 규정되어 있지 않다.'),
    dict(order=52, phase='PERMIT', name='초지전용허가',
         authority='시·도지사', law='초지법', article='제23조제6항', statutory_days=35,
         statutory_basis='',
         depends_on=['환경영향평가 협의'], conditional_on='GRASSLAND', confidence='HIGH',
         note='허가 신청 또는 신고를 받은 날부터 35일 이내에 허가·신고수리 여부를 통지한다.'),
    dict(order=53, phase='PERMIT', name='사방지 지정해제',
         authority='산림청장 / 시·도지사', law='사방사업법', article='(조번호 미확인)',
         statutory_days=None, statutory_basis='UNKNOWN', depends_on=['산지전용허가 또는 산지일시사용허가'],
         conditional_on='FOREST', confidence='LOW', note='조문번호 미확인.'),
    dict(order=60, phase='PERMIT', name='매장유산 지표조사',
         authority='국가유산청 (조사기관 의뢰)',
         law='매장유산 보호 및 조사에 관한 법률', article='시행령 제4조', statutory_days=None, statutory_basis='UNKNOWN',
         depends_on=['발전사업허가'], conditional_on='HERITAGE', confidence='MEDIUM',
         note='대상 사업 면적 기준 미확인. 통상 일정 면적 이상 개발사업이 대상.'),
    dict(order=61, phase='PERMIT', name='군 협의 (작전성 검토·비행안전영향·전파영향)',
         authority='국방부장관 또는 관할부대장 (지자체 경유)',
         law='군사기지 및 군사시설 보호법', article='제10조 · 제13조', statutory_days=None, statutory_basis='UNKNOWN',
         depends_on=['발전사업허가'], conditional_on='MILITARY', confidence='MEDIUM',
         note='풍력은 높이가 커 표면높이 제한·레이더 간섭 검토 대상이 되는 경우가 많음.'),
    dict(order=70, phase='PERMIT', name='개발행위허가',
         authority='시장·군수·구청장 (특별시장·광역시장·특별자치시·도지사 포함)',
         law='국토의 계획 및 이용에 관한 법률',
         article='제56조 · 제57조제2항 · 시행령 제54조제1항', statutory_days=15,
         statutory_basis='',
         depends_on=['산지전용허가 또는 산지일시사용허가'], conditional_on='',
         confidence='HIGH',
         note='허가·불허가 처분 기간이 15일이다. 다만 **도시계획위원회 심의를 거치거나 관계 '
              '행정기관과 협의해야 하는 경우 그 기간은 제외**되므로(시행령 제54조제1항 괄호) '
              '풍력처럼 심의·협의가 따르는 사업의 실제 소요는 훨씬 길다. '
              '다수 지자체가 조례로 풍력 이격거리 기준을 개발행위허가 기준에 포함.'),
    dict(order=71, phase='PERMIT', name='도로점용허가',
         authority='도로관리청', law='도로법', article='제61조', statutory_days=None,
         statutory_basis='NONE',
         depends_on=['개발행위허가'], conditional_on='ROAD', confidence='HIGH',
         note='법·시행령·시행규칙 원문을 대조했으나 점용허가 처리기간 규정이 없다. '
              '실무 처리기간은 도로관리청의 민원처리 기준에 따른다. 진입로·전력구 등 도로 점용 시.'),
    dict(order=80, phase='PERMIT', name='송전용 전기설비 이용계약',
         authority='한국전력공사',
         law='송·배전용 전기설비 이용규정', article='', statutory_days=None, statutory_basis='UNKNOWN',
         depends_on=['발전사업허가'], conditional_on='', confidence='LOW',
         note='계통 접속 가능 용량 확인 및 이용계약 체결. 실무상 발전사업허가와 병행.'),
    dict(order=90, phase='BUILD', name='공사계획 인가 또는 신고',
         authority='기후에너지환경부장관(인가) / 시·도지사(신고)',
         law='전기사업법', article='제61조', statutory_days=None, statutory_basis='UNKNOWN',
         depends_on=['개발행위허가', '산지전용허가 또는 산지일시사용허가'],
         capacity_rule='10,000kW 이상 인가 / 10,000kW 미만 신고로 안내되나 단일 출처만 확인됨.',
         conditional_on='', confidence='LOW',
         source_url='https://recloud.energy.or.kr/process/sub1_5_3.do',
         note='★ 10,000kW 기준 및 조문 원문 교차검증 필요.'),
    dict(order=100, phase='BUILD', name='전기안전관리자 선임',
         authority='한국전기안전공사 (신고 접수)',
         law='전기안전관리법', article='제22조', statutory_days=None,
         statutory_basis='NONE',
         depends_on=['공사계획 인가 또는 신고'], conditional_on='', confidence='MEDIUM',
         note='선임 신고 절차로, 법 원문에 처리기간 규정이 없다.'),
    dict(order=110, phase='BUILD', name='착공 및 시공',
         authority='—', law='', article='', statutory_days=None,
         depends_on=['공사계획 인가 또는 신고'], conditional_on='', confidence='MEDIUM'),
    dict(order=120, phase='BUILD', name='사용전검사',
         authority='기후에너지환경부장관 또는 시·도지사 (한국전기안전공사 위탁 수행)',
         law='전기사업법', article='제63조', statutory_days=None, statutory_basis='UNKNOWN',
         depends_on=['착공 및 시공'], conditional_on='', confidence='MEDIUM',
         note='조문 원문 확인. 다만 최신 개정본의 소관 부처 표기는 재확인 필요.'),
    dict(order=130, phase='OPS', name='사업개시신고',
         authority='허가권자 (기후에너지환경부장관 또는 시·도지사)',
         law='전기사업법', article='제9조제4항', statutory_days=30,
         depends_on=['사용전검사'], conditional_on='', confidence='HIGH',
         note='발전사업자는 최초 전력거래일부터 30일 이내 신고 (심사기간이 아닌 신고기한).'),
    dict(order=140, phase='OPS', name='REC 발급 신청 및 전력거래 개시',
         authority='한국에너지공단 신재생에너지센터 / 한국전력거래소',
         law='신에너지 및 재생에너지 개발·이용·보급 촉진법', article='(조번호 미확인)',
         statutory_days=None, statutory_basis='UNKNOWN', depends_on=['사업개시신고'], conditional_on='',
         confidence='LOW'),
]


class Command(BaseCommand):
    help = '풍력 입지·인허가 기준 시드 데이터를 입력합니다.'

    def handle(self, *args, **options):
        n_law = n_rule = n_ord = n_step = 0

        for d in LAWS:
            LawReference.objects.update_or_create(
                # ⚠️ 에너지원까지 함께 키로 잡는다. LawReference는 발전원별로
                #    따로 두는 레코드인데(같은 전기사업법이라도 풍력과 태양광의
                #    소관·용량 경계가 다르다) 종전에는 name만으로 찾아, 태양광
                #    시드가 같은 이름을 넣은 뒤로는 이 시드가 통째로 죽었다
                #    (MultipleObjectsReturned). seed_solar와 같은 규약으로 맞춘다.
                energy_type='WIND', name=d['name'],
                defaults={**d, 'verified_at': TODAY if d['confidence'] == 'HIGH' else None},
            )
            n_law += 1

        for (layer, key, desc, status, diff, reason, law, article, conf, url) in RULES:
            RegulationRule.objects.update_or_create(
                layer=layer, condition_key=key,
                defaults=dict(condition_desc=desc, status=status, difficulty=diff,
                              reason_template=reason, law=law, article=article,
                              confidence=conf, source_url=url,
                              verified_at=TODAY if conf == 'HIGH' else None),
            )
            n_rule += 1

        for d in ORDINANCES:
            LocalOrdinance.objects.update_or_create(
                sido=d['sido'], sigungu=d['sigungu'], energy_type=d['energy_type'],
                target=d['target'], target_detail=d.get('target_detail', ''),
                defaults={k: v for k, v in d.items()
                          if k not in ('sido', 'sigungu', 'energy_type', 'target', 'target_detail')},
            )
            n_ord += 1

        for d in STEPS:
            PermitStep.objects.update_or_create(
                # LawReference와 같은 이유로 에너지원을 키에 넣는다. 지금은
                # 태양광이 800번대를 써서(seed_solar.ORDER_BASE) 번호가 겹치지
                # 않지만, 그 규약은 주석 한 줄로만 지켜지고 있어 언제든 깨진다.
                energy_type='WIND', order=d['order'],
                defaults={k: v for k, v in d.items() if k != 'order'},
            )
            n_step += 1

        self.stdout.write(self.style.SUCCESS(
            f'시드 완료 — 법령 {n_law}건 · 판정규칙 {n_rule}건 · 조례 {n_ord}건 · 인허가절차 {n_step}건'
        ))
        low = (LawReference.objects.filter(confidence='LOW').count()
               + RegulationRule.objects.filter(confidence='LOW').count()
               + LocalOrdinance.objects.filter(confidence='LOW').count()
               + PermitStep.objects.filter(confidence='LOW').count())
        self.stdout.write(self.style.WARNING(
            f'⚠️ 미검증(LOW) 레코드 {low}건 — 실무 적용 전 국가법령정보센터에서 원문 대조가 필요합니다.'
        ))
