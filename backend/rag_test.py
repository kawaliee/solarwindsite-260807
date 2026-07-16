"""
RAG 파이프라인 E2E 테스트
실제 업무 질문으로 벡터 검색 품질 검증
"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from services.embedding import get_single_embedding, get_single_sparse_embedding
from services.qdrant_service import search

# ── 테스트 질문 목록 ──────────────────────────────────────────
test_queries = [
    ("당진 태양광 PF 대출 금리 및 약정 조건", None),
    ("EPC 감리 월간 공정률 현황", None),
    ("재생에너지 공급의무화 RPS 비율", None),
    ("보험 근질권 설정 계약서", None),
    ("태양광 발전사업 개발행위허가 조건", None),
    ("전력거래 PPA 계약 신고 절차", None),
]

SEP = "=" * 65

print(SEP)
print("  재생E AI Agent — RAG 검색 품질 테스트")
print(SEP)

for query, project_id in test_queries:
    print(f"\n📌 질문: {query}")
    print("-" * 55)

    vec = get_single_embedding(query)
    sparse_vec = get_single_sparse_embedding(query)
    if vec is None or sparse_vec is None:
        print("  ❌ 임베딩 실패")
        continue

    results = search(vec, sparse_vec, project_id=project_id, top_k=3)

    if not results:
        print("  ⚠️  검색 결과 없음")
        continue

    for i, r in enumerate(results, 1):
        p = r.payload
        title = p.get("document_title", "")[:55]
        section = p.get("section_title", "-")[:35]
        content = p.get("content", "")[:100].strip().replace("\n", " ")
        ftype = p.get("file_type", "").upper()
        score = r.score
        is_global = p.get("is_global", False)
        scope = "전사공용" if is_global else "프로젝트"

        print(f"  {i}. [{ftype}][{scope}] {title}")
        print(f"     섹션  : {section}")
        print(f"     유사도: {score:.4f}")
        print(f"     내용  : {content}...")
        print()

print(SEP)
print("✅ 테스트 완료")
print(SEP)
