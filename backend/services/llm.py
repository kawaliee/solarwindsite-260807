"""
LLM 어댑터 — 기 개발 LLM API 연동 (OpenAI-호환) + Mock 모드
"""
import logging
import re
import json
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_client():
    """OpenAI-호환 클라이언트 생성"""
    if settings.LLM_MODEL == 'mock' or not settings.LLM_API_BASE:
        return None

    from openai import OpenAI
    return OpenAI(
        base_url=settings.LLM_API_BASE,
        api_key=settings.LLM_API_KEY or 'dummy',
    )


def generate_answer(question: str, context: str = '', history: list = None,
                    use_internal_docs: bool = True) -> dict:
    """
    RAG 챗봇 답변 생성 (Hybrid RAG with Tavily Tool Calling)
    """
    client = _get_client()

    if client is None:
        return _mock_answer(question, context, use_internal_docs)

    system_prompt = """너는 자사 내부 문서(계약서, 보고자료 등) 벡터 DB와 외부 웹 검색 API(Tavily)를 유기적으로 결합하여, 사용자 질문에 대해 가장 정확하고 신뢰할 수 있는 답변을 제공하는 '하이브리드 RAG 에이전트'이다.

[CRITICAL RULE FOR UNVERIFIED INFORMATION - 미확인 정보 절대 금지 규칙]
- **제공된 [내부 참조 자료]에 질문에 대한 구체적이고 신뢰할 수 있는 사실 관계나 수치(예: 특정 계약 금액, 금리, 대주단 등)가 명확하게 기재되어 있지 않다면, 절대로 어설프게 추측하거나 임의로 지어내어(Hallucination) 답변하지 마십시오.**
- **제대로 확인되지 않는 정보에 대해서는 반드시 "제공된 내부 DB 문서에서 관련 정보를 정확하게 찾지 못했습니다."라는 취지로 명확하고 정중하게 선을 그어 사용자에게 안내하십시오. 다만, 비록 단일 숫자로 바로 정의되지 않더라도 문서 내에 조건별 정산단가·기준가격 등이나 관련 보증 발전시간 계산 조건 등의 명확한 수식/수치 기준이 실재한다면 이를 누락하지 말고 상세히 발췌하여 설명하십시오.**
- **계약가격·정산단가·보증사항(보장 발전시간, 연간 보장공급량, 감소기준 등)처럼 계약서·합의서에 수치나 수식 기준이 존재하는 항목은, 반드시 [내부 참조 자료]에 실제로 제시된 값·조건만을 근거로 정확히 발췌하여 답변하십시오. 자료에 없는 수치는 임의로 단정하지 말고, 조건부 단가(예: SMP 연동, 초과 시 별도 단가 등)나 서면합의 예정 등 유보 조건이 문서에 있으면 그대로 함께 밝히십시오. 프로젝트 별칭과 정식 법인명이 다를 수 있으니 [내부 참조 자료] 상단의 동의어 사전을 참고하여 명칭 불일치로 정보를 누락·기각하지 마십시오.**
- **[내부 참조 자료]에 '거래단가/기준가격/계약단가 = N원/kWh'(예: 169.8원/kWh) 같은 구체적인 단가 수치가 실재한다면, 조항 번호(예: 제5.2조)만 인용하고 넘어가지 말고 그 수치(N원/kWh)를 답변에 명시적으로 제시하십시오. 단, 그 수치가 자료에 없으면 지어내지 마십시오.**

[CRITICAL RULE FOR TOOL CALLS - 필수 지침]
- 제공된 [내부 참조 자료]에 사용자의 질문과 직접적으로 관련된 구체적인 정보(예: 계약 금액, 프로젝트 명칭, 일정, 계약 조건 등)가 일부라도 포함되어 있다면, **절대로 `search_web_via_tavily` 도구를 호출해서는 안 됩니다.**
- 제공된 [내부 참조 자료]의 내용만으로도 질문에 핵심적으로 답변이 가능하다면, **오직 내부 문서 내용만으로 답변을 구성**하고 웹 검색 도구는 호출하지 마십시오.

Step 2: 외부 웹 검색 라우팅 (Fallback & Parallel Web Search)
- 내부 자료가 불충분하거나, 최신 법령, 외부 표준 약관, 외부 트렌드 등 외부 데이터가 반드시 필요한 경우 `search_web_via_tavily` 도구를 호출한다.

Step 3: 정보 종합 및 답변 생성
- 내부 데이터와 외부 데이터를 종합하되, 충돌 시 내부 자사 기준을 우선시한다.
- 알 수 없는 정보는 임의로 지어내지(Hallucination) 말고 솔직하게 모른다고 답하라.

[출처 표기 규칙]
- 내부 문서 활용 시: `[출처: 내부문서 - {문서명}]`
- 외부 웹 검색 활용 시: `[출처: Tavily Search - {URL 또는 도메인}]`
- 전문적이고 격식 있는 비즈니스 톤앤매너를 유지하라.
한국어로 답변하라."""

    if use_internal_docs and context:
        system_prompt += f"\n\n[내부 참조 자료]\n{context}"

    messages = [{"role": "system", "content": system_prompt}]

    if history:
        for msg in history[-10:]:
            messages.append({"role": msg['role'], "content": msg['content']})

    messages.append({"role": "user", "content": question})

    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_web_via_tavily",
                "description": "외부 웹 정보를 실시간으로 검색하여 관련 요약문과 URL을 반환합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "검색할 키워드 또는 문장"
                        },
                        "search_depth": {
                            "type": "string",
                            "enum": ["basic", "advanced"],
                            "description": "기본 검색(basic) 또는 심층 검색(advanced)"
                        }
                    },
                    "required": ["query"]
                }
            }
        }
    ]

    try:
        import json
        from services.tavily_service import search_web_via_tavily

        # Tool Calling Loop (최대 3회 제한)
        MAX_ITERATIONS = 3
        prompt_tokens = 0
        completion_tokens = 0
        for _ in range(MAX_ITERATIONS):
            response = client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=messages,
                tools=tools,
                temperature=0.3,
                max_tokens=2048,
            )
            
            msg = response.choices[0].message
            if response.usage:
                prompt_tokens += response.usage.prompt_tokens
                completion_tokens += response.usage.completion_tokens
            
            if msg.tool_calls:
                messages.append(msg.model_dump()) # 보조자 응답 추가 (tool_calls 포함)
                for tool_call in msg.tool_calls:
                    if tool_call.function.name == "search_web_via_tavily":
                        args = json.loads(tool_call.function.arguments)
                        logger.info(f"LLM Tool Call: search_web_via_tavily({args})")
                        tool_result = search_web_via_tavily(args.get("query"), args.get("search_depth", "basic"))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.function.name,
                            "content": json.dumps(tool_result, ensure_ascii=False)
                        })
                # 도구 결과를 추가했으니 루프를 돌아 다시 LLM 호출
            else:
                # 일반 텍스트 응답 도착 -> 루프 종료
                return {
                    'content': msg.content,
                    'model': response.model,
                    'token_usage': {
                        'prompt_tokens': prompt_tokens,
                        'completion_tokens': completion_tokens,
                    },
                }

        # 제한 초과 시 마지막 응답 반환
        return {
            'content': messages[-1].get("content", "도구 호출이 너무 많아 답변을 완료하지 못했습니다."),
            'model': settings.LLM_MODEL,
            'token_usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens},
        }

    except Exception as e:
        logger.error(f'LLM API error: {e}')
        return _mock_answer(question, context, use_internal_docs)


def generate_answer_stream(question: str, context: str = '', history: list = None,
                           use_internal_docs: bool = True):
    """
    RAG 챗봇 답변 생성 (Streaming & Hybrid RAG with Tavily Tool Calling)
    SSE(Server-Sent Events)를 위해 청크를 yield 합니다.
    """
    client = _get_client()

    if client is None:
        yield json.dumps({"content": "Mock 모드에서는 스트리밍을 지원하지 않습니다."})
        return

    system_prompt = """너는 자사 내부 문서(계약서, 보고자료 등) 벡터 DB와 외부 웹 검색 API(Tavily)를 유기적으로 결합하여, 사용자 질문에 대해 가장 정확하고 신뢰할 수 있는 답변을 제공하는 '하이브리드 RAG 에이전트'이다.

[CRITICAL RULE FOR UNVERIFIED INFORMATION - 미확인 정보 절대 금지 규칙]
- **제공된 [내부 참조 자료]에 질문에 대한 구체적이고 신뢰할 수 있는 사실 관계나 수치(예: 특정 계약 금액, 금리, 대주단 등)가 명확하게 기재되어 있지 않다면, 절대로 어설프게 추측하거나 임의로 지어내어(Hallucination) 답변하지 마십시오.**
- **제대로 확인되지 않는 정보에 대해서는 반드시 "제공된 내부 DB 문서에서 관련 정보를 정확하게 찾지 못했습니다."라는 취지로 명확하고 정중하게 선을 그어 사용자에게 안내하십시오. 다만, 비록 단일 숫자로 바로 정의되지 않더라도 문서 내에 조건별 정산단가·기준가격 등이나 관련 보증 발전시간 계산 조건 등의 명확한 수식/수치 기준이 실재한다면 이를 누락하지 말고 상세히 발췌하여 설명하십시오.**
- **계약가격·정산단가·보증사항(보장 발전시간, 연간 보장공급량, 감소기준 등)처럼 계약서·합의서에 수치나 수식 기준이 존재하는 항목은, 반드시 [내부 참조 자료]에 실제로 제시된 값·조건만을 근거로 정확히 발췌하여 답변하십시오. 자료에 없는 수치는 임의로 단정하지 말고, 조건부 단가(예: SMP 연동, 초과 시 별도 단가 등)나 서면합의 예정 등 유보 조건이 문서에 있으면 그대로 함께 밝히십시오. 프로젝트 별칭과 정식 법인명이 다를 수 있으니 [내부 참조 자료] 상단의 동의어 사전을 참고하여 명칭 불일치로 정보를 누락·기각하지 마십시오.**
- **[내부 참조 자료]에 '거래단가/기준가격/계약단가 = N원/kWh'(예: 169.8원/kWh) 같은 구체적인 단가 수치가 실재한다면, 조항 번호(예: 제5.2조)만 인용하고 넘어가지 말고 그 수치(N원/kWh)를 답변에 명시적으로 제시하십시오. 단, 그 수치가 자료에 없으면 지어내지 마십시오.**

[Operational Workflow & Routing Rules]
Step 1: 내부 벡터 DB 우선 검색 (Internal Vector DB Search)
- 제공된 [내부 참조 자료]의 내용이 사용자의 질문을 해결하기에 충분한지 판단한다.
- 충분한 경우 외부 검색을 호출하지 않고 내부 문서를 기반으로 정확한 답변을 생성한다.

Step 2: 외부 웹 검색 라우팅 (Fallback & Parallel Web Search)
- 내부 자료가 불충분하거나, 최신 법령, 외부 표준 약관, 외부 트렌드 등 외부 데이터가 반드시 필요한 경우 `search_web_via_tavily` 도구를 호출한다.
- 복합적인 질문은 2~3개의 하위 키워드로 나누어 검색 도구를 호출할 수 있다.

Step 3: 정보 종합 및 답변 생성
- 내부 데이터와 외부 데이터를 종합하되, 충돌 시 내부 자사 기준을 우선시한다.
- 알 수 없는 정보는 임의로 지어내지(Hallucination) 말고 솔직하게 모른다고 답하라.

[출처 표기 규칙]
- 내부 문서 활용 시: `[출처: 내부문서 - {문서명}]`
- 외부 웹 검색 활용 시: `[출처: Tavily Search - {URL 또는 도메인}]`
- 전문적이고 격식 있는 비즈니스 톤앤매너를 유지하라.
- **내부 DB 문서에 명확한 수치나 계약 금액 팩트가 누락된 경우, 반드시 "제공된 내부 DB 문서에서 {질문 주제}에 대한 정확한 정보를 찾지 못했습니다."라고 엄격히 답변할 것을 거듭 지시한다.**
한국어로 답변하라."""

    if use_internal_docs and context:
        system_prompt += f"\n\n[내부 참조 자료]\n{context}"

    messages = [{"role": "system", "content": system_prompt}]

    if history:
        for msg in history[-10:]:
            messages.append({"role": msg['role'], "content": msg['content']})

    messages.append({"role": "user", "content": question})

    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_web_via_tavily",
                "description": "외부 웹 정보를 실시간으로 검색하여 관련 요약문과 URL을 반환합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "검색할 키워드 또는 문장"
                        },
                        "search_depth": {
                            "type": "string",
                            "enum": ["basic", "advanced"],
                            "description": "기본 검색(basic) 또는 심층 검색(advanced)"
                        }
                    },
                    "required": ["query"]
                }
            }
        }
    ]

    try:
        import json
        from services.tavily_service import search_web_via_tavily

        MAX_ITERATIONS = 3
        for iteration in range(MAX_ITERATIONS):
            # 스트리밍 요청
            response = client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=messages,
                tools=tools,
                temperature=0.3,
                max_tokens=2048,
                stream=True
            )
            
            tool_calls = {}
            content_accumulated = ""
            
            for chunk in response:
                delta = chunk.choices[0].delta
                
                # 일반 텍스트 응답 스트리밍
                if delta.content:
                    content_accumulated += delta.content
                    yield json.dumps({"content": delta.content}) + "\n"
                    
                # Tool Call 정보 누적
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        if tc.index not in tool_calls:
                            tool_calls[tc.index] = {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.function.name, "arguments": ""}
                            }
                        if tc.function.arguments:
                            tool_calls[tc.index]["function"]["arguments"] += tc.function.arguments

            # 스트림이 끝나고 Tool Call이 있었는지 확인
            if tool_calls:
                # 보조자 응답 추가 (Tool Calls)
                assistant_msg = {"role": "assistant", "tool_calls": list(tool_calls.values())}
                messages.append(assistant_msg)
                
                for tc_idx, tc_data in tool_calls.items():
                    if tc_data["function"]["name"] == "search_web_via_tavily":
                        args = json.loads(tc_data["function"]["arguments"])
                        logger.info(f"LLM Tool Call (Stream): search_web_via_tavily({args})")
                        tool_result = search_web_via_tavily(args.get("query"), args.get("search_depth", "basic"))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_data["id"],
                            "name": tc_data["function"]["name"],
                            "content": json.dumps(tool_result, ensure_ascii=False)
                        })
                # 도구 결과 추가 후 루프를 다시 돌아 LLM 호출
            else:
                # 일반 텍스트가 끝났으므로 스트리밍 종료
                break
                
    except Exception as e:
        logger.error(f'LLM Streaming API error: {e}')
        yield json.dumps({"content": f"\n[오류 발생: {e}]"}) + "\n"


def _mock_answer(question: str, context: str = '', use_internal_docs: bool = True) -> dict:
    """Mock 응답 생성"""
    if use_internal_docs and context:
        content = f"""사내 자료를 검토한 결과를 안내드립니다.

{question}에 대해 다음과 같이 정리하였습니다:

참조한 사내 자료를 기반으로, 주요 포인트를 요약하면:

1. 관련 자료에서 확인된 핵심 내용이 있습니다.
2. 세부 사항은 출처 자료를 직접 확인하시길 권장합니다.
3. 추가 질문이 있으시면 말씀해주세요.

*이 답변은 Mock 모드로 생성되었습니다. 실제 LLM 연동 후 정확한 답변이 제공됩니다.*"""
    else:
        content = f"""질문해 주셔서 감사합니다.

"{question}"에 대해 답변드리겠습니다.

현재 Mock 모드로 운영 중이므로 일반적인 안내만 가능합니다.
사내 자료 기반의 정확한 답변을 위해 LLM API 연동이 필요합니다.

*이 답변은 Mock 모드로 생성되었습니다.*"""

    return {
        'content': content,
        'model': 'mock',
        'token_usage': None,
    }


def generate_contract_draft(template, key_terms: dict) -> dict:
    """
    계약서 신규 생성 (K-1) - RAG 연동 고도화
    """
    client = _get_client()

    if client is None:
        return {'content': _mock_contract_draft(template, key_terms)}

    # 1. RAG 검색 수행 (실제 DB의 유사 계약서 조회)
    try:
        from services.rag import search_contracts_for_drafting
        rag_result = search_contracts_for_drafting(template.code, key_terms, top_k=5)
        reference_context = rag_result.get('context', '')
        sources = rag_result.get('sources', [])
    except Exception as e:
        logger.error(f'RAG search for drafting failed: {e}')
        reference_context = ''
        sources = []

    # 2. 종류별 필수/표준 조항 체크리스트 정의
    checklists = {
        'SHA': "이사 지명권, 우선매수권(ROFR), 동반매도권(tag-along), 강제매도권(drag-along), 의결권 약정, 신주인수권, 교착상태(deadlock) 해소, 진술·보장, 경업금지, 주식양도 제한",
        'SPA': "매매대금 및 대금조정(price adjustment), 선행조건(CP), 진술·보장, 손해배상(indemnification), 경업금지, 완결(closing) 절차, 해제 사유(termination), MAC/MAE 조항",
        'NDA': "비밀정보 정의·범위, 사용 목적 제한, 유지기간, 예외사유, 반환·파기 의무, 잔존의무(residual), 위반 시 구제수단",
        'JDA': "개발 범위(scope of work)·역할 분담, 비용·자원 분담, 지식재산권(IP) 귀속 및 실시권, 배경 IP vs 성과 IP 구분, 마일스톤·산출물, 성과 활용·상업화 권리, 비밀유지, 종료 시 처리",
        'MOU': "목적, 협력 범위, 구속력 유무(binding/non-binding 명시), 독점·비독점 여부, 유효기간, 비용 부담, 후속 본계약 전환 조항",
        'DSA': "용역 범위(scope of work)·산출물(deliverables) 정의, 개발 일정·마일스톤, 검수(acceptance) 기준 및 절차, 용역대금 및 지급 조건(마일스톤·검수 연동), 지식재산권 귀속(성과물 IP), 배경 IP·오픈소스·제3자 IP 처리, 소스코드·산출물 인도 및 이관, 변경관리(change order), 하자보수·유지보수 기간 및 책임, 재위탁(하도급) 제한, 투입 인력·핵심인력 교체 제한, 비밀유지, 손해배상·책임제한, 검수 지연·개발 지연 시 처리(지연배상), 종료 시 산출물 처리",
        'SLA': "서비스 지표(가용성·응답시간 등) 정의, 목표 수준(SLO), 측정 방법, 서비스 크레딧/페널티, 제외 사유(exclusions), 보고·리뷰 주기, 에스컬레이션 절차",
        'LEASE': "임대목적물 특정, 보증금·차임·관리비, 계약기간·갱신, 원상복구, 수선·유지보수 책임 분담, 전대 제한, 제세공과금, 해지 사유, 명도·연체 시 처리, 확정일자/대항력 관련",
        'EPC': "업무 범위·설계 책임, 완공·인도 일정, 지연배상(LD), 성능보증·성능시험, 하자보수·하자담보(warranty), 변경(variation/change order), 대금지급 마일스톤, 불가항력, 위험부담·소유권 이전, 보험, 준거법·분쟁해결",
        'OM': "운영·유지보수 범위, KPI/가용성 보증, 정기·비정기 정비, 예비품·소모품 부담, 성능보증 및 페널티/보너스, 보고 의무, 계약기간·갱신, 책임제한, 인수인계",
        'PPA_REC': "계약전력·공급량, 가격 구조(고정/변동/에스컬레이션), 인수·인도지점, take-or-pay 여부, 계량·정산, 공급개시일(COD), 성능·가용성 보증, 불가항력, 정부정책 변경(change in law), 신용보강·담보, 해지 및 정산"
    }
    
    checklist_prompt = checklists.get(template.code, "해당 계약의 통상적인 필수 및 표준 조항 일체")

def mask_confidential_info(text: str) -> str:
    """참조 조항 내의 특정 기업명, 계약금액, 기밀일자 등 민감 정보를 마스킹 및 일반화"""
    # 1. 기업명/기관명 마스킹
    text = re.sub(r'[가-힣A-Za-z0-9\-]+주식회사|주식회사\s*[가-힣A-Za-z0-9\-]+|[가-힣A-Za-z0-9\-]+\(주\)|\(주\)\s*[가-힣A-Za-z0-9\-]+', '[회사 A]', text)
    # 2. 계약 금액 마스킹 (원화/외화)
    text = re.sub(r'일금\s*[가-힣0-9\s,\(\)]+원|금\s*[가-힣0-9\s,\(\)]+원|\b\d{1,3}(?:,\d{3})+(?:\s*원|\s*USD|\s*KRW)', '[계약금액]', text)
    # 3. 날짜 마스킹
    text = re.sub(r'\b\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일', '[계약일]', text)
    # 4. 주민등록번호 및 주소 대략적 마스킹
    text = re.sub(r'\b\d{6}\s*-\s*[1-4]\d{6}\b', '[주민등록번호]', text)
    return text


def generate_contract_draft(template, key_terms: dict) -> dict:
    """
    계약서 신규 생성 (K-1) - 2단계 생성(Outline -> Clause-by-Clause) 및 Critic Pass 고도화
    """
    client = _get_client()

    if client is None:
        return {'content': _mock_contract_draft(template, key_terms), 'sources': []}

    # 1. 종류별 필수/표준 조항 체크리스트 정의
    checklists = {
        'SHA': "이사 지명권, 우선매수권(ROFR), 동반매도권(tag-along), 강제매도권(drag-along), 의결권 약정, 신주인수권, 교착상태(deadlock) 해소, 진술·보장, 경업금지, 주식양도 제한",
        'SPA': "매매대금 및 대금조정(price adjustment), 선행조건(CP), 진술·보장, 손해배상(indemnification), 경업금지, 완결(closing) 절차, 해제 사유(termination), MAC/MAE 조항",
        'NDA': "비밀정보 정의·범위, 사용 목적 제한, 유지기간, 예외사유, 반환·파기 의무, 잔존의무(residual), 위반 시 구제수단",
        'JDA': "개발 범위(scope of work)·역할 분담, 비용·자원 분담, 지식재산권(IP) 귀속 및 실시권, 배경 IP vs 성과 IP 구분, 마일스톤·산출물, 성과 활용·상업화 권리, 비밀유지, 종료 시 처리",
        'MOU': "목적, 협력 범위, 구속력 유무(binding/non-binding 명시), 독점·비독점 여부, 유효기간, 비용 부담, 후속 본계약 전환 조항",
        'DSA': "용역 범위(scope of work)·산출물(deliverables) 정의, 개발 일정·마일스톤, 검수(acceptance) 기준 및 절차, 용역대금 및 지급 조건(마일스톤·검수 연동), 지식재산권 귀속(성과물 IP), 배경 IP·오픈소스·제3자 IP 처리, 소스코드·산출물 인도 및 이관, 변경관리(change order), 하자보수·유지보수 기간 및 책임, 재위탁(하도급) 제한, 투입 인력·핵심인력 교체 제한, 비밀유지, 손해배상·책임제한, 검수 지연·개발 지연 시 처리(지연배상), 종료 시 산출물 처리",
        'SLA': "서비스 지표(가용성·응답시간 등) 정의, 목표 수준(SLO), 측정 방법, 서비스 크레딧/페널티, 제외 사유(exclusions), 보고·리뷰 주기, 에스컬레이션 절차",
        'LEASE': "임대목적물 특정, 보증금·차임·관리비, 계약기간·갱신, 원상복구, 수선·유지보수 책임 분담, 전대 제한, 제세공과금, 해지 사유, 명도·연체 시 처리, 확정일자/대항력 관련",
        'EPC': "업무 범위·설계 책임, 완공·인도 일정, 지연배상(LD), 성능보증·성능시험, 하자보수·하자담보(warranty), 변경(variation/change order), 대금지급 마일스톤, 불가항력, 위험부담·소유권 이전, 보험, 준거법·분쟁해결",
        'OM': "운영·유지보수 범위, KPI/가용성 보증, 정기·비정기 정비, 예비품·소모품 부담, 성능보증 및 페널티/보너스, 보고 의무, 계약기간·갱신, 책임제한, 인수인계",
        'PPA_REC': "계약전력·공급량, 가격 구조(고정/변동/에스컬레이션), 인수·인도지점, take-or-pay 여부, 계량·정산, 공급개시일(COD), 성능·가용성 보증, 불가항력, 정부정책 변경(change in law), 신용보강·담보, 해지 및 정산"
    }
    checklist_prompt = checklists.get(template.code, "해당 계약의 통상적인 필수 및 표준 조항 일체")

    # ──────────────────────────────────────────────────────────
    # [1단계] 목차(Outline) 및 정의어(Definitions) 생성
    # ──────────────────────────────────────────────────────────
    logger.info(f"K-1 고도화: 1단계 목차 및 정의어 생성 중... (Template: {template.code})")
    outline_prompt = f"""다음 계약서 기본 정보와 입력된 Key-term을 기반으로, 이 계약서의 전체 목차(조항 리스트)와 문서 전체에 일관성 있게 사용될 정의어(Definitions) 목록을 JSON 포맷으로 생성하십시오.

[계약서 정보]
- 유형: {template.name_ko} ({template.name_en})
- 카테고리: {template.category}

[사용자 입력 Key-term]
{_format_key_terms(key_terms)}

[요구사항]
- 목차는 조항의 논리적 흐름에 맞춰 제1조부터 최종조까지 정교하게 구성하십시오.
- 각 조항에 대해 아래 분류 중 가장 적절한 조항 유형(article_type)을 반드시 하나 매핑해 주십시오:
  '비밀유지', '손해배상', '준거법', '분쟁해결', 'IP귀속', '용역대금', '검수', '계약기간/갱신', '해제/해지', '불가항력', '권리의무양도', '하도급제한', '하자보수', '이사지명권', '우선매수권', '동반매도권', '강제매도권', '교착상태', '진술보장', '경업금지', '선행조건', '기타'

반드시 아래 형식의 순수 JSON 데이터만 출력하십시오. 코드 블록(```json ```)을 포함할 수 있습니다.

{{
  "definitions": {{
    "정의어1": "정의 내용1",
    "정의어2": "정의 내용2"
  }},
  "outline": [
    {{
      "article_number": 1,
      "title": "제1조 (목적)",
      "article_type": "기타"
    }},
    {{
      "article_number": 2,
      "title": "제2조 (정의)",
      "article_type": "기타"
    }}
  ]
}}
"""
    
    try:
        res = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": "당신은 계약 법무 설계 전문가이며 JSON 파서입니다. 출력은 오직 약속된 JSON 포맷만 반환합니다."},
                {"role": "user", "content": outline_prompt}
            ],
            temperature=0.1
        )
        raw_json = res.choices[0].message.content
        # 코드블록 제거
        raw_json = re.sub(r'```json|```', '', raw_json).strip()
        data = json.loads(raw_json)
        definitions = data.get('definitions', {})
        outline = data.get('outline', [])
    except Exception as e:
        logger.error(f"Outline generation failed or invalid JSON: {e}")
        # 예외 처리: 하드코딩 기본 목차 제공
        definitions = {"본 계약": "본 주금 및 자산매매 계약", "당사자": "본 계약의 서명 당사자"}
        outline = [
            {"article_number": 1, "title": "제1조 (목적)", "article_type": "기타"},
            {"article_number": 2, "title": "제2조 (정의)", "article_type": "기타"},
            {"article_number": 3, "title": "제3조 (비밀유지)", "article_type": "비밀유지"},
            {"article_number": 4, "title": "제4조 (손해배상)", "article_type": "손해배상"},
            {"article_number": 5, "title": "제5조 (해제 및 해지)", "article_type": "해제/해지"},
            {"article_number": 6, "title": "제6조 (준거법 및 분쟁해결)", "article_type": "준거법"}
        ]

    # ──────────────────────────────────────────────────────────
    # [2단계] 조항별 개별 생성 및 RAG few-shot 매핑
    # ──────────────────────────────────────────────────────────
    logger.info(f"K-1 고도화: 2단계 조항 단위 개별 생성 중 (총 {len(outline)}개 조항)...")
    from services.rag import search_clause_examples
    
    generated_articles = []
    all_sources = []
    
    for idx, article in enumerate(outline, start=1):
        title = article.get('title', f"제{idx}조")
        art_type = article.get('article_type', '기타')
        
        # 각 조항 타입에 부합하는 사내 계약서 실제 조항 2~3건 검색 (RAG)
        few_shot_text = ""
        try:
            # project_id는 템플릿 프로젝트를 사용하거나 None
            clause_examples = search_clause_examples(template.code, art_type, top_k=2)
            if clause_examples:
                examples_parts = []
                for ex_idx, ex in enumerate(clause_examples, start=1):
                    # 비식별화 마스킹 처리 적용 및 토큰 절약을 위해 3000자 제한
                    masked_content = mask_confidential_info(ex['content'])[:3000]
                    examples_parts.append(f"  [사례 {ex_idx} - 출처: {ex['display_title']}]\n  {masked_content}")
                    
                    # 출처 트래킹용 수집 (중복 제거)
                    if not any(s['display_title'] == ex['display_title'] for s in all_sources):
                        all_sources.append(ex)
                few_shot_text = "\n\n".join(examples_parts)
        except Exception as se:
            logger.warning(f"Failed to fetch clause examples for {art_type}: {se}")
            
        if not few_shot_text:
            few_shot_text = "(참조 가능한 사내 계약서 사례가 없습니다. 업계 표준 문체에 맞춰 구체적으로 작성하세요.)"
            
        clause_prompt = f"""당신은 계약 법무 및 재생에너지 프로젝트 전문 변호사입니다. 다음 조건에 부합하는 계약서 조항 1개 전체를 세부적으로 상세하게 작성해 주십시오.

[계약 기본 정보]
- 계약 유형: {template.name_ko}
- 사용자 입력 조건(Key-term):
{_format_key_terms(key_terms)}

[공통 정의어 목록 (일관성 있게 일치시킬 것)]
{json.dumps(definitions, ensure_ascii=False, indent=2)}

[참조용 실제 계약서 우수 사례 (Few-shot, 민감 정보 마스킹 완료)]
{few_shot_text}

[작성할 조항]
- 제목: {title} (유형: {art_type})

[작성 수칙 - 필수]
1. 요약, 개조식, 설명조 작성을 철저히 금지합니다. 실제 계약서 문서의 완성된 법률 문체로 완성본 조항을 상세하게 작성하십시오.
2. 조항의 세부 내용(①, ②, ③ 등의 항 구분과 구체적 단서조항, 예외 규정)을 반드시 포함하여 법률적 분량이 풍부하고 꼼꼼하게 나오도록 작성하십시오.
3. [공통 정의어 목록]의 용어를 해당 단어가 들어갈 위치에 그대로 적용하여 일치시키십시오.
4. 특정 회사 실명 등 민감한 고유값이 포함되어 있다면 모두 일반화(예: '발주자', '수급인') 또는 마스킹('[회사 A]') 처리하십시오.
"""
        
        try:
            import time
            time.sleep(1.5) # TPM 속도 한도(Rate limit) 우회용 대기
            res = client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": "당신은 타협하지 않고 가장 디테일하고 정교하게 계약서를 작성하는 최고 법률 대리인입니다. 설명 없이 오직 계약 조항 텍스트만 출력하십시오."},
                    {"role": "user", "content": clause_prompt}
                ],
                temperature=0.2,
                max_tokens=2048
            )
            article_content = res.choices[0].message.content.strip()
            generated_articles.append(article_content)
            logger.info(f"   - {title} 생성 완료 ({len(article_content)}자)")
        except Exception as e:
            logger.error(f"Failed to generate article {title}: {e}")
            generated_articles.append(f"\n{title}\n(조항 생성 오류 발생: {e})")

    # ──────────────────────────────────────────────────────────
    # [3단계] 조항 병합 및 정의어/참조 정렬
    # ──────────────────────────────────────────────────────────
    merged_contract_text = "\n\n".join(generated_articles)

    # ──────────────────────────────────────────────────────────
    # [4단계] Critic Pass (필수 조항 점검 및 짧은 조항 확장 보완)
    # ──────────────────────────────────────────────────────────
    fallback_warning = ""
    if not all_sources:
        fallback_warning = '\n   "※ 현재 DB에 참조 가능한 실제 계약서가 부족하여 기본 템플릿 및 업계 표준을 기반으로 생성되었습니다."'

    critic_prompt = f"""당신은 계약 검토 및 품질 보증을 수행하는 파트너 변호사입니다. 다음 생성된 계약서 전체 초안을 분석하여 보완본을 작성해 주십시오.

[생성된 계약서 초안]
{merged_contract_text}

[필수 점검 체크리스트]
본 계약서 유형({template.name_ko})은 다음 조항들을 반드시 누락 없이 상세히 포함하고 있어야 합니다:
{checklist_prompt}

[검증/보완 지시사항]
1. 위 체크리스트 중 누락된 표준 조항이 있다면 해당 조항을 생성하여 계약서 내에 편입시키십시오.
2. 각 조항 중 지나치게 짧거나 개조식 요약 형태로 서술된 조항이 있다면, 실제 실무 계약서의 상세한 항과 단서조항을 추가하여 3배 이상 확장된 법률 문장으로 재작성하십시오.
3. 문서 최상단에는 반드시 아래의 안내 문구를 명시하십시오:
   "※ 본 생성물은 AI가 작성한 초안이며, 법률 자문을 대체하지 않습니다. 최종 서명 전 반드시 법률 전문가의 검토를 거치시기 바랍니다."{fallback_warning}
4. 설명이나 도입부 멘트 없이, 오직 완성된 최종 전체 계약서 본문만을 출력해 주십시오.
"""

    try:
        res = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": "당신은 계약서 품질과 무결성을 철저하게 보완하는 수석 법무 검토관입니다. 보완 멘트 없이 최종 정렬된 계약서 본문만 출력하십시오."},
                {"role": "user", "content": critic_prompt}
            ],
            temperature=0.3,
            max_tokens=8192
        )
        final_content = res.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Critic pass failed: {e}")
        final_content = merged_contract_text

    return {
        'content': final_content,
        'sources': all_sources
    }


def _mock_contract_draft(template, key_terms: dict) -> str:
    """Mock 계약서 초안"""
    parties = key_terms.get('parties', key_terms.get('계약 당사자', '(당사자 미지정)'))
    return f"""# {template.name_ko}

## 당사자
본 계약은 {parties} 간에 체결한다.

## 제1조 (목적)
본 계약의 목적을 정의합니다.

*Mock 모드로 생성된 초안입니다.*"""


def review_contract(review, template=None) -> dict:
    """
    계약서 검토 (K-2)
    """
    client = _get_client()

    if client is None:
        return _mock_review()

    # 원본 파일 읽기
    source_text = ''
    if review.source_document_uri:
        try:
            from services.parser import parse_document
            ext = review.source_document_uri.rsplit('.', 1)[-1]
            parsed = parse_document(review.source_document_uri, ext)
            source_text = '\n'.join(c['content'] for c in parsed.get('chunks', []))
        except Exception:
            pass

    prompt = f"""다음 계약서를 검토하고, 조항별 위험 요소를 분석해주세요.

검토 지시: {review.review_instruction}

{f'표준 양식 유형: {template.name_ko}' if template else ''}

계약서 내용:
{source_text or '(파일 내용을 파싱할 수 없습니다)'}

응답 형식 (JSON):
{{
  "summary": "검토 총평",
  "findings": [
    {{
      "clause_ref": "제N조",
      "severity": "high|mid|low",
      "category": "독소조항|불리조항|누락|오류",
      "finding": "지적 내용",
      "suggestion": "수정 방향"
    }}
  ]
}}"""

    try:
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": "당신은 재생에너지 분야 계약서 검토 전문 AI입니다. JSON으로 응답하세요."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=4096,
        )
        import json
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error(f'Contract review error: {e}')
        return _mock_review()


def _mock_review() -> dict:
    """Mock 검토 결과"""
    return {
        'summary': '검토 대상 계약서에 대한 Mock 검토 결과입니다.',
        'findings': [
            {
                'clause_ref': '제12조',
                'severity': 'high',
                'category': '독소조항',
                'finding': '지체상금 상한 없음 — 무한 책임 리스크',
                'suggestion': '계약금액의 10% 상한 신설 제안',
            },
            {
                'clause_ref': '제18조',
                'severity': 'mid',
                'category': '불리조항',
                'finding': '하자담보 책임 기간 과도 (5년)',
                'suggestion': '표준 2년 대비 과도, 단축 협상 필요',
            },
            {
                'clause_ref': '—',
                'severity': 'low',
                'category': '누락',
                'finding': '불가항력 조항 부재',
                'suggestion': '표준 양식 제24조 삽입 권장',
            },
        ]
    }


def _format_key_terms(key_terms: dict) -> str:
    return '\n'.join(f'- {k}: {v}' for k, v in key_terms.items())


def generate_document_summary(title: str, text: str) -> str:
    """
    RAG Contextual Retrieval용 문서 전역 요약문 생성
    비용과 속도 절약을 위해 문서당 딱 1회 호출하며, 2~3문장(200-300자 내외)으로 핵심 맥락을 요약합니다.
    """
    client = _get_client()
    if not client or not text:
        return "이 문서는 사내 비즈니스 관련 참고 자료입니다."

    sample_text = text[:15000]
    
    prompt = f"""다음 문서를 분석하여 RAG 검색에서 해당 문서의 개별 문단들을 검색할 때, 각 문단이 전체 맥락을 잃지 않도록 돕기 위한 2~3문장 내외(총 200~300자 내외)의 간결한 문서 요약을 작성해 주십시오.
이 요약에는 반드시 다음 정보가 포함되어야 합니다:
1. 문서의 종류 및 목적 (예: EPC 도급계약서, 태양광 발전소 준공보고서 등)
2. 해당 프로젝트명 및 대상 주체 (예: 프로젝트명, 계약 당사자/발주자·수급인 등)
3. 문서의 핵심 요약 내용 및 키워드

[문서 제목]: {title}

[문서 본문 발췌]:
{sample_text}

[답변 가이드라인]:
- 인사말이나 부연 설명 없이 오직 요약 텍스트만 출력하십시오.
- 간결하고 명확하게 완성된 한국어 문장으로 작성하십시오.
"""

    try:
        res = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": "당신은 문서 전체의 맥락을 극도로 정교하고 명료하게 요약하는 전문 분석가입니다."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=300
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Document summary generation failed: {e}")
        return f"{title} 문서는 사내 비즈니스 관련 참고 자료입니다."


# 청크 역할 분류 라벨 (Tier 3 — 국면별 라우팅/부스트용)
CHUNK_ROLE_LABELS = ['가격', '보증', '정산', '당사자', '기간', '해지', '담보', '일반']


def generate_chunk_context(doc_title: str, doc_summary: str, chunk_content: str) -> dict:
    """
    Anthropic Contextual Retrieval — 청크별(chunk-specific) 맥락 + 역할 태그 생성.
    문서 단위 요약을 모든 청크에 동일 prepend하던 방식은 청크를 구별하지 못하므로,
    각 청크가 문서 내에서 '무엇을 담은' 부분인지 1~2문장(50~100토큰)으로 상황화한다.
    한 번의 LLM 호출로 {context, role}을 함께 반환한다.

    Returns: {'context': str, 'role': str}
    """
    client = _get_client()
    if not client or not chunk_content:
        return {'context': '', 'role': '일반'}

    prompt = f"""아래는 '{doc_title}' 문서의 한 청크(부분)입니다.
검색 시스템이 이 청크를 정확히 찾을 수 있도록, 이 청크가 문서 전체에서 어떤 주제·조항·수치를 담고 있는지
1~2문장(50~100토큰)의 간결한 '맥락 설명'을 작성하십시오.
반드시 청크에 담긴 핵심 고유값(예: PPA 거래단가 169.8원/kWh, 연간 보장공급량, SMP 정산 조건, 계약 당사자명 등)을 구체적으로 명시하십시오.
또한 이 청크의 주제를 다음 중 하나로 분류하십시오: {', '.join(CHUNK_ROLE_LABELS)}.

[문서 개요]
{doc_summary}

[청크 본문]
{chunk_content[:2500]}

아래 JSON 형식으로만 출력(코드블록 없이):
{{"context": "<맥락 설명 1~2문장>", "role": "<위 분류 중 하나>"}}"""

    try:
        res = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": "당신은 RAG 검색 품질을 높이기 위해 각 문단을 정밀하게 상황화하는 분석가입니다. 오직 약속된 JSON만 출력합니다."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=200,
        )
        raw = res.choices[0].message.content.strip()
        raw = re.sub(r'```json|```', '', raw).strip()
        data = json.loads(raw)
        ctx = str(data.get('context', '')).strip()
        role = str(data.get('role', '일반')).strip()
        if role not in CHUNK_ROLE_LABELS:
            role = '일반'
        return {'context': ctx, 'role': role}
    except Exception as e:
        logger.warning(f"Chunk context generation failed: {e}")
        return {'context': '', 'role': '일반'}
