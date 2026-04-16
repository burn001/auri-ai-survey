FROM python:3.13-slim

RUN groupadd -r survey && useradd -r -g survey survey

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R survey:survey /app
USER survey

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
