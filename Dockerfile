FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && pip install -r requirements.txt

COPY pyproject.toml README.md run_server.py ./
COPY src ./src
COPY docker/start.sh ./docker/start.sh

RUN pip install -e . \
    && mkdir -p /app/artifacts \
    && groupadd -r appgroup \
    && useradd -r -g appgroup -d /app -s /usr/sbin/nologin appuser \
    && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8080

ENTRYPOINT ["./docker/start.sh"]
