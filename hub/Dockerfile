# scribe-hub — the Azora Hub service. Built and run on atomsk; see compose.example.yaml.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ffmpeg openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 scribe

# S4 agents (H2): the official Codex and Claude Code CLIs, which need Node.
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && npm install -g --no-fund --no-audit @openai/codex @anthropic-ai/claude-code \
 && npm cache clean --force

WORKDIR /app
COPY pyproject.toml ./
COPY scribe_hub ./scribe_hub
RUN pip install --no-cache-dir .

USER scribe
ENV SCRIBE_DATA_DIR=/data \
    CODEX_HOME=/data/agents/codex \
    CLAUDE_CONFIG_DIR=/data/agents/claude
VOLUME ["/data"]
EXPOSE 8780 8781

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8780/healthz', timeout=4).status == 200 else 1)"

CMD ["scribe-hub", "serve"]
