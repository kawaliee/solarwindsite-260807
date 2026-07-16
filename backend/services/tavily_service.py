import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# User provided API key fallback
DEFAULT_TAVILY_API_KEY = "<REDACTED>"

def search_web_via_tavily(query: str, search_depth: str = "basic") -> dict:
    """
    Tavily API를 활용한 웹 검색 함수.
    LLM의 Function Calling(Tool Calling)에 의해 호출됩니다.
    
    Args:
        query: 검색어
        search_depth: 검색 깊이 ("basic" 또는 "advanced")
    
    Returns:
        Tavily 검색 결과 dict (summary, results 등 포함)
    """
    api_key = getattr(settings, 'TAVILY_API_KEY', DEFAULT_TAVILY_API_KEY)
    if not api_key:
        return {"error": "Tavily API key not configured."}
        
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": search_depth,
        "include_answer": True,
        "max_results": 3
    }
    
    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # 포맷팅: LLM이 이해하기 쉽게 정리
        results = data.get("results", [])
        formatted_results = []
        for r in results:
            formatted_results.append({
                "title": r.get("title"),
                "url": r.get("url"),
                "content": r.get("content", "")[:500]  # 너무 길면 자름
            })
            
        return {
            "query": query,
            "answer": data.get("answer", ""),
            "results": formatted_results
        }
        
    except Exception as e:
        logger.error(f"Tavily search failed for query '{query}': {e}")
        return {"error": str(e)}
