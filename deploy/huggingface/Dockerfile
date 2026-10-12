# Hugging Face Space image: builds RINDA straight from GitHub, so the Space repo
# holds only this file and its README. The ADD line changes whenever main moves,
# which invalidates the cache and pulls the new commit on every rebuild.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860
RUN apt-get update && apt-get install -y --no-install-recommends git libgomp1 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 rinda

ARG REPO=https://github.com/HappyAxelo/rinda.git
ADD https://api.github.com/repos/HappyAxelo/rinda/git/refs/heads/main /tmp/rinda-head.json
RUN git clone --depth 1 "$REPO" /app && chown -R rinda:rinda /app

WORKDIR /app
RUN pip install --no-cache-dir -r requirements.txt
USER rinda
RUN python -c "import sys; sys.path.insert(0, 'server'); from engine import ENGINE; ENGINE.load()"

EXPOSE 7860
CMD ["sh", "-c", "uvicorn --app-dir server main:app --host 0.0.0.0 --port ${PORT}"]
