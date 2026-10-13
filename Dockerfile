FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 10001 blackcatguard \
    && useradd --uid 10001 --gid blackcatguard --no-create-home blackcatguard \
    && mkdir /app/data \
    && chown blackcatguard:blackcatguard /app/data

COPY main.py ./
COPY dao ./dao
COPY routers ./routers
COPY services ./services
COPY utils ./utils
COPY templates ./templates
COPY static ./static

USER blackcatguard
EXPOSE 1777

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:1777/login', timeout=3).close()"

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "1777", "--workers", "1"]
