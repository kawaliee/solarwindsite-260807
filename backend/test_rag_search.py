import os
import django
import sys
import json

# Setup Django environment
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from services.embedding import get_single_embedding, get_single_sparse_embedding
from services.qdrant_service import search

def test_search():
    query = "당진 pjt o&m 계약금액과 o&m보증사항은 어떻게 되나요?"
    print(f"=====================================")
    print(f"질의: {query}")
    print(f"=====================================\n")
    
    vec = get_single_embedding(query)
    sparse_vec = get_single_sparse_embedding(query)
    if vec is None or sparse_vec is None:
        print("임베딩 생성 실패!")
        return

    print("--- 1. 하이브리드 검색 결과 (Top 15) ---")
    results = search(vec, sparse_vec, top_k=15)
    
    for i, res in enumerate(results):
        p = res.payload
        print(f"\n[Rank {i+1}] 문서명: {p.get('document_title')}")
        print(f"   Score: {res.score:.4f} | 섹션: {p.get('section_title')}")
        content = p.get('content', '')
        snippet = content[:300].replace('\n', ' ') + "..."
        print(f"   본문 요약: {snippet}")

if __name__ == "__main__":
    test_search()
