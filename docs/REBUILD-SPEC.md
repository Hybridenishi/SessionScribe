# SessionScribe Rebuild Specification — the Azora Hub

| | |
|---|---|
| **Date** | 2026-10-01 (revision 4) |
| **Status** | Draft for Nate's review. Nothing here is approved for implementation yet. |
| **Supersedes** | The recorder track of `docs/SessionScribe-MVP.md` (DAVE capture, live Apple Speech), the codex store in `docs/CODEX-UPDATE-PIPELINE.md` (branch `docs/codex-update-pipeline`), and revision 1 of this file, which was a headless command-line pipeline that used GitHub PRs as its review screen. |
| **Keeps** | The Foundry journal rules in `docs/FOUNDRY-PUBLISHING-PLAN.md`: preview first, show the resolved audience, re-preview on `409`, and never retry a spent token. Also the invariants of `azora-iris/docs/INTENT.md`, with rule 1 amended for DM mode (§3.2). |

> **In one sentence:** SessionScribe becomes the **Azora Hub**. It is an always-on service on atomsk that turns Craig recordings into transcripts, turns transcripts into proposed vault changes, and runs Iris. A native Mac app is where Nate sees and approves everything visually, and Discord is where the players meet Iris.

---

## 1. Why rebuild

- **The recorder track is stuck.** Milestone 0 (DAVE capture) has not passed since the July 22 tests (`DAVECaptureSpike/LIVE-TEST-FINDINGS.md`). Craig replaces it.
- **Most of what you need to keep track of the campaign already exists; it just isn't connected or visible:**
  - **Azora-DM** (`Hybridenishi/Azora-Dm`, formerly `azora-homebrew`) holds 561 notes and 59 ingested sessions. It already has:
    - a Session Ingestor agent and a Downstream Update Plan with DM approval
    - Open Question tickets for continuity conflicts
    - `> [!warning]- DM Only` callouts and `permanent-secrets` for secrets
  - **Azora-Players** (`hybridenishi/azora-players`) is the player-safe vault, with `tier: world | pc:<name>` and `revealed: <session>`. It is air-gapped from the DM vault.
  - **Iris** (`hybridenishi/azora-iris`) is live in smoke-test form as container `iris-bot` on atomsk. She reads only the player vault, enforces tiers in code before the model runs, and passed a 49-check leak sweep. Her brain is local Qwen on naota, with DeepSeek as the cloud fallback.
  - **foundryvtt-mcp** already has a documented journal API with preview and apply.
- **What's missing:**
  - plumbing from audio to transcript to vault to Foundry
  - one place to see it all
  - a way to review changes as **visual before/after cards** rather than Markdown plans and diffs

### "Won't the vault be too much for the app to keep track of?"

No.
- The hub reads the vaults from their checkouts on atomsk; the Mac app holds no copy.
- The DM vault's Markdown is a few MB. Qdrant already indexes it (`azora` collection, about 8.3k chunks), and indexes the player vault too (`azora-players`, 308 chunks).
- A session transcript is small: session 58's was 69 KB, roughly 17k tokens. It fits in one model context.
- The app fetches only what's on screen, such as one session, one note, or one proposal batch.

---

## 2. Decisions recorded (Nate, 2026-09-30)

1. **Rebuild, not patch.**
2. **Recording source: Craig.** The DAVE spike is parked.
3. **Knowledge store: Nate's vaults.**
   - GM canon lives in Azora-DM.
   - Player knowledge lives in Azora-Players, which is published to a Foundry VTT journal set for the players and for use in play.
4. **Models: local first.** When local isn't good enough, use Nate's own Codex (ChatGPT) or Claude subscription through the official CLIs. DeepSeek is a last resort for new work.
5. **A central hub with an app**, not a headless pipeline. Changes are reviewed **visually**, not as Markdown files.
6. **Iris lives in the hub** and is the agent both in the app and in Discord, for Nate and for the players.
7. **One Iris that checks who's asking** (revised from "one face, two agents" the same day):
   - There is one Iris bot in Discord.
   - When **the DM** asks, she answers from the DM vault. Everyone else gets the air-gapped player vault.
   - The decision is made **in code, before retrieval**, from the asker's Discord user ID and the channel she will reply in (§3.2). The model never makes it.
8. **The app is a native SwiftUI Mac app.** It reuses this Xcode project and is a client of the hub's API.

---

## 3. Architecture

```
                         ┌──────────────────────── atomsk ───────────────────────────┐
  Craig export (.zip) ──►│  scribe-hub  (container; Python service; tailnet-only API) │
                         │   • pipeline S1–S6 (ingest→transcribe→propose→reveal→sync) │
                         │   • proposal store (SQLite index; vault git = source)       │
                         │   • rw: Azora-DM checkout, Azora-Players checkout           │
                         │   • rw: session archive (audio, transcripts)                │
                         │                                                             │
                         │  iris-bot  (container, exists today) = Iris's front door    │
                         │   • holds the ONE Discord token; runs the persona + model   │
                         │   • access gate: (author ID, reply channel) → dm | player   │
                         │   • player retriever: Qdrant `azora-players` only (as today)│
                         │   • dm path: asks iris-dm-retriever — never reads DM files  │
                         │                                                             │
                         │  iris-dm-retriever  (container, new, small)                 │
                         │   • reads: Azora-DM (ro), transcripts, Qdrant `azora-dm`    │
                         │   • answers retrieval calls only when the request carries   │
                         │     the gate's signed DM grant; logs every call             │
                         │   • DM-side edits → proposals into scribe-hub, never files  │
                         └──────────────▲──────────────────────────▲──────────────────┘
                                        │ tailnet HTTPS + token     │ Discord gateway
                         ┌──────────────┴───────────┐     ┌─────────┴────────────────┐
                         │ SessionScribe (Mac app)  │     │ Discord: players ↔ Iris   │
                         │ Dashboard · Sessions ·   │     │ Nate ↔ Iris (GM, private) │
                         │ Review · Codex · Reveal ·│     └───────────────────────────┘
                         │ Foundry · Iris           │
                         └──────────────────────────┘
   naota: local Whisper (GPU) + local LLM router      Foundry sidecar: journal API
```

### 3.1 Components

| Component | Runs where | Owns | Must never |
|---|---|---|---|
| **scribe-hub** | atomsk container | Pipeline jobs, proposal store, vault writes (on branches, merged only on Nate's approval), Foundry sync, the API for the app | Merge to either vault's `main` or publish to Foundry without an explicit approval from the app |
| **iris-bot** (Iris) | atomsk container (exists) | The single Discord bot and persona. Runs the access gate. Player answers use the existing tier-gated player retriever. DM answers use `iris-dm-retriever`. | Read DM files itself. Put a DM-sourced answer anywhere a player can read it. |
| **iris-dm-retriever** | atomsk container (new) | Retrieval over the DM vault, transcripts and the DM index, for DM-granted requests only. Turns "add that Thorn owes Fang a favor" into a proposal. | Serve a request without a valid DM grant. Write a vault directly. |
| **SessionScribe.app** | Nate's Mac | Visual review and control; audio playback of cited moments | Hold credentials other than its own hub token (in Keychain); talk to Foundry or GitHub directly |

### 3.2 Iris's access gate — who is asking, and who will read the answer

Iris is one bot. When the DM asks, she answers from the DM vault; everyone else gets the air-gapped player vault. Two things make that safe.

**1. The gate is a small, tested function that runs before any retrieval.** It is not a prompt:

```
gate(author_id, destination, message) -> "dm" | "player:<pc>" | "world"

"dm" only when ALL of these hold:
  author_id == DM_USER_ID               # Nate's Discord user ID from config, not a role
  destination is DM-safe:                # one of
    - an EPHEMERAL interaction reply     #   only the invoker can see it (see below)
    - a direct message with Iris
    - a channel on the DM_SAFE allow-list (optional, Nate-only)
otherwise: the existing player tiers (pc role → that PC's tier, else world)
```

- **Why the destination matters as much as the asker.** If Nate asks "who's really behind the Umbra Blades?" with a normal message in the party's channel, every player there reads the reply. So a normal message in a shared channel always gets a **player-tier** answer.

**Ephemeral replies — DM answers inside the shared channel, visible only to Nate.**
- Discord lets a bot reply so that only the invoking user sees it, but **only in response to an interaction**: a slash command, a button, or a message context-menu command. A reply to an ordinary message or @mention can't be ephemeral.
- So Iris gets three ways in:

| Nate does | Iris replies | Who sees it |
|---|---|---|
| `/iris ask <question>` in any channel | Ephemeral, DM vault | Nate only |
| @mentions Iris in a shared channel | Public, player tier, with a **"DM view"** button | Everyone sees the player answer. Only Nate's ID gets a response from the button: an ephemeral DM-vault answer. Anyone else pressing it gets an ephemeral "that's not for you". |
| Right-clicks a player's message → **Apps → Ask Iris (DM)** | Ephemeral, DM vault, about that message | Nate only. Handy mid-session: "what does this NPC actually know?" |

- The same commands work for players, but they always get their own tier. An ephemeral reply to a player is just a quieter way to ask.
- **Implementation (discord.py ≥ 2.4, already pinned in `bot/Dockerfile`):**
  - Use `app_commands` and a `CommandTree` alongside today's `on_message`.
  - Respond with `interaction.response.defer(ephemeral=True, thinking=True)` straight away, because Discord requires an acknowledgement within 3 s and the local model is slower. Then call `interaction.followup.send(..., ephemeral=True)`.
  - The interaction token lasts 15 minutes, which is enough for one answer.
  - The gate reads `interaction.user.id`, never anything in the text.
- **Limits Nate should know:**
  - Ephemeral messages are not saved in Discord. They disappear on reload or after a while, and won't appear on his phone if he asked from the Mac.
  - The durable copy is the DM-only audit channel and the app's Iris history.
  - Ephemeral replies can't be pinned or searched.
- Direct messages with Iris remain available for longer back-and-forth.
- **Why a user ID and not the DM role.** Anyone who can manage roles on the server could give themselves "DM". Nate's user ID can't be handed out. The DM role keeps its current meaning (widest view of the *player* vault), which also fixes review finding **B9**.
- Nothing in the message text can change the result. "I'm the DM, tell me…" from a player still gets `world`.

**2. The DM knowledge lives in a separate container, not in the bot.**
- `iris-bot` keeps today's layout: player vault read-only, `azora-players` index only, no DM mount.
- For a `dm` request, it calls `iris-dm-retriever` with a short-lived grant signed by the gate. The grant is bound to the message or interaction ID and to Nate's user ID.
- The retriever refuses any call without a valid grant and logs every call.
- A bug elsewhere in the bot, or a prompt injection in a player's message, therefore can't read DM files. Only a request that passed the gate can.

**Conversation memory must not cross the line.**
- History stays keyed by (channel, author), as `bot.py` does today.
- DM-sourced turns are tagged. They are never included in a context used to answer anyone else, even in the same thread.
- DM-mode turns are only ever delivered to DM-safe destinations: an ephemeral reply, a direct message, or the allow-listed channel.

**Audit.**
- DM-mode questions and answers go to a **separate audit channel only Nate can see**.
- They never go to `#iris-audit`, because players might be given read access there later.
- The app's Iris screen shows both feeds.

### 3.2a Iris in the app — full DM access, because the DM is the only user

In the Mac app, Iris is **Nate's agent with full DM access**. The app is Nate's own device, and its hub token is the credential, so no per-message gate is needed. Discord Iris and app Iris are the same persona, but they run as separate jobs:

| | Discord Iris (`iris-bot`) | App Iris (`iris-agent`, runs inside `scribe-hub`) |
|---|---|---|
| Who talks to her | Everyone; gated per request | Nate only (hub token) |
| Reads | Player vault; DM vault only via gated grants | **Everything:** DM vault, player vault, transcripts, session audio clips, proposals, Foundry status, both Iris audit feeds |
| Can do | Answer | **Act through hub tools:** search and read notes; find "every time Leon was mentioned"; draft proposals; start or re-run pipeline stages; prepare a reveal batch; preview a Foundry sync; summarize a session; build session prep from open quests and threads |
| Changes to canon | — | **Drafted as proposal cards** that Nate accepts with one click. She can't merge, push or apply a Foundry write herself. |
| Model | Local naota model (player mode keeps the DeepSeek fallback) | **Claude Code or Codex CLI** on Nate's subscription, run as a tool-using agent over the hub's tools; local Qwen for quick lookups. Never DeepSeek. |

Why keep the proposal step even though Iris has full access:
- An agent that misreads a transcript should cost Nate one "reject" click, not a git revert across both vaults.
- The proposal card also shows the before/after and the evidence, which is the visual review Nate asked for.
- If that friction proves pointless in practice, add an **"auto-accept from Iris"** switch per proposal type, for example new Open Question tickets. Never for reveals or Foundry.

Isolation still holds:
- `iris-agent` lives in the hub, which already has DM access.
- `iris-bot` (Discord) still has no DM mount and no route to the hub's tools.
- Nothing typed in Discord reaches app Iris's tools.

**What this changes in `azora-iris/docs/INTENT.md`.** Rule 1 ("the bot reads the player vault and nothing else") and the "Not a DM tool" non-goal become:
- *Iris's own process reads the player vault only.*
- *DM knowledge is available solely through the gated DM retriever, for Nate, in DM-safe destinations (ephemeral replies, direct messages, allow-listed channels).*
- *App Iris is a separate DM-side agent inside the hub, not this bot.*

Record this as a deliberate decision in that repo, dated, so no later agent "fixes" it back or widens it further.

### 3.3 Where the models run

| Job | Default | Fallback | Notes |
|---|---|---|---|
| Transcription (S2) | Whisper large-v3-turbo (MLX) on **the Mac**, as a worker that pulls jobs from the hub; silence skipped, campaign names as a prose prompt per chunk, loop filter (Addendum 1) | naota GPU, once Whisper is set up there | Audio never leaves the house. atomsk's CPU is too slow (about 5× slower than realtime). About 6 min per 2 h 20 m track on the M5 Pro. |
| Session ingest and proposals (S4) | **Codex CLI** (ChatGPT subscription) in the hub container | Claude Code CLI (Claude subscription), then local Qwen after the R0 trial | An agent-style task that follows the vault's `AGENTS.md`, which Codex reads natively. |
| Reveal pass (S5) | Same as S4 | Same as S4 | The secret sweep is always Nate's. |
| Discord Iris, DM mode | Local Qwen3.8-27B on naota | Codex or Claude CLI. **Never DeepSeek**: DM-vault text doesn't go to a third-party API that isn't under Nate's account. | Same persona card as player mode. |
| App Iris (agent) | Claude Code or Codex CLI as a tool-using agent | Local Qwen for lookups when the subscription is exhausted | Same persona card plus a GM-mode card. Full DM access (§3.2a). |
| Iris, player mode | Unchanged: local model on naota | Unchanged: DeepSeek cloud | Revisit the fallback: it sends player questions and player-vault snippets to DeepSeek. That's player-safe data, but it still needs the table's OK (§8). |

**Subscription rules:**
- Official CLIs only, signed in by Nate once inside the hub container.
- Never extract or proxy their tokens.
- Check the current plan terms before relying on this.
- Headless Claude draws down plan usage about 1.7× faster than the interactive app (Second-Brain research note, 2026-09-22).
- One naota GPU job at a time, as Iris's invariant 6 already requires. The hub owns a **single GPU queue**, exposed to Iris as an OpenAI-compatible proxy in front of naota's llama-server, that every naota job goes through (Iris in both modes, and Whisper if it ever falls back to naota). Iris's in-bot lock (azora-iris PR #25) stays as a second guard.

---

## 4. The pipeline (the hub's engine)

Unchanged in substance from revision 1. What changed is that each stage now reports progress to the app, and review happens in the app.

| Stage | What | Output | Human gate |
|---|---|---|---|
| **S1 Ingest** | Import a Craig multi-track zip (dropped onto the app, which uploads it to the hub). Map each track to a speaker using `campaign.yaml` (Discord username → label, role, PC). An unmapped track stops the run and asks. | Session archive, outside git: original zip plus `manifest.json` | — |
| **S2 Transcribe** | Local Whisper, one track at a time, VAD-gated, with campaign names as the vocabulary prompt. | `utterances.ndjson` (speaker, `start_ms`/`end_ms`, text, confidence) and `transcript.txt` in the legacy speaker-block format the vault already ingests | — |
| **S3 Stage** | Commit the transcript to `_INBOX/` on branch `scribe/session-NNN` of Azora-DM. | Branch | — |
| **S4 Propose** | Run the vault's Session Ingestor headless on that branch. The agent contract gains a **structured output** (§5.1) next to the Markdown Downstream Plan. | Draft session note, Downstream Plan, OQ tickets, and `proposals.json` | **Review screen**: accept, edit, reject or defer each card, then **Publish to canon**, which merges |
| **S5 Reveal** | Propose Azora-Players updates using the tier rules in `Build-Spec.md`. An automated tripwire blocks `permanent-secrets` and `DM Only` text. | Branch `reveal/session-NNN` and `proposals.json` | **Reveal screen**: Nate's secret sweep, then publish |
| **S6 Foundry** | Sync Azora-Players `main` only to the Foundry journal set, using preview → audience → apply. | Foundry entries; `.foundry/links.json` | **Foundry screen**: see who can read each entry, then apply |

Rules carried over from revision 1:
- Each stage is idempotent and resumable.
- Audio is the authoritative record.
- Transcripts never enter Azora-Players or Foundry.
- The Foundry sync can't read Azora-DM; a path guard enforces this and has a test.
- Foundry folders are created by hand once, because the API can't create them.
- The API can't delete entries, so notes removed from the vault are only reported, never deleted.
- `foundry-sidecar` must be healthy first. It was crash-looping with a `401` in September.

---

## 5. Visual review — "see the update, not the Markdown"

### 5.1 The structured proposal (what the app renders)

The Session Ingestor, the reveal agent and Iris in DM mode all emit the same shape. The Markdown Downstream Plan is still written for the vault's own history, but the app renders from this:

```json
{
  "proposal_id": "p-060-014",
  "batch": "session-060",               // or "iris-dm-2026-10-02T21:14"
  "vault": "dm" ,                        // dm | players
  "op": "update-section",                // create-note | update-section | add-to-list |
                                         // set-frontmatter | open-question | move-note
  "target": "Characters/NPCs/Leon-Blackstone.md",
  "section": "Relationships",
  "before": "…current section text…",   // filled by the HUB from the file, never by the model
  "after":  "…proposed section text…",
  "secret": false,                       // true → lands inside a `DM Only` callout
  "tier": null,                          // players vault only: "world" | "pc:mortala" …
  "rationale": "Leon vouches for the party to Angelica.",
  "evidence": [ { "session": 60, "utterance_id": "u0421" } ],   // hub resolves text + audio
  "conflicts_with": []                   // note/section refs → shown side by side
}
```

Validation happens in the hub, before the app ever sees a proposal:
- The target path exists, or is valid for `create-note`.
- The `before` text is re-read from the file.
- Every piece of evidence resolves to a real utterance in the session's `utterances.ndjson`.
- The quote shown is the hub's copy of that utterance, not the model's wording.
- A players-vault proposal can't contain `permanent-secrets` or text from `DM Only` callouts (the tripwire).
- Proposals that fail validation are shown in a "Rejected by checks" tray with the reason. They aren't silently dropped.

### 5.2 The app's screens

| Screen | What Nate sees |
|---|---|
| **Dashboard** | Next and last session; pipeline progress per stage; Iris health (both agents, naota mode, queue depth); Foundry sidecar status; items waiting for review |
| **Sessions** | Session list. The detail view has the transcript by speaker, colored per player. Click a line to **play that moment** from that speaker's track. Also: a timeline of scenes, the draft session note rendered, and a "re-run transcription" option |
| **Review** | Proposal cards grouped by entity. Each card shows: the **entity header** (portrait from `Assets/Characters`, name, type); a **rendered before/after** with changes highlighted in rendered text, not a raw diff; a secret badge; evidence chips that play audio and show the quote; and conflicts side by side. Actions: Accept · Edit (rich editor) · Reject · Defer · "Ask Iris about this". The **Publish to canon** button merges and pushes, and reports any conflict with Nate's own Obsidian edits |
| **Codex** | A visual browser over the DM vault: NPC, location and faction cards with portraits; quests and arcs; the in-world timeline built from `sort-date`; Open Question tickets. A **"What do the players know?"** toggle shows the DM note beside its Azora-Players counterpart and highlights what hasn't been revealed yet |
| **Reveal** | Player-vault proposal cards, labeled world or PC tier, with tripwire results. This is Nate's secret sweep, one card at a time |
| **Foundry** | Journal entries to create or update, each with the **resolved audience** from preview (`visibleTo`); apply per entry or in a batch after reviewing; history of receipts; orphans |
| **Iris** | Chat with Iris as a full-access agent (§3.2a); she can show her work as proposal cards, search results and session clips; both audit feeds (player-mode questions, and the private DM-mode feed); gate test results; an on/off switch for players; the persona card; leak-sweep results |
| **Settings** | Hub address and token (Keychain), `campaign.yaml` speaker map, model routing, Foundry folders |

The existing app shell's navigation, `ServiceHealth` model and `HealthListView` carry over. The recorder-specific models (`LiveSessionViewModel`, `SidecarClient`, `TranscriptionEngine`) are retired.

### 5.3 Vault writes and Obsidian coexistence

- Azora-DM's `AGENTS.md` says agents share `main` with Nate's live Obsidian sessions and must pull first. So the hub always works on a **branch**. **Publish** does pull → merge → push.
- If Nate's own edits conflict, the app shows both versions and asks. The hub never force-pushes.
- The hub's own commits follow the vault's rules: one commit per coherent change, plus `CHANGELOG.md` or `Meta/DM-Change-Log.md` entries per AGENTS.md §6.
- GitHub PRs become optional. The hub can open one for the record, but Nate doesn't need GitHub to approve anything.

---

## 6. Hub API (sketch)

Tailnet-only HTTPS with a per-device token. The app polls, or uses server-sent events for job progress.

```
GET  /status                         dashboard rollup
POST /sessions            (zip)      S1 ingest → job id
GET  /sessions/:n                    manifest, stage states
GET  /sessions/:n/utterances         paged transcript
GET  /sessions/:n/audio/:track?from=&to=   clip for playback
GET  /proposals?batch=&state=        cards
PATCH /proposals/:id                 accept | edit(after) | reject | defer
POST /batches/:id/publish            merge + push (dm or players)
GET  /codex/notes?type=&q=           rendered entity cards
GET  /codex/notes/*path              one note (+ player-vault counterpart)
POST /foundry/preview                → per-entry audience receipts
POST /foundry/apply                  confirmation tokens from preview only
POST /auth/pair                      one-time pairing code → device token (Addendum 1 §4)
GET  /auth/devices                   paired devices
DELETE /auth/devices/:id             revoke a device
POST /gpu/v1/chat/completions        internal GPU-queue proxy to naota (iris-bot only, own token)
POST /iris/chat           (SSE)      App Iris agent: full DM access, tool calls streamed
GET  /iris/feed?mode=player|dm       audit mirrors (read-only)
```

---

## 7. Iris work this rebuild depends on

Iris goes to players only after the blockers from `azora-iris/docs/REVIEW-2026-09-14.md` are closed. Verify each against the repo's current `main`, since PR #19 may already cover some of them:
- **A1**: the guard vocabulary in the repo. The repo is private now, but history still holds it.
- **B2**: the tautological vault-root check.
- **B6**: re-indexing never deletes, so redactions don't take effect. This matters more once the hub publishes reveals regularly.
- **B3**: the GPU lock, which the hub's GPU queue now provides.
- **B5**: a retrieval outage is served to players as canon.
- The DM-path tests (**B4**) and invariant D (**B7**).

New pieces:
- **The access gate** (§3.2) in `iris-bot`: user ID plus DM-safe channel, with tests for every row of the decision table.
- **`iris-dm-retriever`**: a new small container.
  - Retrieval over the DM index (`azora`; rename it to `azora-dm`, as the Iris hardening offer already suggested).
  - Accepts gate-signed grants only.
  - The player retriever refuses any collection other than `azora-players`.
- **An INTENT.md amendment** recording the DM-mode decision (§3.2).
- **Re-index on publish**: after a reveal publishes, the hub triggers a rebuild of `azora-players` so Iris knows the new facts the same night.

---

## 8. Privacy and consent

- Craig announces the recording. The table has agreed to being recorded.
- **Tell the table once**, plainly:
  - transcripts are made locally
  - text excerpts may be processed by OpenAI or Anthropic under Nate's account for the DM-side steps
  - Iris's player-mode fallback sends player questions to DeepSeek (DM mode never uses DeepSeek)
  - player questions are sent to OpenRouter to be embedded for search (player mode only)
- Audio never leaves Nate's machines.
- Transcripts and DM notes never reach Iris's player mode, Azora-Players or Foundry.
- Credentials (the Discord bot token, the gate's signing key, Foundry `API_KEY`, hub device tokens, the CLI logins) live in `0600` secret files on atomsk and in Keychain on the Mac. They never go in a repo, a log, a proposal or an audit post.
- The Discord bot token pasted in chat on 2026-09-13 should be reset before players arrive, if that hasn't already happened (Second-Brain daily log 2026-09-14).

---

## 9. Acceptance criteria

1. Dropping a real Craig zip onto the app produces a transcript the app can play back line by line, with no unmapped speakers.
2. Silence-only audio produces no utterances; a hallucination-bait fixture yields zero lines.
3. Every proposal card shows a rendered before/after; `before` comes from the file, not the model.
4. Every piece of evidence resolves to a real utterance, and its audio plays. A proposal with a bad citation lands in "Rejected by checks" with the reason.
5. Nothing reaches Azora-DM `main`, Azora-Players `main` or Foundry without an explicit Publish or Apply in the app.
6. The reveal tripwire blocks a planted `permanent-secrets` string and a sentence copied from a `DM Only` callout.
7. The Foundry sync reads nothing under Azora-DM (path-guard test), shows `visibleTo` before every apply, and re-previews on `409`.
8. **The gate's decision table is fully tested:**
   - Nate in a direct message → `dm`.
   - Nate in `#iris-gm` → `dm`.
   - Nate with a normal message in a shared channel → public player-tier reply with a "DM view" button.
   - Nate using `/iris ask`, the "DM view" button, or the message context menu → an **ephemeral** DM-vault reply.
   - A player pressing Nate's "DM view" button → ephemeral "not for you", and no retriever call.
   - Any DM-mode reply that isn't ephemeral, a direct message or an allow-listed channel → refused. A test asserts DM-mode output is never sent as an ordinary channel message.
   - A player holding the DM role → player tier.
   - A player whose message claims to be the DM → `world`.
   - A bot → ignored.
9. **`iris-bot` has no DM-vault mount.** `iris-dm-retriever` rejects calls without a valid grant, with an expired grant, or with a grant for another message. An integration test covers all three.
10. A DM-mode turn never appears in the model context for another user's question, including in the same thread. There is a test with an interleaved thread.
11. DM-mode traffic is logged only to the DM-only audit channel.
12. Iris cannot change a vault except through a proposal that Nate accepts.
13. One naota GPU job at a time, hub-wide. A test runs Whisper and Iris jobs concurrently and sees them serialized.
14. No secret appears in any repo, log, archive, proposal, `links.json` or Discord audit post.

---

## 10. Milestones

- **H0 — Trial and Iris safety (no hub code).**
  - Run the R0 trial on the next game night's Craig export: transcript quality, and Codex vs Claude vs local on the ingest step, graded blind.
  - In parallel, close the Iris blockers from §7.
  - Exit: a trusted transcript path, a chosen S4 provider, and Iris cleared to meet players.
- **H1 — Hub skeleton and Sessions.**
  - `scribe-hub` container with S1–S3 and the GPU queue.
  - App: Settings, Dashboard, and Sessions with the transcript and audio playback.
- **H2 — Review and publish to canon.**
  - The S4 structured-proposal contract, which is a change in the Azora-DM repo's agent files.
  - Hub validation; the Review screen; Publish.
- **H3 — Codex browser and Reveal.**
  - Visual entity cards and the players-know toggle.
  - S5 with the tripwire; re-index `azora-players` on publish.
- **H4 — Foundry.** S6 with the audience-receipt screen. Needs a healthy `foundry-sidecar`.
- **H5 — Iris DM mode.** In Discord: the access gate, slash and context-menu commands with ephemeral replies, `iris-dm-retriever`, and the INTENT.md amendment. In the app: the `iris-agent` with hub tools, proposals from chat, and the audit feeds.
- **H6 — Hardening.** Recovery after reboots (atomsk rebooted without warning on 09-26), backups of the session archive, and Iris rollout to the game server.

H0 can start at the very next session. Each milestone after it gives you something usable on its own.

---

## 11. What happens to the existing repos

| Path | Fate |
|---|---|
| `SessionScribe/` (SwiftUI) | **Kept and rebuilt** as the hub client. The navigation shell and the health components are reused; the recorder models and views are retired. |
| `SessionScribeTests/` | Kept. New view-model tests run against a mock hub client, following the existing mock-service pattern. |
| `hub/` (new, this repo) | The `scribe-hub` service, its tests, and `campaign.yaml.example`. |
| `DAVECaptureSpike/` | **Parked**, kept for reference. |
| `docs/SessionScribe-MVP.md` | **Superseded** for recording. Its archive principles carry over. |
| `docs/FOUNDRY-PUBLISHING-PLAN.md` | **Rules kept.** The client moves into the hub. The Mac app's ATS and entitlement step now applies to the hub connection instead. |
| `docs/CODEX-UPDATE-PIPELINE.md` (other branch) | **Folded in.** Its safety rules survive; its separate codex store is replaced by the vaults. |
| `azora-iris` | Stays Iris's repo and container. It gains the access gate and the INTENT.md amendment. `iris-dm-retriever` lives in `hub/` (open question 4). |
| `azora-homebrew` | Gains the structured-proposal output in its Session Ingestor contract, plus a `CLAUDE.md` that imports `AGENTS.md`. Pushing to it from Claude sessions needs the Claude GitHub App installed on it. |

---

## 12. Open questions for Nate

1. ~~What produced the transcripts for sessions 1–59?~~ **Answered:** Apple SpeechAnalyzer via `yap` (`scribe.sh`). Whisper with names beat it in the Addendum 1 trial.
2. **Do you have a recent Craig export** for the trial?
3. **Foundry scope:** a player codex, session recaps, or both? Any GM-only in-play entries?
4. **Where does `iris-dm-retriever` live?** Recommendation: this repo's `hub/`, so Iris's own repo holds only the gate and never contains DM-side retrieval code.
5. **Should Iris's DM mode replace Futaba** (Hermes) for DM-side Azora questions, or live alongside her? Today Futaba owns the DM-side index and daily vault work.
6. **Iris's player-mode DeepSeek fallback:** keep it, switch it to a subscription CLI, or have her say "I'm resting" when naota is gaming?
