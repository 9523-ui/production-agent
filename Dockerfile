FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY prodagent ./prodagent
COPY eval ./eval
RUN mkdir -p /app/data

# 非 root 运行；数据卷单独挂载
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/healthz', timeout=2)"

ENV HOST=0.0.0.0 PORT=8010 LLM_BACKEND=fake DB_PATH=/app/data/prodagent.db
CMD ["python", "-m", "prodagent.app"]
