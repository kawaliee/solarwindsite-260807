import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from services.rag import search_documents

try:
    res = search_documents('당진 pjt 대주단과 대출금액, 금리 등')
    print("--- 당진 RAG 최종 E2E 검색 결과 ---")
    if not res.get('sources'):
        print("결과 없음")
    else:
        for i, s in enumerate(res['sources']):
            print(f"[{i+1}] {s['display_title']} (score: {s['score']:.4f})")
            print(f"    Location: {s['location_label']}")
            print(f"    Snippet: {s['snippet']}")
            print("-" * 50)
except Exception as e:
    import traceback
    traceback.print_exc()
