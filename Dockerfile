FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860 \
    MALLOC_ARENA_MAX=2 OMP_NUM_THREADS=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 rinda

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=rinda:rinda . .
RUN mkdir -p /app/var && chown rinda:rinda /app /app/var
USER rinda

# Fit both forecast models at build time so the container starts in seconds.
RUN python -c "import sys; sys.path.insert(0, 'server'); from engine import ENGINE; ENGINE.load()"

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/v1/ready')"
CMD ["sh", "-c", "uvicorn --app-dir server main:app --host 0.0.0.0 --port ${PORT}"]
