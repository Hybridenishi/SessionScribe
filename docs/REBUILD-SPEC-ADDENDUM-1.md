# Rebuild Spec — Addendum 1: hub stack, layout, and app auth

| | |
|---|---|
| **Date** | 2026-10-01 |
| **Status** | Proposal for Nate's approval. Nothing here is built. |
| **Amends** | `docs/REBUILD-SPEC.md` (revision 4): §3.3, §6, §10, §11, §12 |

Three proposals (§2–§4), plus what verification found that the spec got wrong (§1). Each proposal ends with the decisions it needs.

---

## 1. Findings that change the spec (verified 2026-09-30)

| Spec says | Found | Effect |
|---|---|---|
| `iris-bot` "never reads DM files" and a bot bug "can't read DM files" (§3.2) | Qdrant has **no API key** and listens on `0.0.0.0:6333`. `iris-bot` already talks to it over the LAN and could query the DM index (`azora` → alias of `azora_tmp`, 8,288 points). Only Iris's own code stops it. | The isolation must be enforced at Qdrant: JWT RBAC, read-only token scoped to `azora-players` for `iris-bot`. Client support is in azora-iris PR #26; the server change is pending. |
| Credentials in `0600` files (§8) | `iris-bot` holds its Discord, OpenRouter and DeepSeek keys as container env vars. | Move to `0600` files before players arrive. |
| Privacy note lists DeepSeek as the only third party in player mode (§8) | Player questions are also sent to **OpenRouter** for embeddings. | Add to the table's privacy note, or embed locally. |
| Hub owns vault writes (§3.1) | Hermes (Futaba) mounts `/mnt/user/vaults` read-write. The checkouts there are a detached `Azora-DM` (`origin/main`, 9 days old) and `Azora-Players` on `phase3/pc-exodus` (5 weeks old), owned by `nobody`. | The hub uses **its own clones** (§3). Futaba's role is open question 5. |
| Repo `Hybridenishi/azora-homebrew` | Renamed: `Hybridenishi/Azora-Dm` (old name redirects). | Use the new name. |
| Transcription: Whisper on naota (§3.3); Q1 "what made sessions 1–59?" | Sessions were transcribed by **Apple SpeechAnalyzer** via `yap` (`~/Documents/Azora/scribe/scribe.sh`). naota has **no Whisper** installed (llama.cpp router only). | See the trial below. |
| `foundry-sidecar` must be healthy (§4) | Still `401` on startup; 6,073 restarts. | H4 stays blocked. |

**Transcription trial** (session 59 DM track, 2 h 20 m, M5 Pro Mac, counts of campaign names spelled right / known mishearings):

| Engine | Right | Wrong | Junk lines | Time |
|---|---|---|---|---|
| Apple SpeechAnalyzer (today) | 98 | 21 | 0 | 1 min |
| Apple + hint list / custom model | no change / won't build | | | |
| Whisper large-v3-turbo (MLX) | 121 | 16 | 0 | 2 min |
| **Whisper + silence skip + names as prose + loop filter** | **149** | **4** | 0 after filter | 6 min |

The counts are a proxy, not a hand-graded score; spot checks of the added names were real corrections. Two traps found: in `mlx_audio`'s Whisper the prompt is dropped after the first 30 s window, and a `Names: a, b, c` prompt makes Whisper echo the list into silences.

---

## 2. Hub stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Matches Iris and the spec; the CLIs and Whisper tooling are Python/Node anyway |
| API | FastAPI + uvicorn, SSE for job progress | Typed request models, OpenAPI the Swift client can be checked against |
| Store | SQLite (WAL) via stdlib `sqlite3`, plain SQL migrations | One file on atomsk; proposals index, jobs, devices, audit. Vault git stays the source of truth |
| Jobs | In-process worker over a SQLite `jobs` table; stages S1–S6 idempotent and resumable | No Redis or Celery to run or back up; survives reboots (atomsk rebooted unannounced on 09-26) |
| GPU queue | One `gpu` lane with a single slot. The hub exposes a small OpenAI-compatible proxy in front of naota's llama-server, and Iris points `IRIS_LLM_BASE` at it | Serialises Iris (both modes) and any naota job hub-wide with no Iris code change beyond a URL; replaces the in-bot lock from PR #25 for cross-service cases |
| S2 transcription | **Mac worker** (recommended, see decision 1): a small launchd Python agent on the Mac that pulls S2 jobs from the hub, runs the trial recipe with MLX, and posts `utterances.ndjson` back | Proven on this hardware; the Mac is on when Nate drops the zip. naota stays a later fallback |
| S4/S5 agents | Codex CLI and Claude Code CLI installed in the hub image; Nate signs in once with `docker exec -it scribe-hub codex login --device-auth` and `claude setup-token`; credentials on a `0600` named volume | Official CLIs only, per §3.3 |
| Vault git | Hub's own clones under `/mnt/user/appdata/scribe-hub/vaults/`, one GitHub **deploy key per repo**; branches only; Publish = pull → merge → push, never force | Doesn't share a working tree with Obsidian sync or Futaba |
| Exposure | Bound to the container network only; published to the tailnet with `tailscale serve` (HTTPS, valid `*.ts.net` certificate) | No public port, no self-signed cert, no ATS exception in the app |

**Containers and networks on atomsk** (new network `azora`):

| Container | Networks | Mounts | Reaches |
|---|---|---|---|
| `scribe-hub` | `azora` | its vault clones (rw), session archive (rw), secrets (ro) | GitHub, naota, Foundry sidecar, Qdrant (DM + players tokens) |
| `iris-dm-retriever` | `azora` | `Azora-DM` clone (ro), transcripts (ro) | Qdrant (`azora-dm` read token) |
| `iris-bot` | `azora-iris` (new, separate) | persona, guard, secrets (ro) | Discord, the hub's GPU proxy only, Qdrant (`azora-players` read token) |

`iris-bot` never joins `azora`, so it cannot reach the hub's API or the DM retriever except through the one gated retriever route (H5).

**Decisions**
1. **S2 location.** Mac worker (recommended: tested, 6 min per DM track, Mac must be awake) **or** naota (always on, untested, Whisper on ROCm still to set up, shares the GPU with Iris).
2. **GPU proxy in the hub** for Iris's local model (recommended) or keep Iris talking to naota directly with only the in-bot lock.

---

## 3. `hub/` layout

```
hub/
  pyproject.toml
  Dockerfile                  # python:3.12-slim + node (for the CLIs), non-root user, HEALTHCHECK
  compose.example.yaml        # the real compose file lives on atomsk, never in git
  campaign.yaml.example       # Discord username -> speaker label, role, PC
  scribe_hub/
    app.py                    # FastAPI app, routers mounted here
    auth.py                   # device tokens, pairing codes (§4)
    db.py                     # SQLite schema + migrations
    api/                      # status, sessions, proposals, batches, codex, foundry, iris
    jobs/
      queue.py                # job table, worker loop, retries, resume after reboot
      gpu.py                  # single-slot lane + OpenAI-compatible proxy to naota
      s1_ingest.py … s6_foundry.py
    vaults/                   # git ops, branch/publish, path guards (Foundry sync can't read Azora-DM)
    proposals/                # §5.1 schema, hub-side validation, reveal tripwire
    foundry/                  # client moved from the app, rules from FOUNDRY-PUBLISHING-PLAN.md
    agents/                   # Codex/Claude CLI runners; iris-agent tools (app Iris, §3.2a)
  dm_retriever/               # iris-dm-retriever: own package, own image (open question 4)
  mac_worker/                 # S2 transcription agent for the Mac (if decision 1 = Mac)
  tests/
```

`hub/` gets its own CI job (pytest, ruff) separate from the Xcode project.

---

## 4. Mac app ↔ hub authentication

- **Address.** `https://atomsk.<tailnet>.ts.net` via `tailscale serve`. The existing ATS exception for `100.100.244.3` over plain HTTP is removed.
- **Pairing, once per device.**
  1. Nate runs `docker exec -it scribe-hub scribe-hub pair --device "Nate's MacBook"` in his own terminal. It prints an 8-character **one-time code**, valid 10 minutes.
  2. In Settings he enters the hub address and the code. The app calls `POST /auth/pair`.
  3. The hub returns a **device token** (32 random bytes) once, and stores only its SHA-256 hash with the device name, creation and last-seen times.
  4. The app stores the token in Keychain: service `com.natedavis.SessionScribe.hub`, `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`, so it is never synced to iCloud.
  - The long-lived token never appears in a terminal, a chat or a log. Only the short code does, and it dies on first use. Pairing is rate-limited and locks after five bad codes.
- **Every request.** `Authorization: Bearer <token>`. As a second check, the hub requires the `Tailscale-User-Login` header that `tailscale serve` adds to match Nate's tailnet login, so a copied token alone does not work from another tailnet identity.
- **Revoke and rotate.** `scribe-hub devices list | revoke <id>`; the app's Settings shows "Re-pair" when it gets a `401`.
- **Scope.** One scope (full), because Nate is the only app user. No token is ever issued to Discord, Iris or Futaba.
- **Logging.** The hub logs a token's id prefix only. A test asserts no full token or code appears in logs.
- **Migration.** Once the hub owns Foundry (H4), the app deletes its existing `foundry-sidecar` Keychain item; the app then holds the hub token and nothing else (§3.1).

**Decision 3.** Approve pairing codes + device tokens + Tailscale identity check, **or** keep it simpler with a token you paste once (no pairing step; the token passes through your clipboard).

---

## 5. Spec edits if approved

- §3.3: S2 default becomes the chosen location from decision 1, with the trial recipe.
- §6: add `POST /auth/pair`, `GET /auth/devices`, `DELETE /auth/devices/:id`, and the internal GPU proxy route.
- §8: add OpenRouter embeddings to the privacy note; move `iris-bot` secrets to files.
- §11: repo name `Hybridenishi/Azora-Dm`.
- §12: Q1 answered (Apple SpeechAnalyzer via `yap`).
