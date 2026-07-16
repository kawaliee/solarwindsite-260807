import os
import sys
import django
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from services.llm import generate_answer

print("=== 1. 내부 검색만으로 충분한 질문 ===")
res1 = generate_answer("태양광 FS 보고서에서 다루고 있는 주요 내용은 무엇인가요?", context="[내부 문서] 당진 태양광 발전 FS 보고서: 재무모델 수익성 15%, CAPEX 500억 등")
print(res1['content'])

print("\n=== 2. 외부 검색이 필요한 질문 ===")
res2 = generate_answer("2026년 표준 근로계약서 유급휴가 개정안에 대해 알려줘.", context="")
print(res2['content'])
