# scribe-hub

The Azora Hub service (REBUILD-SPEC.md, Addendum 1). H1 scope: S1 ingest, S2 transcription via the
Mac worker, S3 staging to a vault branch, the GPU queue, device pairing, and the API the
SessionScribe app uses for Settings, Dashboard and Sessions.

## Layout

```
scribe_hub/
  app.py          Hub + two FastAPI apps: main API (app/worker scopes) and the GPU listener
  auth.py         pairing codes, hashed device tokens, scopes, Tailscale identity check
  db.py           SQLite schema + migrations
  campaign.py     campaign.yaml: speaker map + Whisper vocabulary prompt
  craig.py        safe Craig .zip extraction
  archive.py      session archive on disk; audio clips for playback
  transcript.py   merge tracks -> utterances.ndjson, transcript.txt, Full-Transcript.md
  vaults.py       git on the hub's own clone: scribe/* branches only, _INBOX only, never main
  jobs/queue.py   job table, leases, recovery after reboot
  jobs/stages.py  S1-S3
  jobs/gpu.py     single-slot naota queue
  api/            routes
mac_worker/       S2 worker for the Mac (Whisper large-v3-turbo on MLX) + launchd plist
```

## API (H1)

| Route | Scope | |
|---|---|---|
| `GET /healthz` | none | liveness only |
| `POST /auth/pair` | tailnet identity | one-time code -> device token |
| `GET /auth/devices`, `DELETE /auth/devices/{id}` | app | list / revoke |
| `GET /status` | app | dashboard rollup |
| `POST /sessions?number=N` (zip) | app | S1 |
| `GET /sessions`, `GET /sessions/{n}` | app | list, detail with manifest and jobs |
| `GET /sessions/{n}/utterances?offset&limit` | app | paged transcript |
| `GET /sessions/{n}/audio/{track}?from&to` | app | WAV clip |
| `POST /sessions/{n}/retry`, `POST /sessions/{n}/transcribe` | app | re-run S1 / S2 |
| `GET/PUT /campaign` | app | speaker map + vocabulary |
| `POST /worker/claim`, `/worker/jobs/{id}/audio\|heartbeat\|result\|fail` | worker | S2 |
| GPU listener `POST /v1/chat/completions`, `GET /health` | gpu | naota via the queue |

## Develop

```
uv venv -p 3.12 .venv && uv pip install -p .venv -e ".[dev]" -e mac_worker
.venv/bin/pytest tests mac_worker/tests && .venv/bin/ruff check .
```

Deploy: see `compose.example.yaml`.
