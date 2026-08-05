"""
사업 Fact-sheet 입력 스키마 — 단일 진실 공급원(Single Source of Truth)

프론트엔드 입력 폼과 RAG 적재용 마크다운 생성기가 **이 파일 하나**를 공유한다.
항목을 여기서 추가/수정하면 화면과 마크다운이 동시에 갱신되므로 두 곳이 어긋날 수 없다.

설계 근거 (사업개요 양식 v2 / 사내 코퍼스 실측):
  - 섹션 제목마다 사업명을 반복한다 → 청킹 후에도 "어느 사업 수치인지" 식별 가능
  - 별칭(alias)을 명시한다 → 문서명만으로 사업 판정이 안 되는 케이스 대응
  - 확정도(확정/예상/미정/해당없음)를 표기한다 → 예상치가 확정 사실처럼 답변되는 것을 차단
  - 빈칸은 '미확인'으로 출력한다 → LLM이 빈칸을 추측으로 메우는 것을 차단
  - ⭐(star) 항목이 최우선 입력 대상이다

필드 타입
  text | textarea | number | date | select | table

필드 속성
  key      : 데이터 저장 키 (섹션 내 유일)
  label    : 화면/마크다운 표기 항목명
  type     : 위 타입 중 하나
  unit     : number 타입의 단위 표기 (MW, 억원, 원/kWh …)
  options  : select 타입의 선택지
  hint     : 입력 도움말 (마크다운 '비고' 열에도 사용)
  star     : True 면 ⭐ 핵심 항목
  status   : True 면 확정도 토글 노출
  columns  : table 타입의 열 정의
  rows     : table 타입의 기본 행 (마일스톤처럼 목록이 정해진 경우)
"""

STAGES = [
    {'key': 'dev', 'no': 'STAGE 1', 'title': '개발', 'desc': '부지확보 · 인허가 · 계통 · 전력판매'},
    {'key': 'build', 'no': 'STAGE 2', 'title': '건설', 'desc': 'EPC · 금융종결 · 착공~준공'},
    {'key': 'ops', 'no': 'STAGE 3', 'title': '운영', 'desc': 'COD · O&M · 발전/수익 실적'},
]

CONFIDENCE_CHOICES = ['확정', '예상', '미정', '미확인', '해당없음']

SIDO_CHOICES = [
    '서울특별시', '부산광역시', '대구광역시', '인천광역시', '광주광역시', '대전광역시',
    '울산광역시', '세종특별자치시', '경기도', '강원특별자치도', '충청북도', '충청남도',
    '전북특별자치도', '전라남도', '경상북도', '경상남도', '제주특별자치도',
]

_PERMIT_STATUS = ['신청 전', '신청 중', '허가 완료', '조건부 허가', '해당없음']
_CONSULT_STATUS = ['미착수', '진행 중', '협의 완료', '해당없음']
_CONTRACT_STATUS = ['미체결', '협상 중', '체결 완료', '해지', '해당없음']
_MILESTONE_STATUS = ['예정', '진행중', '완료', '지연', '해당없음']


SECTIONS = [
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'basic', 'no': 1, 'title': '사업 기본 정보', 'icon': '📋', 'stage': 'dev',
        'subtitle': 'SPC · 위치 · 설비용량 · 부지',
        # SPC 법인명·별칭은 문서 머리(모델 필드)에서 관리하며,
        # 마크다운 1번 섹션 표에 자동으로 합류한다.
        'fields': [
            {'key': 'ceo', 'label': '대표자', 'type': 'text'},
            {'key': 'hq_address', 'label': '본사 소재지', 'type': 'text'},
            {'key': 'sido', 'label': '사업 위치 (시·도)', 'type': 'select', 'options': SIDO_CHOICES, 'star': True},
            {'key': 'sigungu', 'label': '사업 위치 (시·군·구)', 'type': 'text', 'star': True},
            {'key': 'address', 'label': '사업장 상세 주소', 'type': 'text'},
            {'key': 'capacity_ac', 'label': '설비 용량 (AC)', 'type': 'number', 'unit': 'MW',
             'star': True, 'status': True},
            {'key': 'capacity_dc', 'label': '모듈 설치용량 (DC)', 'type': 'number', 'unit': 'MWp',
             'status': True, 'hint': 'AC와 다르면 반드시 구분해 기재'},
            {'key': 'voltage', 'label': '공급전압', 'type': 'number', 'unit': 'kV'},
            {'key': 'area_m2', 'label': '부지 면적', 'type': 'number', 'unit': '㎡', 'status': True},
            {'key': 'area_pyeong', 'label': '부지 면적 (평)', 'type': 'number', 'unit': '평'},
            {'key': 'land_type', 'label': '부지 유형', 'type': 'select',
             'options': ['육상', '임야', '농지', '염해간척지', '수상', '건물지붕', '기타']},
            {'key': 'current_stage', 'label': '현재 사업 단계', 'type': 'select', 'star': True,
             'options': ['개발', '인허가', '금융', '건설', '시운전', '운영']},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'land', 'no': 2, 'title': '부지 현황', 'icon': '📍', 'stage': 'dev',
        'subtitle': '확보 방식 · 소유자 · 계약 상태',
        'fields': [
            {'key': 'acquisition', 'label': '확보 방식', 'type': 'select', 'star': True, 'status': True,
             'options': ['매입', '임대', '사용허가', '혼합', '미정']},
            {'key': 'owner', 'label': '토지 소유자', 'type': 'text'},
            {'key': 'parcels', 'label': '필지 수 / 지목', 'type': 'text', 'placeholder': '예: 24필지 / 답·전'},
            {'key': 'contract_status', 'label': '부지 계약 상태', 'type': 'select', 'star': True,
             'options': ['미착수', '협상 중', '계약 체결', '등기 완료', '해당없음']},
            {'key': 'lease_terms', 'label': '계약 기간 / 임대료', 'type': 'text', 'status': True,
             'hint': '기산 시점 · 기간 · 단가 · 인상률 · 선급 여부'},
            {'key': 'zoning', 'label': '용도지역 · 지구', 'type': 'text'},
            {'key': 'land_use_change', 'label': '지목변경 필요 여부', 'type': 'select',
             'options': ['필요', '불필요', '검토 중', '해당없음']},
            {'key': 'obstacles', 'label': '지장물 · 분묘 · 관정', 'type': 'text'},
            {'key': 'adjacent_limit', 'label': '연접개발 제한 저촉', 'type': 'select',
             'options': ['저촉', '비저촉', '검토 중', '해당없음']},
            {'key': 'issues', 'label': '토지 관련 이슈', 'type': 'textarea'},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'structure', 'no': 3, 'title': '사업 구조 및 지분', 'icon': '🏢', 'stage': 'dev',
        'subtitle': '참여사 · 지분 · 주주간계약',
        'fields': [
            {'key': 'participants', 'label': '참여사 지분 구성', 'type': 'table', 'star': True,
             'columns': [
                 {'key': 'company', 'label': '참여사', 'type': 'text', 'width': '1fr'},
                 {'key': 'share', 'label': '지분율', 'type': 'number', 'unit': '%', 'width': '110px'},
                 {'key': 'role', 'label': '역할', 'type': 'select', 'width': '140px',
                  'options': ['시행', '재무투자', 'EPC', '운영', '기타']},
                 {'key': 'note', 'label': '비고', 'type': 'text', 'width': '1fr'},
             ]},
            {'key': 'dev_service', 'label': '개발용역계약', 'type': 'text', 'hint': '수행사 · 범위'},
            {'key': 'asset_transfer', 'label': '자산/사업 양수도', 'type': 'text', 'hint': '당사자 · 시점'},
            {'key': 'sha', 'label': '주주간계약(SHA)', 'type': 'textarea',
             'hint': '체결일 · 우선매수권 · 동반매도 · 이사회 구성'},
            {'key': 'spa_history', 'label': '지분 변동 이력 (SPA)', 'type': 'textarea',
             'hint': '매각 · 인수 일자와 상대방'},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'permit', 'no': 4, 'title': '인허가 현황', 'icon': '📄', 'stage': 'dev',
        'subtitle': '발전사업허가 · 개발행위허가 · 개별법 협의',
        'groups': [
            {'title': '발전사업허가', 'fields': [
                {'key': 'ba_status', 'label': '허가 상태', 'type': 'select', 'star': True, 'options': _PERMIT_STATUS},
                {'key': 'ba_number', 'label': '허가 번호', 'type': 'text', 'star': True},
                {'key': 'ba_applied', 'label': '신청일', 'type': 'date'},
                {'key': 'ba_approved', 'label': '허가일', 'type': 'date'},
                {'key': 'ba_capacity', 'label': '허가 용량', 'type': 'number', 'unit': 'MW', 'star': True,
                 'hint': '허가가 분할된 경우 각각 기재'},
                {'key': 'ba_conditions', 'label': '허가 조건', 'type': 'textarea',
                 'hint': '조건부허가면 조건 내용을 반드시 기재'},
                {'key': 'ba_period', 'label': '사업수행기간', 'type': 'text',
                 'hint': '공사계획인가기간 · 사업준비기간'},
            ]},
            {'title': '그 밖의 전기사업법 절차', 'fields': [
                {'key': 'ba_change', 'label': '발전사업 변경허가', 'type': 'text',
                 'hint': '변경 사유(용량 · 기간 · 주주)'},
                {'key': 'construction_approval', 'label': '공사계획인가', 'type': 'select',
                 'options': ['미착수', '신청 중', '인가 완료', '해당없음'], 'status': True},
                {'key': 'pre_use_inspection', 'label': '사용전검사', 'type': 'text',
                 'hint': '차수별로 기재 (발전 / 송전 N차)'},
                {'key': 'biz_start_report', 'label': '사업개시신고', 'type': 'text'},
                {'key': 'safety_manager', 'label': '전기안전관리자 선임', 'type': 'text'},
            ]},
            {'title': '개발행위허가 (국토계획법)', 'fields': [
                {'key': 'dev_status', 'label': '허가 상태', 'type': 'select', 'star': True, 'options': _PERMIT_STATUS},
                {'key': 'dev_applied', 'label': '신청일', 'type': 'date'},
                {'key': 'dev_approved', 'label': '허가일', 'type': 'date'},
                {'key': 'dev_conditions', 'label': '허가 조건', 'type': 'textarea'},
                {'key': 'dev_transmission', 'label': '개발행위허가 (송전선로)', 'type': 'text',
                 'hint': '부지와 별건으로 진행되는 경우가 많다'},
                {'key': 'city_committee', 'label': '도시계획위원회 심의', 'type': 'select',
                 'options': ['미해당', '상정', '조건부의결', '의결', '해당없음']},
                {'key': 'building_permit', 'label': '건축허가 · 신고', 'type': 'text', 'hint': '변전소 · 관리동'},
            ]},
            {'title': '개별법 협의', 'fields': [
                {'key': 'consultations', 'label': '개별법 협의 현황', 'type': 'table', 'star': True,
                 'hint': '해당 없는 항목도 지우지 말고 「해당없음」으로 남긴다 — 그 자체가 답이 된다',
                 'columns': [
                     {'key': 'name', 'label': '협의', 'type': 'text', 'width': '1.3fr'},
                     {'key': 'target', 'label': '대상여부', 'type': 'select', 'width': '110px',
                      'options': ['대상', '비대상', '검토 중']},
                     {'key': 'status', 'label': '상태', 'type': 'select', 'width': '110px',
                      'options': _CONSULT_STATUS},
                     {'key': 'date', 'label': '협의완료일', 'type': 'date', 'width': '140px'},
                     {'key': 'note', 'label': '조건 · 조치사항', 'type': 'text', 'width': '1.3fr'},
                 ],
                 'rows': [
                     {'name': '환경영향평가 (소규모 / 본안)'},
                     {'name': '재해영향평가 (자연재해대책법)'},
                     {'name': '토양 염도평가'},
                     {'name': '농지전용 / 타용도일시사용 (농지법)'},
                     {'name': '산지전용 / 일시사용 (산지관리법)'},
                     {'name': '공유수면 점용·사용 (공유수면법)'},
                     {'name': '경관심의 (경관법)'},
                     {'name': '매장문화재 지표조사 (매장유산법)'},
                     {'name': '도로점용 (도로법)'},
                     {'name': '하천점용 (하천법)'},
                     {'name': '군사시설보호구역 협의 (군사기지법)'},
                     {'name': '소방 동의 (소방시설법)'},
                 ]},
            ]},
            {'title': '인허가 종합', 'fields': [
                {'key': 'permit_summary', 'label': '현재 단계', 'type': 'text', 'star': True,
                 'placeholder': '예: 개발행위허가 완료, 착공 준비'},
                {'key': 'permit_remaining', 'label': '미완료 잔여 인허가', 'type': 'textarea', 'star': True},
                {'key': 'permit_risk', 'label': '일정 리스크가 있는 협의', 'type': 'textarea'},
            ]},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'community', 'no': 5, 'title': '주민 수용성', 'icon': '🤝', 'stage': 'dev',
        'subtitle': '동의율 · 설명회 · 이익공유',
        'fields': [
            {'key': 'consent_rate', 'label': '주민 동의율', 'type': 'number', 'unit': '%', 'status': True,
             'hint': '대상 세대수 / 동의 세대수'},
            {'key': 'briefing', 'label': '주민 설명회', 'type': 'text', 'hint': '개최일 · 참석 현황'},
            {'key': 'benefit_share', 'label': '이익공유 방안', 'type': 'textarea',
             'hint': '주민참여형 채권 · 펀드 / 마을발전기금'},
            {'key': 'resident_rec', 'label': '주민참여형 REC 가중치 적용', 'type': 'select', 'status': True,
             'options': ['적용', '미적용', '검토 중', '해당없음'],
             'hint': '적용 시 가중치가 올라가 수익에 직결된다'},
            {'key': 'acceptance_agency', 'label': '수용성 확보 용역사', 'type': 'text'},
            {'key': 'complaints', 'label': '민원 현황', 'type': 'textarea', 'hint': '유형 · 진행 상태'},
            {'key': 'local_gov', 'label': '지자체 협의 사항', 'type': 'textarea', 'hint': '지역기여 협약 등'},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'grid', 'no': 6, 'title': '계통 / 송전', 'icon': '⚡', 'stage': 'dev',
        'subtitle': '접속 절차 · 이용계약 · 송전선로',
        'groups': [
            {'title': '계통접속 절차', 'fields': [
                {'key': 'grid_steps', 'label': '접속 절차 진행', 'type': 'table',
                 'hint': '단계마다 공문이 남는다 — 공문번호를 적으면 챗봇이 원본을 짚어준다',
                 'columns': [
                     {'key': 'step', 'label': '단계', 'type': 'text', 'width': '1.2fr'},
                     {'key': 'status', 'label': '상태', 'type': 'select', 'width': '120px',
                      'options': ['미신청', '신청 중', '완료', '해당없음']},
                     {'key': 'date', 'label': '일자', 'type': 'date', 'width': '140px'},
                     {'key': 'doc_no', 'label': '공문번호 · 비고', 'type': 'text', 'width': '1.2fr'},
                 ],
                 'rows': [
                     {'step': '계통접속 신청'},
                     {'step': '접속제의서 수령'},
                     {'step': '접속제의 승낙'},
                     {'step': '송전용전기설비 이용계약 체결'},
                     {'step': '변경 이용계약'},
                     {'step': '이용개시조치 시행'},
                 ]},
            ]},
            {'title': '이용계약 내용', 'fields': [
                {'key': 'grid_counterparty', 'label': '계약 상대방', 'type': 'text', 'placeholder': '한국전력공사'},
                {'key': 'contract_power', 'label': '계약 전력', 'type': 'number', 'unit': 'MW', 'star': True,
                 'status': True, 'hint': '허가용량과 다를 수 있으니 반드시 별도 기재'},
                {'key': 'substation', 'label': '접속 변전소', 'type': 'text', 'star': True, 'status': True},
                {'key': 'poi', 'label': '계통연계 지점 / 접속설비', 'type': 'text'},
                {'key': 'grid_voltage', 'label': '공급전압', 'type': 'number', 'unit': 'kV'},
                {'key': 'grid_fee', 'label': '공사부담금', 'type': 'number', 'unit': '원', 'status': True,
                 'hint': '납부 여부 · 시기'},
                {'key': 'grid_period', 'label': '계약 기간', 'type': 'text'},
            ]},
            {'title': '송전선로 · 변전설비', 'fields': [
                {'key': 'tl_method', 'label': '송전 방식', 'type': 'select',
                 'options': ['가공', '지중', '혼합', '해당없음']},
                {'key': 'tl_distance', 'label': '송전 거리', 'type': 'number', 'unit': 'km', 'status': True},
                {'key': 'tl_spec', 'label': '전압 / 케이블 규격', 'type': 'text'},
                {'key': 'tl_route', 'label': '경과지', 'type': 'text', 'hint': '경유 지자체 · 주요 구간'},
                {'key': 'tl_landowner', 'label': '용지 교섭 현황', 'type': 'text',
                 'hint': '미착수 / 협의중 / 완료 · 필지 수 · 진척률'},
                {'key': 'tl_crossing', 'label': '도로 · 하천 횡단 점용허가', 'type': 'text'},
                {'key': 'substation_build', 'label': '변전소 신설 / 기존 이용', 'type': 'text'},
                {'key': 'tl_complaints', 'label': '송전선로 관련 민원', 'type': 'textarea'},
            ]},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'sales', 'no': 7, 'title': '전력 판매 (PPA / REC)', 'icon': '🔌', 'stage': 'dev',
        'subtitle': '판매 구조 · 계약 단가 · REC 가중치',
        'note': '사업 수익의 근간이므로 경제성보다 먼저 채운다. 미정이면 검토 중인 안을 적어둔다.',
        'groups': [
            {'title': '판매 구조', 'fields': [
                {'key': 'sales_type', 'label': '판매 방식', 'type': 'select', 'star': True, 'status': True,
                 'options': ['RPS 고정가격계약', '제3자PPA', '직접(기업)PPA', 'SMP+REC 현물',
                             '전력시장 직접판매', '혼합', '미정']},
                {'key': 'offtaker', 'label': '계약 상대방', 'type': 'text', 'star': True,
                 'hint': '한전 / 발전공기업 / 기업 수요처 / 미정'},
                {'key': 'contract_volume', 'label': '계약 물량', 'type': 'text',
                 'hint': '전량 계약 여부. 일부면 잔여분 처리 방식'},
                {'key': 'contract_price', 'label': '계약 단가', 'type': 'number', 'unit': '원/kWh',
                 'star': True, 'status': True, 'hint': 'SMP·REC 합산인지 분리인지 명시'},
                {'key': 'price_basis', 'label': '단가 구성', 'type': 'select',
                 'options': ['SMP+REC 합산', 'SMP / REC 분리', '고정단가(합산)', '미정']},
                {'key': 'sales_period', 'label': '계약 기간', 'type': 'text', 'hint': '시작일 · 종료일'},
                {'key': 'settlement', 'label': '정산 주기 · 방식', 'type': 'text'},
                {'key': 'price_adjust', 'label': '가격 조정 조항', 'type': 'text', 'hint': '물가연동 · 재협상'},
                {'key': 'output_guarantee', 'label': '발전량 보증 / 미달 시 정산', 'type': 'textarea'},
                {'key': 'termination', 'label': '계약 해지 조건', 'type': 'textarea'},
            ]},
            {'title': 'RPS · REC', 'fields': [
                {'key': 'rec_weight', 'label': 'REC 가중치', 'type': 'number', 'star': True, 'status': True,
                 'hint': '부지유형 · 설비용량 · 주민참여 반영값'},
                {'key': 'rec_basis', 'label': '가중치 산정 근거', 'type': 'text',
                 'hint': 'RPS 관리·운영지침 해당 조항'},
                {'key': 'rps_bid', 'label': '고정가격계약 입찰 참여', 'type': 'select',
                 'options': ['미참여', '참여', '낙찰', '탈락', '해당없음']},
                {'key': 'bid_price', 'label': '낙찰 단가', 'type': 'number', 'unit': '원/kWh', 'status': True},
                {'key': 'rec_start', 'label': 'REC 발급 개시일', 'type': 'date'},
                {'key': 'rec_contract', 'label': 'REC 매매계약', 'type': 'text',
                 'hint': '미체결 / 체결 / 해지 · 상대방 · 기간'},
                {'key': 'carbon_grade', 'label': '탄소인증 등급', 'type': 'text'},
            ]},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'contracts', 'no': 8, 'title': '주요 계약 총괄', 'icon': '📑', 'stage': 'build',
        'subtitle': '누구와 · 언제 · 어떤 상태인지',
        'note': '상세 조건은 각 전용 섹션에 적고, 여기서는 계약 목록만 한눈에 보이게 한다.',
        'fields': [
            {'key': 'contract_list', 'label': '계약 목록', 'type': 'table', 'star': True,
             'columns': [
                 {'key': 'kind', 'label': '계약 종류', 'type': 'select', 'width': '150px',
                  'options': ['EPC', 'PF 대출약정', 'PPA', 'REC 매매', 'O&M', '감리 · OE',
                              '송전용전기설비 이용계약', '부지 임대차', '부지 매매',
                              '주주간계약(SHA)', '지분매매(SPA)', '개발용역', '인허가 용역',
                              '환경영향평가 용역', '재해영향평가 용역', '전기설계 용역',
                              '지역수용성 용역', '보험(건설)', '보험(재산·배상)', '기타']},
                 {'key': 'party', 'label': '상대방', 'type': 'text', 'width': '1fr'},
                 {'key': 'date', 'label': '체결일', 'type': 'date', 'width': '140px'},
                 {'key': 'terms', 'label': '금액 / 주요 조건', 'type': 'text', 'width': '1.2fr'},
                 {'key': 'status', 'label': '상태', 'type': 'select', 'width': '120px',
                  'options': _CONTRACT_STATUS},
             ],
             'rows': [
                 {'kind': 'EPC'}, {'kind': 'PF 대출약정'}, {'kind': 'PPA'}, {'kind': 'O&M'},
             ]},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'finance', 'no': 9, 'title': '사업비 및 금융', 'icon': '💰', 'stage': 'build',
        'subtitle': '총사업비 · 자본구조 · PF · 금융종결',
        'groups': [
            {'title': '사업비', 'fields': [
                {'key': 'total_cost', 'label': '총사업비', 'type': 'number', 'unit': '억원',
                 'star': True, 'status': True},
                {'key': 'epc_cost', 'label': 'EPC 공사비', 'type': 'number', 'unit': '억원', 'status': True},
                {'key': 'land_cost', 'label': '부지비', 'type': 'number', 'unit': '억원', 'status': True,
                 'hint': '매입 또는 임대 선급'},
                {'key': 'grid_cost', 'label': '계통 연계비 · 공사부담금', 'type': 'number', 'unit': '억원',
                 'status': True},
                {'key': 'service_cost', 'label': '인허가 · 용역비', 'type': 'number', 'unit': '억원',
                 'status': True},
                {'key': 'finance_cost', 'label': '금융비용 · 수수료', 'type': 'number', 'unit': '억원',
                 'status': True},
                {'key': 'contingency', 'label': '예비비', 'type': 'number', 'unit': '억원', 'status': True},
            ]},
            {'title': '금융 조건', 'fields': [
                {'key': 'equity_ratio', 'label': '자기자본 비율', 'type': 'number', 'unit': '%',
                 'star': True, 'status': True},
                {'key': 'debt_ratio', 'label': '타인자본 비율', 'type': 'number', 'unit': '%', 'status': True},
                {'key': 'lenders', 'label': 'PF 대주단 구성', 'type': 'textarea', 'star': True, 'status': True,
                 'hint': '변동금리 대주 / 고정금리 대주를 나눠 기재'},
                {'key': 'loan_amount', 'label': '대주별 약정금액 · 약정비율', 'type': 'textarea'},
                {'key': 'interest_rate', 'label': 'PF 금리', 'type': 'number', 'unit': '%', 'status': True},
                {'key': 'rate_type', 'label': '금리 유형', 'type': 'select',
                 'options': ['고정', '변동(CD+스프레드)', '혼합', '미정']},
                {'key': 'maturity', 'label': '만기 · 상환 방식', 'type': 'text'},
                {'key': 'fc_date', 'label': '금융종결(FC) 일자', 'type': 'date', 'star': True, 'status': True},
                {'key': 'security', 'label': '담보 · 보증 구조', 'type': 'textarea'},
                {'key': 'reserve', 'label': '준비금 (DSRA 등)', 'type': 'text'},
                {'key': 'drawdown', 'label': '자금 인출 진행', 'type': 'text', 'hint': 'N회차 / 누적 인출액'},
                {'key': 'covenant', 'label': '재무약정 (DSCR 등)', 'type': 'text'},
            ]},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'epc', 'no': 10, 'title': 'EPC (설계 · 조달 · 시공)', 'icon': '🏗️', 'stage': 'build',
        'subtitle': '계약사 · 금액 · 기자재 사양 · 보증',
        'fields': [
            {'key': 'epc_contractor', 'label': 'EPC 계약사', 'type': 'text', 'star': True},
            {'key': 'epc_amount', 'label': 'EPC 계약 금액', 'type': 'number', 'unit': '억원',
             'star': True, 'status': True},
            {'key': 'epc_type', 'label': '계약 방식', 'type': 'select',
             'options': ['턴키', '분리발주', '기타', '미정']},
            {'key': 'epc_bid', 'label': '입찰 경과', 'type': 'text', 'hint': '참여사 · 낙찰 일자'},
            {'key': 'epc_period', 'label': '공사 기간', 'type': 'text', 'hint': '착공 ~ 준공'},
            {'key': 'module_spec', 'label': '모듈 사양', 'type': 'text', 'status': True,
             'placeholder': '예: 550W, 22.5%, 5,000장'},
            {'key': 'inverter_spec', 'label': '인버터 사양', 'type': 'text', 'status': True,
             'placeholder': '예: 스트링형 100kW × 30대'},
            {'key': 'structure_spec', 'label': '구조물 / 기초 공법', 'type': 'textarea',
             'hint': '연약지반 · 염해간척지는 침하 대책이 핵심'},
            {'key': 'performance_guarantee', 'label': '성능보증 (PR 등)', 'type': 'text', 'hint': '미달 시 배상'},
            {'key': 'defect_warranty', 'label': '하자보증 기간', 'type': 'text'},
            {'key': 'liquidated_damages', 'label': '지체상금 조항', 'type': 'text'},
            {'key': 'epc_progress', 'label': '기성 진행', 'type': 'text', 'hint': 'N차 기성 / 준공금 청구 여부'},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'economics', 'no': 11, 'title': '경제성', 'icon': '📊', 'stage': 'build',
        'subtitle': '이용률 · 발전량 · 매출 · IRR',
        'note': '단가·판매 조건은 「전력 판매」 섹션에 적고, 여기서는 수익성 지표만 다룬다.',
        'fields': [
            {'key': 'irradiation', 'label': '일사량', 'type': 'number', 'unit': 'kWh/㎡·년', 'status': True},
            {'key': 'capacity_factor', 'label': '이용률', 'type': 'number', 'unit': '%',
             'star': True, 'status': True},
            {'key': 'gen_hours', 'label': '연간 발전시간', 'type': 'number', 'unit': 'h/년', 'status': True},
            {'key': 'guaranteed_hours', 'label': '발전보증시간', 'type': 'number', 'unit': 'h/년',
             'status': True, 'hint': 'EPC 성능보증 기준'},
            {'key': 'expected_pr', 'label': '예상 PR', 'type': 'number', 'unit': '%', 'status': True},
            {'key': 'annual_gen', 'label': '연간 발전량', 'type': 'number', 'unit': 'MWh',
             'star': True, 'status': True},
            {'key': 'smp_assumption', 'label': 'SMP 가정', 'type': 'number', 'unit': '원/kWh', 'status': True},
            {'key': 'rec_assumption', 'label': 'REC 가정', 'type': 'number', 'unit': '원', 'status': True},
            {'key': 'annual_revenue', 'label': '연간 매출', 'type': 'number', 'unit': '억원',
             'star': True, 'status': True},
            {'key': 'annual_opex', 'label': '연간 운영비', 'type': 'number', 'unit': '억원', 'status': True},
            {'key': 'operating_profit', 'label': '영업이익', 'type': 'number', 'unit': '억원', 'status': True},
            {'key': 'eirr', 'label': 'E.IRR', 'type': 'number', 'unit': '%', 'star': True, 'status': True,
             'hint': '자기자본 수익률'},
            {'key': 'pirr', 'label': 'P.IRR', 'type': 'number', 'unit': '%', 'star': True, 'status': True,
             'hint': '사업 수익률'},
            {'key': 'dscr', 'label': '최소 · 평균 DSCR', 'type': 'text', 'status': True},
            {'key': 'payback', 'label': '투자회수기간', 'type': 'number', 'unit': '년', 'status': True},
            {'key': 'sensitivity', 'label': '민감도 분석', 'type': 'textarea',
             'hint': 'SMP · REC · 이용률 변동 시'},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'operation', 'no': 12, 'title': '운영', 'icon': '⚙️', 'stage': 'ops',
        'subtitle': 'COD · O&M · 안전관리',
        'fields': [
            {'key': 'cod', 'label': 'COD (상업운전개시일)', 'type': 'date', 'star': True, 'status': True},
            {'key': 'project_life', 'label': '사업 기간', 'type': 'number', 'unit': '년', 'status': True},
            {'key': 'om_provider', 'label': 'O&M 수행사', 'type': 'text'},
            {'key': 'om_amount', 'label': 'O&M 계약 금액', 'type': 'number', 'unit': '원/kW·년', 'status': True},
            {'key': 'om_period', 'label': 'O&M 계약 기간', 'type': 'text'},
            {'key': 'om_scope', 'label': 'O&M 범위', 'type': 'textarea',
             'hint': '정기점검 · 고장대응 · 제초 · 세척'},
            {'key': 'monitoring', 'label': '원격 감시 시스템', 'type': 'text'},
            {'key': 'safety_outsourcing', 'label': '전기안전관리 위탁', 'type': 'text'},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'performance', 'no': 13, 'title': '발전 · 수익 실적', 'icon': '📈', 'stage': 'ops',
        'subtitle': '연도별 발전량 · 이용률 · 매출',
        'note': '운영 전 사업은 비워 두면 「해당없음(개발단계)」으로 출력된다.',
        'fields': [
            {'key': 'yearly', 'label': '연도별 실적', 'type': 'table',
             'columns': [
                 {'key': 'year', 'label': '연도', 'type': 'text', 'width': '90px'},
                 {'key': 'plan', 'label': '발전량 계획 (MWh)', 'type': 'number', 'width': '1fr'},
                 {'key': 'actual', 'label': '발전량 실적 (MWh)', 'type': 'number', 'width': '1fr'},
                 {'key': 'cf', 'label': '이용률 (%)', 'type': 'number', 'width': '100px'},
                 {'key': 'pr', 'label': 'PR (%)', 'type': 'number', 'width': '100px'},
                 {'key': 'revenue', 'label': '매출 (억원)', 'type': 'number', 'width': '110px'},
                 {'key': 'dscr', 'label': 'DSCR', 'type': 'number', 'width': '90px'},
             ]},
            {'key': 'smp_actual', 'label': 'SMP 실적 단가', 'type': 'number', 'unit': '원/kWh', 'status': True},
            {'key': 'rec_actual', 'label': 'REC 실적 단가', 'type': 'number', 'unit': '원', 'status': True},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'equipment', 'no': 14, 'title': '설비 현황', 'icon': '🔧', 'stage': 'ops',
        'subtitle': '모듈 · 인버터 · 고장 이력',
        'fields': [
            {'key': 'module_status', 'label': '모듈 상태', 'type': 'textarea', 'hint': '열화율 · 교체 이력'},
            {'key': 'inverter_status', 'label': '인버터 상태', 'type': 'textarea'},
            {'key': 'substation_status', 'label': '변전 · 송전설비 상태', 'type': 'textarea'},
            {'key': 'fault_history', 'label': '고장 · 정지 이력', 'type': 'textarea',
             'hint': '일자 · 원인 · 조치'},
            {'key': 'inspection_history', 'label': '정기점검 이력', 'type': 'textarea'},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'insurance', 'no': 15, 'title': '보험 · 세제 · 안전', 'icon': '🛡️', 'stage': 'ops',
        'subtitle': '부보 현황 · 감면 · 안전보건',
        'fields': [
            {'key': 'construction_ins', 'label': '건설공사 보험', 'type': 'text',
             'hint': '보험사 · 기간 · 부보액'},
            {'key': 'property_ins', 'label': '재산종합 보험', 'type': 'text'},
            {'key': 'liability_ins', 'label': '배상책임 보험', 'type': 'text'},
            {'key': 'surety_ins', 'label': '이행 · 하자 보증보험', 'type': 'text'},
            {'key': 'tax_exemption', 'label': '취득세 · 재산세 감면', 'type': 'text', 'hint': '근거 조항'},
            {'key': 'local_tax', 'label': '지역자원시설세', 'type': 'text'},
            {'key': 'vat_refund', 'label': '부가세 환급', 'type': 'text'},
            {'key': 'safety_system', 'label': '안전보건 관리체계', 'type': 'textarea',
             'hint': '중대재해처벌법 대응'},
            {'key': 'accident_history', 'label': '산업안전 사고 이력', 'type': 'textarea'},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'schedule', 'no': 16, 'title': '일정', 'icon': '📅', 'stage': None,
        'subtitle': '마일스톤별 계획 / 실적',
        'fields': [
            {'key': 'milestones', 'label': '마일스톤', 'type': 'table', 'star': True,
             'columns': [
                 {'key': 'name', 'label': '마일스톤', 'type': 'text', 'width': '1fr'},
                 {'key': 'plan', 'label': '계획일', 'type': 'date', 'width': '150px'},
                 {'key': 'actual', 'label': '실적일', 'type': 'date', 'width': '150px'},
                 {'key': 'status', 'label': '상태', 'type': 'select', 'width': '120px',
                  'options': _MILESTONE_STATUS},
             ],
             'rows': [
                 {'name': '부지 확보'},
                 {'name': '발전사업허가'},
                 {'name': '계통접속 이용계약 체결'},
                 {'name': '환경영향평가 협의 완료'},
                 {'name': '재해영향평가 협의 완료'},
                 {'name': '개발행위허가'},
                 {'name': '전력판매(PPA/REC) 계약 체결'},
                 {'name': '공사계획인가'},
                 {'name': '금융종결(FC)'},
                 {'name': '착공'},
                 {'name': '사용전검사 (송전 / 발전)'},
                 {'name': '계통 병입'},
                 {'name': '사업개시신고'},
                 {'name': '준공 / COD'},
             ]},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'log', 'no': 17, 'title': '진행 경과', 'icon': '📝', 'stage': None,
        'subtitle': '완료된 마일스톤 · 주요 의사결정',
        'fields': [
            {'key': 'events', 'label': '주요 이벤트', 'type': 'table',
             'columns': [
                 {'key': 'date', 'label': '일자', 'type': 'date', 'width': '150px'},
                 {'key': 'event', 'label': '주요 이벤트', 'type': 'text', 'width': '1fr'},
             ]},
        ],
    },
    # ────────────────────────────────────────────────────────────────
    {
        'id': 'risk', 'no': 18, 'title': '주요 리스크 및 대응방안', 'icon': '⚠️', 'stage': None,
        'subtitle': '인허가 · 계통 · 부지 · 금융 · 수익성',
        'fields': [
            {'key': 'risks', 'label': '리스크', 'type': 'table',
             'columns': [
                 {'key': 'category', 'label': '구분', 'type': 'select', 'width': '120px',
                  'options': ['인허가', '계통', '부지', '주민', '금융', '전력판매 · 가격',
                              '공사 · 기자재', '발전량 · 수익성', '설비', '기타']},
                 {'key': 'risk', 'label': '리스크', 'type': 'text', 'width': '1.3fr'},
                 {'key': 'impact', 'label': '영향', 'type': 'select', 'width': '90px',
                  'options': ['상', '중', '하']},
                 {'key': 'action', 'label': '대응방안', 'type': 'text', 'width': '1.3fr'},
                 {'key': 'status', 'label': '상태', 'type': 'select', 'width': '110px',
                  'options': ['미착수', '진행중', '해소']},
             ]},
        ],
    },
]


# ── 조회 헬퍼 ────────────────────────────────────────────────────────

def iter_fields(section):
    """섹션의 필드를 그룹 구분 없이 (group_title, field) 로 순회한다."""
    for f in section.get('fields', []):
        yield None, f
    for g in section.get('groups', []):
        for f in g.get('fields', []):
            yield g.get('title'), f


def field_index():
    """(section_id, field_key) -> field 사전"""
    idx = {}
    for sec in SECTIONS:
        for _, f in iter_fields(sec):
            idx[(sec['id'], f['key'])] = f
    return idx


def star_fields():
    """⭐ 핵심 항목 (section_id, field) 목록 — 완성도 계산에 쓴다."""
    out = []
    for sec in SECTIONS:
        for _, f in iter_fields(sec):
            if f.get('star'):
                out.append((sec['id'], f))
    return out


def preset_keys(field) -> set:
    """
    표 필드에서 스키마가 미리 채워 둔 열의 키.

    마일스톤명·협의명처럼 양식이 제공한 값은 '사용자가 입력한 내용'이 아니다.
    이를 구분하지 않으면 폼을 열어 두기만 해도 모든 표가 '입력됨'으로 집계되고,
    마크다운에는 값이 전부 `미확인`인 빈 표가 실려 나간다.
    """
    rows = field.get('rows') or []
    keys = set()
    for r in rows:
        keys.update(k for k, v in r.items() if str(v).strip())
    return keys


def row_has_input(row, field) -> bool:
    """표의 한 행에 사용자가 실제로 채운 값이 있는가"""
    if not isinstance(row, dict):
        return False
    preset = preset_keys(field)
    return any(str(v).strip() for k, v in row.items() if k not in preset)


def as_dict():
    """프론트엔드로 내려보낼 스키마 전문"""
    return {
        'version': 'v1',
        'stages': STAGES,
        'confidence_choices': CONFIDENCE_CHOICES,
        'sections': SECTIONS,
    }
