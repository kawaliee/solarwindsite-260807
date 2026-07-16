import json
from django.core.management.base import BaseCommand
from apps.contracts.models import ContractTemplate

class Command(BaseCommand):
    help = '16종 표준 계약서 마스터 시드 데이터를 입력합니다.'

    def handle(self, *args, **options):
        templates_data = [
            {
                "code": "JDA",
                "name_ko": "공동개발협약",
                "name_en": "Joint Development Agreement",
                "category": "개발",
                "description": "재생에너지 사업의 초기 단계에서 사업권 확보 및 공동 개발을 위해 당사자 간의 권리와 의무를 정의하는 협약서",
                "template_body": """# 공동개발협약서 (Joint Development Agreement)

본 공동개발협약(이하 "본 협약")은 [계약 당사자] (이하 "당사자들") 간에 재생에너지 발전사업의 공동 개발과 관련하여 체결된다.

## 제1조 (목적)
본 협약은 당사자들이 추진하고자 하는 [계약 용량] 규모의 발전사업(이하 "본 사업")의 공동 개발에 있어 당사자 간의 역할 분담, 비용 분담 및 수익 배분 등에 관한 기본적인 사항을 규정함을 목적으로 한다.

## 제2조 (협약 기간)
본 협약의 유효기간은 체결일로부터 [계약 기간]으로 한다. 단, 당사자들의 서면 합의에 의해 연장할 수 있다.

## 제3조 (정산 및 비용 분담)
본 사업의 개발에 소요되는 제반 비용 및 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 협약의 체결을 증명하기 위하여 협약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "목적", "content": "본 협약은 사업개발 공동 추진을 목적으로 함"},
                    {"section": "제2조", "title": "역할 및 의무", "content": "각 당사자의 업무 분담 범위 정의"},
                    {"section": "제3조", "title": "비용의 분담", "content": "인허가, 부지 확보 등 비용 분담 비율 및 방식"},
                    {"section": "제4조", "title": "지분 배분", "content": "사업권 획득 후 본 사업 SPC 설립 시 지분 비율"}
                ],
                "review_checklist": [
                    {"id": "RC-JDA-001", "clause": "비용 부담", "check_point": "사업 중단 시 기지출 비용의 정산 및 반환 조건이 명확한가"},
                    {"id": "RC-JDA-002", "clause": "독점권", "check_point": "개발 기간 동안 타사와의 중복 개발 금지(독점 배타적 권리) 기간이 적정한가"},
                    {"id": "RC-JDA-003", "clause": "지적재산권", "check_point": "공동 개발 중 획득한 특허 및 사업권의 귀속이 명확한가"}
                ]
            },
            {
                "code": "SPA",
                "name_ko": "지분/자산 매매계약",
                "name_en": "Sale & Purchase Agreement",
                "category": "인수",
                "description": "발전사업 SPC의 지분 또는 발전소 자산을 매수/매도할 때 거래 조건과 보장 사항을 규율하는 계약서",
                "template_body": """# 지분/자산 매매계약서 (Sale & Purchase Agreement)

본 지분/자산 매매계약(이하 "본 계약")은 [계약 당사자] 간에 지분 또는 자산의 양수도와 관련하여 체결된다.

## 제1조 (목적)
양도인은 양수인에게 본 사업([계약 용량])의 지분/자산을 양도하고, 양수인은 이를 양수한다.

## 제2조 (양수도 기간 및 종결)
본 계약에 따른 거래 종결 및 양수도 기간은 [계약 기간]으로 한다.

## 제3조 (매매대금 및 정산 조건)
본 계약에 따른 매매대금 및 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 계약의 체결을 증명하기 위하여 계약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "매매 대상", "content": "양수도 대상 주식 또는 자산의 명세"},
                    {"section": "제2조", "title": "매매 대금", "content": "계약금, 중도금, 잔금 지급 조건 및 시기"},
                    {"section": "제3조", "title": "선행 조건", "content": "거래 종결을 위해 충족되어야 하는 정부 인허가 등 선행조건"},
                    {"section": "제4조", "title": "진술과 보장", "content": "양도인이 대상 회사 및 자산에 대해 보장하는 사항"}
                ],
                "review_checklist": [
                    {"id": "RC-SPA-001", "clause": "진술과 보장", "check_point": "양도인의 우발 채무나 세무 리스크에 대한 면책 및 손해배상 한도가 명확한가"},
                    {"id": "RC-SPA-002", "clause": "사후 정산", "check_point": "거래 종결일 기준 순자산 변동에 대한 정산 메커니즘이 포함되어 있는가"},
                    {"id": "RC-SPA-003", "clause": "해제 조건", "check_point": "선행조건 미충족 시 계약 해제권 및 위약벌 규정이 균형 잡혀 있는가"}
                ]
            },
            {
                "code": "SHA",
                "name_ko": "주주간 협약",
                "name_en": "Shareholders Agreement",
                "category": "지배구조",
                "description": "공동 투자로 설립된 발전사업 SPC 주주들 간의 경영권 분담, 의결권 행사, 지분 처분 제한 등을 정하는 협약서",
                "template_body": """# 주주간 협약서 (Shareholders Agreement)

본 주주간 협약서(이하 "본 협약")는 [계약 당사자] 간에 대상 회사의 주주로서 회사 경영 및 지배구조에 관한 사항을 정하기 위해 체결된다.

## 제1조 (목적)
본 협약은 대상 회사가 추진하는 [계약 용량] 규모의 발전사업의 운영 및 주주 간 권리관계를 규율함을 목적으로 한다.

## 제2조 (협약 유효기간)
본 협약의 유효기간은 [계약 기간] 동안 또는 당사자가 주주로 남아 있는 한 유지된다.

## 제3조 (출자 및 정산 조건)
주주들의 출자 비율, 추가 자금 조달 및 배당 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 협약의 체결을 증명하기 위하여 협약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "이사회 구성", "content": "각 주주별 이사 지명 권한 및 이사회 결의 요건"},
                    {"section": "제2조", "title": "지분 처분 제한", "content": "우선매수권(Right of First Refusal), 동반매도참여권(Tag-along), 동반매도요구권(Drag-along)"},
                    {"section": "제3조", "title": "자금 조달 및 배당", "content": "추가 증자 의무 및 이익 배당 정책"},
                    {"section": "제4조", "title": "교착상태 해결", "content": "주주 간 이견 대립(Deadlock) 시 해결 방안"}
                ],
                "review_checklist": [
                    {"id": "RC-SHA-001", "clause": "교착상태", "check_point": "의결정족수 미달로 이사회가 마비될 경우 동반매수/매도 옵션이 실효성 있게 규정되었는가"},
                    {"id": "RC-SHA-002", "clause": "처분 제한", "check_point": "소수주주 보호를 위한 Tag-along 권리와 대주주의 투자 회수를 위한 Drag-along 권리가 균형을 이루는가"},
                    {"id": "RC-SHA-003", "clause": "추가 출자 의무", "check_point": "추가 자금 필요 시 증자 거부 주주에 대한 지분 희석 규정이 적절한가"}
                ]
            },
            {
                "code": "EPC",
                "name_ko": "EPC 도급계약",
                "name_en": "Engineering Procurement Construction",
                "category": "건설",
                "description": "발전소의 설계, 기자재 조달, 시공을 일괄 도급하는 시공사와의 계약서",
                "template_body": """# 설계·조달·시공 도급계약서 (EPC Contract)

본 EPC 도급계약(이하 "본 계약")은 발주자와 시공사(이하 [계약 당사자]) 간에 발전소 건설공사를 일괄 도급하기 위하여 체결된다.

## 제1조 (목적)
시공사는 발주자에게 [계약 용량] 규모의 발전소 건설 공사를 설계, 조달, 시공을 포함하여 일괄 수행하여 인도한다.

## 제2조 (공사 기간)
본 계약에 따른 공사 착공일 및 준공 기한은 [계약 기간]으로 한다.

## 제3조 (계약 금액 및 정산 조건)
공사 도급대금 및 기성금 지급 등의 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 계약의 체결을 증명하기 위하여 계약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "공사 범위", "content": "설계, 인허가 지원, 기자재 구매, 시공, 시운전 범위"},
                    {"section": "제2조", "title": "공사 대금 및 기성", "content": "마일스톤별 기성금 지급 일정 및 유보금"},
                    {"section": "제3조", "title": "지체상금", "content": "준공 기한 지연 시 일일 지체상금 요율 및 한도"},
                    {"section": "section 4", "title": "성능 보증", "content": "발전 효율, 출력 등 준공 시 성능 시험 및 미달 시 배상 의무"}
                ],
                "review_checklist": [
                    {"id": "RC-EPC-001", "clause": "지체상금 상한", "check_point": "지체상금 상한이 통상적인 계약금액의 10% 이내로 제한되어 있으며 발주자 해제권과 연결되는가"},
                    {"id": "RC-EPC-002", "clause": "설계 변경", "check_point": "발주자의 지시에 의한 설계 변경 시 계약 금액 조정 절차가 명확한가"},
                    {"id": "RC-EPC-003", "clause": "하자보수보증", "check_point": "하자보수기간(예: 2~3년) 및 하자보수보증금 요율(예: 계약금액의 5%)이 금융약정 요구조건을 충족하는가"}
                ]
            },
            {
                "code": "OM",
                "name_ko": "운영·유지보수 계약",
                "name_en": "Operation & Maintenance Agreement",
                "category": "운영",
                "description": "발전소 준공 후 상업운전 기간 동안의 설비 관리, 예방 정비, 긴급 복구 등을 위탁하는 O&M 계약서",
                "template_body": """# 운영·유지보수 계약서 (O&M Agreement)

본 운영·유지보수 계약(이하 "본 계약")은 발주자와 O&M 수행사(이하 [계약 당사자]) 간에 발전소 관리 업무를 위탁하기 위해 체결된다.

## 제1조 (목적)
수행사는 [계약 용량] 규모의 발전소 설비에 대하여 안정적인 운영 및 유지보수 업무를 수행한다.

## 제2조 (계약 기간)
본 계약의 O&M 위탁 기간은 상업운전 개시일로부터 [계약 기간]으로 한다.

## 제3조 (용역 대금 및 정산 조건)
O&M 기본 용역비 및 성과 인센티브, 패널티 등 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 계약의 체결을 증명하기 위하여 계약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "용역 범위", "content": "일상 점검, 정기 예방 정비, 긴급 복구 및 스페어 파트 관리"},
                    {"section": "제2조", "title": "가동률 보증", "content": "연간 목표 발전 가동률 보증 및 미달 시 손실 배상"},
                    {"section": "제3조", "title": "용역비 정산", "content": "월별/분기별 고정 용역비 지급 및 물가상승률 반영 방식"},
                    {"section": "제4조", "title": "비상 대응", "content": "재해 발생 시 긴급 출동 시한 및 보고 체계"}
                ],
                "review_checklist": [
                    {"id": "RC-OM-001", "clause": "발전 효율 보증", "check_point": "가동률 또는 PR(Performance Ratio) 보증 규정이 명확하며 예외사유(기상악화, 계통차단 등)가 합리적으로 정의되어 있는가"},
                    {"id": "RC-OM-002", "clause": "부품 교체 비용", "check_point": "대형 부품(인버터, 변압기 등) 교체 비용 부담 주체(발주자 vs 수행사)가 명확한가"},
                    {"id": "RC-OM-003", "clause": "손해 배상", "check_point": "O&M 과실로 인한 발전 중단 손실에 대한 책임 제한 한도가 합당한가"}
                ]
            },
            {
                "code": "OE",
                "name_ko": "감리/OE 계약",
                "name_en": "Owner's Engineer Agreement",
                "category": "건설",
                "description": "발주자를 대행하여 공사 현장의 설계 도면 검토, 시공 감리, 품질 및 기성 검사를 수행하는 기술 용역 계약서",
                "template_body": """# 감리·발주자엔지니어(OE) 용역계약서 (Owner's Engineer Agreement)

본 용역계약(이하 "본 계약")은 발주자와 기술용역사(이하 [계약 당사자]) 간에 감리 및 발주자엔지니어링 업무를 위탁하기 위하여 체결된다.

## 제1조 (목적)
용역사는 [계약 용량] 규모의 발전소 시공 공사에 대한 감리 및 발주자 기술 지원(OE) 용역을 수행한다.

## 제2조 (용역 기간)
본 계약에 따른 용역 기간은 공사 착공일로부터 준공 검사 완료 후 [계약 기간]까지로 한다.

## 제3조 (용역 금액 및 정산 조건)
본 용역에 따른 용역비 및 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 계약의 체결을 증명하기 위하여 계약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "용역 범위", "content": "기자재 제작 감수, 현장 시공 감리, 설계도서 검토 및 기술 조언"},
                    {"section": "제2조", "title": "인력 투입", "content": "분야별 기술사 및 고급 기술 인력 현장 배치 계획"},
                    {"section": "제3조", "title": "보고서 제출", "content": "주간/월간 감리 보고서 및 최종 준공 보고서"},
                    {"section": "제4조", "title": "비밀 유지", "content": "사업 시행 도중 취득한 기밀 정보의 누설 금지"}
                ],
                "review_checklist": [
                    {"id": "RC-OE-001", "clause": "용역 범위", "check_point": "시공사(EPC)와의 분쟁 발생 시 OE로서의 기술 중재 또는 의견서 제출 의무가 포함되어 있는가"},
                    {"id": "RC-OE-002", "clause": "인력 상주", "check_point": "상주 감리 인력의 자격 요건과 부재 시 대체 인력 투입 규정이 마련되어 있는가"},
                    {"id": "RC-OE-003", "clause": "책임 범위", "check_point": "OE의 오판으로 인한 공사 지연 또는 재시공 발생 시 손해배상 책임 한도가 적합하게 설정되었는가"}
                ]
            },
            {
                "code": "FIN",
                "name_ko": "금융약정",
                "name_en": "Project Finance Agreement",
                "category": "금융",
                "description": "발전소 건설에 소요되는 자금을 조달하기 위해 대주단(은행 등)과 체결하는 프로젝트 파이낸스 약정서",
                "template_body": """# 금융약정서 (Project Finance Agreement)

본 금융약정(이하 "본 약정")은 차주와 대주단(이하 [계약 당사자]) 간에 사업 자금 대출 및 담보 설정을 위하여 체결된다.

## 제1조 (목적)
대주단은 [계약 용량] 규모의 발전소 건설 및 운영을 위한 자금을 차주에게 대출하고, 차주는 이를 본 사업 목적으로만 사용한다.

## 제2조 (인출 및 대출 기간)
본 약정에 따른 자금의 최초 인출 기한 및 대출 원리금 상환 기간은 [계약 기간]으로 한다.

## 제3조 (금리 및 정산 조건)
대출 금리, 수수료, 원리금 분할 상환 방식 등의 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 약정의 체결을 증명하기 위하여 약정서 서명날인 후 관련 당사자들이 각각 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "대출 한도 및 인출", "content": "대출 약정 금액 및 인출 선행 조건(Conditions Precedent)"},
                    {"section": "제2조", "title": "담보 및 보증", "content": "사업 부지 저당권, 주식 근질권, 에스크로 계좌 설정, 시공사 책임준공 약정"},
                    {"section": "제3조", "title": "재무적 약정사항", "content": "부채상환재원비율(DSCR) 유지 조건 등"},
                    {"section": "제4조", "title": "기한의 이익 상실", "content": "기한의 이익 상실(Event of Default) 사유 및 효력"}
                ],
                "review_checklist": [
                    {"id": "RC-FIN-001", "clause": "인출 선행조건", "check_point": "최초 자금 인출 조건으로 요구되는 인허가 및 계약서 체결 목록이 현실적으로 달성 가능한가"},
                    {"id": "RC-FIN-002", "clause": "EOD 사유", "check_point": "기한의 이익 상실 사유 중 통제 불가능한 사유(예: 시공사 부도 외 경미한 인허가 지연 등)가 과도하게 포함되지 않았는가"},
                    {"id": "RC-FIN-003", "clause": "자금 통제", "check_point": "에스크로 계좌를 통한 자금 집행 순위(원리금, O&M비용, 주주배당 등)가 운영상 타당한가"}
                ]
            },
            {
                "code": "DD",
                "name_ko": "실사 자문용역",
                "name_en": "Due Diligence Service Agreement",
                "category": "자문",
                "description": "프로젝트 인수 혹은 금융 약정 전 재무, 기술, 법률적 리스크를 분석하기 위해 선임하는 전문 기관과의 자문 용역 계약서",
                "template_body": """# 실사 자문용역계약서 (Due Diligence Agreement)

본 용역계약(이하 "본 계약")은 발주자와 자문사(이하 [계약 당사자]) 간에 사업 실사 업무를 위탁하기 위하여 체결된다.

## 제1조 (목적)
자문사는 발주자가 추진하는 [계약 용량] 규모의 발전사업 인수/금융 조달을 위해 재무/기술/법률 부문의 정밀 실사(Due Diligence) 용역을 제공한다.

## 제2조 (용역 기간)
본 계약에 따른 실사 수행 및 최종 보고서 제출 기한은 [계약 기간]으로 한다.

## 제3조 (자문 수수료 및 정산 조건)
실사 용역 금액, 수수료 지급 시기 및 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 계약의 체결을 증명하기 위하여 계약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "업무 범위", "content": "문서 실사, 현장 실사, 리스크 보고서 작성 및 Q&A 대응"},
                    {"section": "제2조", "title": "산출물", "content": "중간 실사 보고서, 최종 실사 보고서 국/영문 제출"},
                    {"section": "제3조", "title": "자료 협조", "content": "발주자의 대상 회사 관련 자료 제공 지원 범위"},
                    {"section": "제4조", "title": "책임 제한", "content": "자문사 의견의 사용 제한 및 고의/중과실 외 면책"}
                ],
                "review_checklist": [
                    {"id": "RC-DD-001", "clause": "배상 책임 제한", "check_point": "자문사의 과실로 인한 손해 발생 시 배상 한도가 통상적인 수수료 범위(예: 1~3배)로 제한되는가"},
                    {"id": "RC-DD-002", "clause": "일정 지연", "check_point": "대상 기업의 자료 제공 지연 시 자문 기간 자동 연장 및 추가 수수료 청구권이 규정되었는가"},
                    {"id": "RC-DD-003", "clause": "Reliance", "check_point": "작성된 실사보고서를 대주단(금융기관) 등 제3자가 신뢰(Reliance)하여 사용할 수 있도록 보장하는 범위가 합리적인가"}
                ]
            },
            {
                "code": "DSA_SLA",
                "name_ko": "DSA/SLA 직접계약/서비스수준협약",
                "name_en": "Direct Agreement / Service Level Agreement",
                "category": "금융/운영",
                "description": "대주단이 사업주 부도 시 수탁운영권 등을 직접 통제하기 위한 직접계약(DSA) 및 위탁운영 품질 보증을 위한 서비스 수준 협약(SLA)",
                "template_body": """# 직접계약/서비스수준협약서 (Direct Agreement / SLA)

본 약정(이하 "본 약정")은 대주단, 사업주, 그리고 운영사(이하 [계약 당사자]) 간에 사업 안정성 및 운영 품질 보증을 위해 체결된다.

## 제1조 (목적)
본 약정은 [계약 용량] 규모의 발전사업에서 사업주의 채무불이행 시 대주단이 운영 계약의 권리를 직접 행사하거나 승계할 수 있는 직접계약(DSA)과 O&M 서비스의 품질 지표(SLA)를 정함을 목적으로 한다.

## 제2조 (약정 기간)
본 약정의 효력은 금융약정이 만료되거나 원리금 상환이 완료되는 [계약 기간]까지로 한다.

## 제3조 (품질 지표 및 정산 조건)
서비스 수준 평가 지표(가동률, 장애 복구 시간) 및 미달 시 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 약정의 체결을 증명하기 위하여 약정서 3부를 작성하여 각 당사자가 서명날인 후 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "개입권", "content": "대주단의 운영자 지명 및 대체 운영권(Step-in Right)"},
                    {"section": "제2조", "title": "통지 의무", "content": "계약 해지 사유 발생 시 대주단에 사전 통지 및 치유 기간 부여"},
                    {"section": "제3조", "title": "SLA 지표", "content": "핵심성과지표(KPI) 및 가동률 측정 산식"},
                    {"section": "제4조", "title": "용역비 차감", "content": "SLA 미달 시 O&M 용역비 패널티 차감 규정"}
                ],
                "review_checklist": [
                    {"id": "RC-DS-001", "clause": "치유 기간", "check_point": "대주단에 대한 통지 후 개입할 수 있는 유예 및 치유 기간(Cure Period)이 최소 30~90일 이상 주어지는가"},
                    {"id": "RC-DS-002", "clause": "대체 지명", "check_point": "대주단이 지정한 신규 운영사 승계 시 발주자의 거부권을 합리적으로 제한하였는가"},
                    {"id": "RC-DS-003", "clause": "SLA 패널티", "check_point": "SLA 평가 결과에 따른 금액적 감액 한도가 연간 총 용역비의 10% 수준으로 합당하게 조율되어 있는가"}
                ]
            },
            {
                "code": "ADMIN",
                "name_ko": "사무위탁 계약",
                "name_en": "General Administration Agreement",
                "category": "경영관리",
                "description": "발전소 지분을 소유한 SPC의 회계, 세무, 공시 등 일상적인 행정 업무를 전문 대행사에 위탁하는 계약서",
                "template_body": """# 사무위탁계약서 (General Administration Agreement)

본 사무위탁계약(이하 "본 계약")은 위탁자([계약 용량] 발전소 SPC)와 수탁회사(이하 [계약 당사자]) 간에 경영 관리 사무 수탁을 위해 체결된다.

## 제1조 (목적)
수탁회사는 위탁자의 본 사업 경영 전반에 관한 회계, 자금 관리, 공시 및 행정 사무 업무를 대행한다.

## 제2조 (계약 기간)
본 계약에 따른 사무 위탁 기간은 체결일로부터 [계약 기간]으로 한다.

## 제3조 (수수료 및 정산 조건)
사무위탁 대행 수수료 및 실제 경비 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 계약의 체결을 증명하기 위하여 계약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "위탁 사무 범위", "content": "상업장부 작성, 세무 신고 지원, 법인 인감 관리, 은행 계좌 출납 실무"},
                    {"section": "제2조", "title": "자금 통제 및 승인", "content": "위탁자 대표이사의 사전 결재 라인 및 예산 집행 승인 한도"},
                    {"section": "제3조", "title": "자료 보관", "content": "회계 전표 및 세무 관련 증빙 서류의 보존 의무"},
                    {"section": "제4조", "title": "보고 의무", "content": "월별 재무제표 및 자금 일보 작성 보고"}
                ],
                "review_checklist": [
                    {"id": "RC-ADM-001", "clause": "자금 횡령 리스크", "check_point": "수탁회사 단독으로 송금을 집행할 수 없도록 OTP/인감 이원화 장치가 계약적으로 포함되어 있는가"},
                    {"id": "RC-ADM-002", "clause": "손해배상", "check_point": "신고 누락 또는 지연으로 가산세 등 가산금 발생 시 수탁사의 전액 배상 규정이 있는가"},
                    {"id": "RC-ADM-003", "clause": "해지 사유", "check_point": "신뢰 훼손 또는 서비스 불만족 시 위탁자가 비교적 단기에 해지(예: 30일 전 통지)할 수 있는 조항이 마련되어 있는가"}
                ]
            },
            {
                "code": "LEASE",
                "name_ko": "임대차 계약",
                "name_en": "Land Lease Agreement",
                "category": "개발",
                "description": "태양광 모듈 설치나 풍력 터빈 타워가 들어설 토지, 옥상 등을 최장 20~30년간 사용하기 위해 체결하는 임대차 계약서",
                "template_body": """# 토지/건물 임대차 계약서 (Land Lease Agreement)

본 임대차 계약(이하 "본 계약")은 임대인과 임차인(이하 [계약 당사자]) 간에 재생에너지 발전 설비 설치를 위한 부지 사용을 위하여 체결된다.

## 제1조 (목적 및 대상)
임대인은 본 사업([계약 용량] 규모)의 설치를 위해 소유 부지(지번: [부지 주소])를 임대하고, 임차인은 이를 임차하여 사용한다.

## 제2조 (임대차 기간)
본 임대차 기간은 사용 개시일로부터 [계약 기간]으로 한다.

## 제3조 (임대료 및 정산 조건)
임대료 산정 방식, 지급 시기 및 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 계약의 체결을 증명하기 위하여 계약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "임대차 목적물", "content": "대상 토지(건물) 면적 및 사용 구역 지번"},
                    {"section": "제2조", "title": "인허가 협조", "content": "개발행위 허가, 토지 사용 승낙서 발급 등 임대인의 인허가 협조 의무"},
                    {"section": "제3조", "title": "원상 복구", "content": "계약 종료 시 설비 철거 및 지목 원상복구 의무 분담"},
                    {"section": "제4조", "title": "지상권 설정", "content": "금융기관 PF 요건 충족을 위한 지상권 등 등기 설정 협조"}
                ],
                "review_checklist": [
                    {"id": "RC-LEA-001", "clause": "지상권 설정", "check_point": "임대인이 지상권 설정 등 금융약정 담보 요구 사항에 무조건적으로 협조하도록 규정되어 있는가"},
                    {"id": "RC-LEA-002", "clause": "사용료 기산 시점", "check_point": "임대료 발생 개시가 계약 체결일이 아닌 상업운전개시일(COD) 또는 인허가 획득 시점부터 산정되도록 명시되어 있는가"},
                    {"id": "RC-LEA-003", "clause": "중도 해지 및 매수", "check_point": "인허가 반려 등 사업 불가 판단 시 임차인이 불이익 없이 해지할 수 있으며 임대인의 사망/토지 매각 시에도 계약 승계가 확실히 보장되는가"}
                ]
            },
            {
                "code": "GSVC",
                "name_ko": "일반용역 계약",
                "name_en": "General Service Agreement",
                "category": "공통",
                "description": "기타 일반 조문 검토, 법률/회계/경영 자문 등 특정 분야에 한정되지 않는 범용 용역 계약서",
                "template_body": """# 일반용역계약서 (General Service Agreement)

본 용역계약(이하 "본 계약")은 발주자와 용역 수행사(이하 [계약 당사자]) 간에 일반 용역 업무의 제공 및 수행을 위해 체결된다.

## 제1조 (목적)
수행사는 본 사업([계약 용량])과 관련하여 발주자가 위탁하는 일반 용역을 수행하고 보고서를 납품한다.

## 제2조 (용역 기간)
본 계약에 따른 용역 수행 기간은 체결일로부터 [계약 기간]으로 한다.

## 제3조 (용역 대금 및 정산 조건)
용역 단가, 마일스톤별 대금 지급 시기 및 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 계약의 체결을 증명하기 위하여 계약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "용역 범위", "content": "용역의 내용, 인도해야 하는 결과물 요건"},
                    {"section": "제2조", "title": "검수", "content": "납품 완료 후 발주자의 검수 절차 및 보완 요구권"},
                    {"section": "제3조", "title": "지적재산권", "content": "산출물의 소유권 및 사용 허가 권한"},
                    {"section": "제4조", "title": "계약 해지", "content": "채무 불이행 시 시정 요구 및 중도 계약 해지 조항"}
                ],
                "review_checklist": [
                    {"id": "RC-GSV-001", "clause": "지적재산권", "check_point": "납품한 보고서 및 설계 결과물의 지적재산권이 대금 완납 시 발주자에게 완전히 귀속되는가"},
                    {"id": "RC-GSV-002", "clause": "검수 조건", "check_point": "검수 기간이 영업일 기준 최대 10~15일 이내로 제한되어 있으며 묵시적 검수완료 규정이 있는가"},
                    {"id": "RC-GSV-003", "clause": "배상 한도", "check_point": "용역사의 고의가 없는 한 배상 책임 한도가 총 계약 금액 범위 내로 조율되어 있는가"}
                ]
            },
            {
                "code": "PSVC",
                "name_ko": "인허가용역 계약",
                "name_en": "Permit Service Agreement",
                "category": "개발",
                "description": "발전사업허가, 개발행위허가, 환경영향평가 등 까다로운 재생에너지 인허가 획득을 위해 지역 설계사무소와 체결하는 계약서",
                "template_body": """# 인허가용역 계약서 (Permit Service Agreement)

본 인허가용역 계약(이하 "본 계약")은 발주자와 용역 수행사(이하 [계약 당사자]) 간에 인허가 업무 대행 및 기술 지원을 위해 체결된다.

## 제1조 (목적)
수행사는 본 사업([계약 용량])과 관련하여 발전사업허가, 개발행위허가 등 정부 및 지자체 관련 인허가를 획득하기 위한 설계 및 대행 업무를 수행한다.

## 제2조 (용역 기간)
본 용역의 유효 기간은 계약 체결일로부터 각 목적 인허가가 최종 통과 및 교부되는 [계약 기간]까지로 한다.

## 제3조 (대금 지급 및 성공보수 정산 조건)
인허가 단계별 지급 일정 및 최종 허가 취득에 따른 성공보수 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 계약의 체결을 증명하기 위하여 계약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "인허가 수행 대상", "content": "개발행위허가, 소규모 환경영향평가, 사전재해영향성검토 등"},
                    {"section": "제2조", "title": "기성 마일스톤", "content": "신청 접수 시, 주민 의견 수렴 완료 시, 허가장 교부 시"},
                    {"section": "제3조", "title": "주민 민원 협조", "content": "민원 발생 시 발주자와 수행사의 역할 분담 및 민원 비용 해결 귀속"},
                    {"section": "제4조", "title": "계약 반려 시", "content": "행정 기관에 의해 허가 불허 결정 시 귀책사유 판별 및 대금 정산"}
                ],
                "review_checklist": [
                    {"id": "RC-PSV-001", "clause": "성공 보수", "check_point": "인허가 보류 또는 반려 시 기성금 반환 한도가 명확하고, 성공보수가 최종 허가장 수령 완료 기준으로 명확히 설정되었는가"},
                    {"id": "RC-PSV-002", "clause": "민원 해결", "check_point": "지역 주민 민원 처리를 용역사에만 미뤄두지 않도록 발주자의 대관 협조 역할 범위가 명기되었는가"},
                    {"id": "RC-PSV-003", "clause": "허가 보장 불가", "check_point": "행정심의 반려 시 용역사의 손해배상 책임 면책 사유(정부 정책 변동, 불가항력 등)가 타당하게 반영되었는가"}
                ]
            },
            {
                "code": "PPA_REC",
                "name_ko": "PPA/REC 거래계약",
                "name_en": "Power Purchase Agreement & REC Contract",
                "category": "매출",
                "description": "발전소에서 생산된 전력(SMP) 및 신재생에너지 공급인증서(REC)를 한전, 발전자회사 또는 일반 RE100 기업에 판매하는 핵심 매출 계약서",
                "template_body": """# 전력판매계약 및 REC 거래계약서 (PPA / REC Contract)

본 판매계약(이하 "본 계약")은 발전사업자(매도인)와 매수인(이하 [계약 당사자]) 간에 생산 전력 및 공급인증서의 매매 거래를 위하여 체결된다.

## 제1조 (목적)
매도인은 본 사업([계약 용량] 규모)에서 생산하는 전력(SMP) 및 발생 신재생에너지 공급인증서(REC)를 매수인에게 인도하고 매수인은 이를 전량 매수한다.

## 제2조 (계약 기간)
본 계약에 따른 전력 공급 개시일로부터 상업 거래 유효기간은 [계약 기간]으로 한다.

## 제3조 (거래 단가 및 정산 조건)
SMP/REC 고정가격, 단가 조정 메커니즘, 매월 대금 청구 및 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 계약의 체결을 증명하기 위하여 계약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "매매 대상", "content": "전력량(MWh) 및 공급인증서(REC) 수량"},
                    {"section": "제2조", "title": "단가 결정", "content": "고정가격계약 단가 또는 시장가격(SMP+REC) 연동 단가"},
                    {"section": "제3조", "title": "인도 및 계량", "content": "전력량계 계량 시점, 귀속 송전선로 손실률"},
                    {"section": "제4조", "title": "계통 접속 제한", "content": "한전 송배전선로 제약으로 인한 강제 출력 제어(Curtailment) 발생 시 보상 여부"}
                ],
                "review_checklist": [
                    {"id": "RC-PPA-001", "clause": "출력 제어 리스크", "check_point": "지방 계통 포화에 따른 한전의 출력 차단 발생 시 전력 판매 미달분에 대한 매수인의 대금 지불(Take-or-pay) 또는 손실 보상 조건이 합리적인가"},
                    {"id": "RC-PPA-002", "clause": "정산 및 체납", "check_point": "매수인의 대금 지급 연체 시 지연이자 규정 및 지속적인 연체 시 계약 해지권과 REC 회수권이 확실한가"},
                    {"id": "RC-PPA-003", "clause": "규제 변동", "check_point": "정부의 전력시장 규칙 개정(예: SMP 상한제 등) 또는 REC 가중치 변동 시 가격 조정 합의 절차가 규정되어 있는가"}
                ]
            },
            {
                "code": "NDA",
                "name_ko": "비밀유지계약",
                "name_en": "Non-Disclosure Agreement",
                "category": "공통",
                "description": "프로젝트 협의 초기 단계에서 상호 제공하는 사업권 정보, 재무 정보, 기술 자료의 기밀을 유지하기 위한 계약서",
                "template_body": """# 비밀유지계약서 (Non-Disclosure Agreement)

본 계약(이하 "본 계약")은 정보 제공자와 정보 수령자(이하 [계약 당사자]) 간에 재생에너지 개발 사업 제안과 관련하여 상호 제공하는 비밀 정보의 유출을 방지하기 위하여 체결된다.

## 제1조 (목적)
본 계약은 본 사업([계약 용량])과 관련하여 양 당사자가 교환하는 비밀 정보의 오용 및 제3자 누설 방지를 규정한다.

## 제2조 (비밀유지 기간)
비밀 정보 보호 및 정보 기밀 유지 의무 기간은 본 계약 체결일 또는 정보 수령일로부터 [계약 기간]으로 한다.

## 제3조 (반환 및 폐기 정산 조건)
정보의 이용 목적 종료 시 원본의 반환, 복사본 폐기 확약서 제출 등 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 계약의 체결을 증명하기 위하여 계약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "비밀 정보 정의", "content": "서면, 구두, 전산 매체로 제공된 일체의 기술적·재무적·경영 정보"},
                    {"section": "제2조", "title": "이용 목적 제한", "content": "본 사업 공동 참여 여부 검토 목적으로만 사용 의무"},
                    {"section": "제3조", "title": "예외 정보", "content": "이미 공지된 정보, 정보 제공과 무관하게 독자 개발한 정보 등"},
                    {"section": "제4조", "title": "손해 배상", "content": "기밀 유지 위반으로 손해 발생 시 손해액 입증 및 배상 의무"}
                ],
                "review_checklist": [
                    {"id": "RC-NDA-001", "clause": "유효 기간", "check_point": "프로젝트 협의가 중단된 이후에도 최소 2~3년 동안 기밀 유지 의무가 계속 유효하게 지속되는가"},
                    {"id": "RC-NDA-002", "clause": "손해배상 입증", "check_point": "위반 시 손해배상 입증이 매우 어려우므로 사전에 위약벌 또는 손해배상액의 예정액이 합리적으로 들어가 있는가"},
                    {"id": "RC-NDA-003", "clause": "임직원 책임", "check_point": "정보를 공유받을 수 있는 임직원 및 자문기관의 범위 규정과 연대책임 조항이 들어가 있는가"}
                ]
            },
            {
                "code": "MOU",
                "name_ko": "양해각서",
                "name_en": "Memorandum of Understanding",
                "category": "공통",
                "description": "공식 본계약 체결 전 상호 신뢰 하에 사업 기본 방침을 확인하고 타당성 조사를 수행하기 위해 약정하는 양해각서",
                "template_body": """# 양해각서 (Memorandum of Understanding)

본 양해각서(이하 "본 양해각서")는 [계약 당사자] 간에 재생에너지 개발 사업을 성공적으로 추진하기 위한 상호 협력 사항을 정하기 위해 체결된다.

## 제1조 (목적)
당사자들은 본 사업([계약 용량])의 타당성 검토 및 본계약 체결 협상을 신의성실에 기반하여 진행할 것을 합의한다.

## 제2조 (유효 기간)
본 양해각서의 유효기간은 체결일로부터 [계약 기간] 또는 본계약 체결 시까지로 한다.

## 제3조 (법적 구속력 유무 및 정산 조건)
본 양해각서 조항 중 법적 구속력을 가지는 조항(기밀유지, 독점교섭 등) 및 비용 정산 조건은 다음과 같이 정한다.
[매매 단가 / 정산 조건]

## 제4조 (기타 조건)
[기타 관철 조건]

본 양해각서의 체결을 증명하기 위하여 협약서 2부를 작성하여 서명날인 후 각각 1부씩 보관한다.
""",
                "key_term_schema": [
                    {"name": "parties", "label": "계약 당사자", "type": "text", "required": True},
                    {"name": "capacity", "label": "계약 용량", "type": "text", "required": False},
                    {"name": "period", "label": "계약 기간", "type": "text", "required": True},
                    {"name": "price", "label": "매매 단가 / 정산 조건", "type": "text", "required": True},
                    {"name": "etc", "label": "기타 관철 조건", "type": "textarea", "required": False}
                ],
                "standard_clauses": [
                    {"section": "제1조", "title": "협력 분야", "content": "인허가 동향 파악, 부지 조사, 사업 타당성 분석 공동 수행"},
                    {"section": "제2조", "title": "법적 구속력 배제", "content": "기밀유지, 배타적 교섭권 등 일부 조항 외에는 법적 구속력을 부여하지 않음"},
                    {"section": "제3조", "title": "독점 교섭권", "content": "협약 기간 동안 타사와 동일 목적 협약 체결 및 협상 금지"},
                    {"section": "제4조", "title": "비용의 개별 부담", "content": "타당성 조사 단계에서 발생한 각자 소요 비용의 귀속"}
                ],
                "review_checklist": [
                    {"id": "RC-MOU-001", "clause": "법적 구속력 배제", "check_point": "본 계약이 아님에도 불구하고 전체 조항에 법적 구속력이 발생하는 독소 표현(Shall 등)이 없는지 확인하였는가"},
                    {"id": "RC-MOU-002", "clause": "독점 교섭권 기간", "check_point": "독점적으로 교섭권을 가질 수 있는 기한(예: 3~6개월)이 타당하며, 상대의 불성실 교섭 시 즉시 해제 가능한가"},
                    {"id": "RC-MOU-003", "clause": "실사 권한", "check_point": "타당성 분석을 위해 양 당사자가 보유한 기존 기초 인허가 데이터를 상호 성실히 교환하도록 명시하였는가"}
                ]
            }
        ]

        created_count = 0
        updated_count = 0

        for data in templates_data:
            template, created = ContractTemplate.objects.update_or_create(
                code=data["code"],
                defaults={
                    "name_ko": data["name_ko"],
                    "name_en": data["name_en"],
                    "category": data["category"],
                    "description": data["description"],
                    "template_body": data["template_body"],
                    "key_term_schema": data["key_term_schema"],
                    "standard_clauses": data["standard_clauses"],
                    "review_checklist": data["review_checklist"],
                    "is_active": True,
                }
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"16종 표준 계약서 시드 완료: {created_count}개 생성, {updated_count}개 업데이트"
        ))
