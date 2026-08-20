FROM python:3.12.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/workspace/.matplotlib

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        antiword \
        catdoc \
        file \
        fonts-noto-cjk \
        jq \
        libcairo2 \
        libreoffice-nogui \
        nodejs \
        p7zip-full \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        unzip \
    && rm -rf /var/lib/apt/lists/*

COPY docker/cxba-sandbox/requirements.txt /opt/cxba/requirements.txt

RUN python -m pip install --no-cache-dir --requirement /opt/cxba/requirements.txt \
    && python -m pip check \
    && python -m pip uninstall --yes pip

COPY profiles/cxba-production/skills/cxba/cxba-safe-tabular-analysis/scripts /opt/cxba/scripts
COPY docker/cxba-sandbox/smoke.py /opt/cxba/smoke.py

RUN chmod 0555 /opt/cxba/scripts/*.py \
    && chmod 0444 /opt/cxba/smoke.py \
    && rm -f /usr/bin/apt /usr/bin/apt-get /usr/bin/apt-cache \
    && mkdir -p /workspace \
    && chmod 0777 /workspace

WORKDIR /workspace

CMD ["python", "--version"]
