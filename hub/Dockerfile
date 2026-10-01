# scribe-hub — the Azora Hub service. Built and run on atomsk; see compose.example.yaml.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ffmpeg openssh-client \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 scribe

WORKDIR /app
COPY pyproject.toml ./
COPY scribe_hub ./scribe_hub
RUN pip install --no-cache-dir .

USER scribe
ENV SCRIBE_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8780 8781

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8780/healthz', timeout=4).status == 200 else 1)"

CMD ["scribe-hub", "serve"]
