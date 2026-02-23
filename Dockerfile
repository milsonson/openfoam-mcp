FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENFOAM_BASHRC_PATH=/opt/openfoam11/etc/bashrc

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    bash \
    build-essential \
    python3 \
    python3-pip \
    python3-dev \
    python3-venv \
    openmpi-bin \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://dl.openfoam.org/gpg.key | gpg --dearmor -o /etc/apt/keyrings/openfoam.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/openfoam.gpg] http://dl.openfoam.org/ubuntu jammy main" > /etc/apt/sources.list.d/openfoam.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    openfoam11 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python3 -m pip install --upgrade pip && python3 -m pip install -r requirements.txt

COPY pyproject.toml README.md run_server.py ./
COPY src ./src
COPY docker/start.sh ./docker/start.sh

RUN python3 -m pip install -e . \
    && mkdir -p /app/artifacts \
    && groupadd -r appgroup \
    && useradd -r -g appgroup -d /app -s /usr/sbin/nologin appuser \
    && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8080

ENTRYPOINT ["./docker/start.sh"]
