# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 시스템 의존성 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    tesseract-ocr \
    tesseract-ocr-kor \
    tesseract-ocr-eng \
    poppler-utils \
    # 검토 보고서 지도에 한글을 렌더링하려면 한글 폰트가 필요하다 (없으면 □로 깨짐)
    fonts-nanum \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# Python 패키지 설치
#   기본 타임아웃(15초)으로는 대용량 휠(torch·matplotlib 등) 내려받다 끊긴다.
#   실제로 ReadTimeoutError로 빌드가 실패해 타임아웃·재시도를 늘렸다.
#
#   --mount=type=cache 로 pip 다운로드 캐시를 이미지 레이어 밖에 둔다.
#   종전에는 위 apt 줄 하나만 고쳐도(예: 이번의 libreoffice 제거) 그 아래
#   레이어가 전부 무효화되어 torch(526MB) 등 대용량 휠을 매번 PyPI에서
#   다시 받았다 — 재빌드 한 번에 10분 넘게 걸린 원인이었다. 캐시 마운트는
#   레이어 캐시가 깨져도 살아남으므로, 이후로는 apt 목록을 고쳐도 이미
#   받아 둔 휠을 그대로 쓴다. (--no-cache-dir는 pip 자체 캐시를 끄는
#   옵션이라 이 마운트와 상충하므로 뺐다.)
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 120 --retries 10 -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
