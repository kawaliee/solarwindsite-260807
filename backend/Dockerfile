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
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
