# SessionScribe Rebuild Specification — the Azora Hub

| | |
|---|---|
| **Date** | 2026-09-30 (revision 2, same day) |
| **Status** | Draft for Nate's review. Nothing here is approved for implementation yet. |
| **Supersedes** | The recorder track of `docs/SessionScribe-MVP.md` (DAVE capture, live Apple Speech), the codex store in `docs/CODEX-UPDATE-PIPELINE.md` (branch `docs/codex-update-pipeline`), and revision 1 of this file, which was a headless command-line pipeline that used GitHub PRs as its review screen. |
| **Keeps** | The Foundry journal rules in `docs/FOUNDRY-PUBLISHING-PLAN.md`: preview first, show the resolved audience, re-preview on `409`, and never retry a spent token. Also the six invariants of `azora-iris/docs/INTENT.md`. |

> **In one sentence:** SessionScribe becomes the **Azora Hub**. It is an always-on service on atomsk that turns Craig recordings into transcripts, turns transcripts into proposed vault changes, and runs Iris. A native Mac app is where Nate sees and approves everything visually, and Discord is where the players meet Iris.

---

## 1. Why rebuild

- **The recorder track is stuck.** Milestone 0 (DAVE capture) has not passed since the July 22 tests (`DAVECaptureSpike/LIVE-TEST-FINDINGS.md`). Craig replaces it.
- **Most of what you need to keep track of the campaign already exists; it just isn't connected or visible:**
  - **Azora-DM** (`Hybridenishi/azora-homebrew`) holds 561 notes and 59 ingested sessions. It already has:
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
7. **"One face, two agents."**
   - Iris has one persona everywhere, but two separately privileged processes:
     - **GM-Iris** can see the DM vault and transcripts, and answers only Nate.
     - **Player-Iris** stays air-gapped exactly as today.
   - The boundary is enforced by credentials, containers and file mounts, never by prompts.
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
                         │  iris-gm  (container)                                       │
                         │   • reads: Azora-DM, transcripts, Qdrant `azora`            │
                         │   • writes: NOTHING directly — only files proposals         │
                         │     into scribe-hub                                         │
                         │   • talks to: Nate only (app chat + private Discord bot)    │
                         │                                                             │
                         │  iris-bot  (container — exists today = Player-Iris)         │
                         │   • reads: Azora-Players (ro), Qdrant `azora-players` only  │
                         │   • no route to scribe-hub, iris-gm, or the DM vault        │
                         │   • emits: health + audit events (one-way) to scribe-hub    │
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
| **iris-gm** (GM-Iris) | atomsk container | Nate's conversational agent: answers DM-side questions, drafts proposals on request ("add that Thorn owes Fang a favor") | Write to a vault directly; talk to anyone except Nate; run in any channel players can read |
| **iris-bot** (Player-Iris) | atomsk container (exists) | Player lore answers in voice, tier-gated in code | Read anything but the player vault and its index. **Unchanged from `azora-iris` INTENT.md.** |
| **SessionScribe.app** | Nate's Mac | Visual review and control; audio playback of cited moments | Hold credentials other than its own hub token (in Keychain); talk to Foundry or GitHub directly |

### 3.2 Why "one face, two agents" is built this way

The previous player bot was taken down for leaking DM material, and `azora-iris` exists so that failure is impossible by design. Merging GM powers into it would undo that. So:

- **Separate containers, separate mounts.** Player-Iris's container has no DM-vault mount and no network route to the hub's write API or to GM-Iris. A bug in Player-Iris can't reach DM data because the data isn't there.
- **Separate Discord bot accounts with the same face.**
  - The player-facing bot is today's `Azora#4235`.
  - GM-Iris is a **second bot application** with the same name, avatar and persona card. It is invited only to a private channel (for example `#iris-gm`) that only Nate can see.
  - It also hard-checks Nate's Discord **user ID**, not a role, and ignores everyone else. Roles are for sorting players; they aren't enough to protect secrets.
  - Two tokens means leaking one can't impersonate the other.
- **Same persona, different knowledge.** Both load `persona/iris.md`. GM-Iris adds a GM-mode rules card, and in-character framing is optional there.
- **Proposals are the only way GM-Iris changes anything.** Everything GM-Iris wants to change becomes a proposal card in the app, exactly like the ones from sessions. There's one approval path for both.

### 3.3 Where the models run

| Job | Default | Fallback | Notes |
|---|---|---|---|
| Transcription (S2) | Local Whisper on **naota's GPU**, driven by the hub over the LAN | The MacBook, on demand | Audio never leaves the house. atomsk's CPU is too slow (about 5× slower than realtime). |
| Session ingest and proposals (S4) | **Codex CLI** (ChatGPT subscription) in the hub container | Claude Code CLI (Claude subscription), then local Qwen after the R0 trial | An agent-style task that follows the vault's `AGENTS.md`, which Codex reads natively. |
| Reveal pass (S5) | Same as S4 | Same as S4 | The secret sweep is always Nate's. |
| GM-Iris chat | Local Qwen3.8-27B on naota | Codex or Claude CLI when naota is in gaming mode or the question needs file-hopping | The persona card works on the local model today (Player-Iris's Phase 1 trial). |
| Player-Iris | Unchanged: local model on naota | Unchanged: DeepSeek cloud | Revisit the fallback: it sends player questions and player-vault snippets to DeepSeek. That's player-safe data, but it still needs the table's OK (§8). |

**Subscription rules:**
- Official CLIs only, signed in by Nate once inside the hub container.
- Never extract or proxy their tokens.
- Check the current plan terms before relying on this.
- Headless Claude draws down plan usage about 1.7× faster than the interactive app (Second-Brain research note, 2026-09-22).
- One naota GPU job at a time, as Iris's invariant 6 already requires. The hub owns a **single GPU queue** that Whisper, GM-Iris and Player-Iris all go through. That also fixes Iris review finding **B3**.

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

The Session Ingestor, the reveal agent and GM-Iris all emit the same shape. The Markdown Downstream Plan is still written for the vault's own history, but the app renders from this:

```json
{
  "proposal_id": "p-060-014",
  "batch": "session-060",               // or "iris-gm-2026-10-02T21:14"
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
| **Iris** | Chat with GM-Iris; the Player-Iris activity feed (a mirror of the audit channel); an on/off switch for players; the persona card; leak-sweep results |
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
POST /iris-gm/chat        (SSE)      GM-Iris conversation
GET  /iris/player/feed               Player-Iris audit mirror (read-only)
```

---

## 7. Iris work this rebuild depends on

Player-Iris goes to players only after the blockers from `azora-iris/docs/REVIEW-2026-09-14.md` are closed. Verify each against the repo's current `main`, since PR #19 may already cover some of them:
- **A1**: the guard vocabulary in the repo. The repo is private now, but history still holds it.
- **B2**: the tautological vault-root check.
- **B6**: re-indexing never deletes, so redactions don't take effect. This matters more once the hub publishes reveals regularly.
- **B3**: the GPU lock, which the hub's GPU queue now provides.
- **B5**: a retrieval outage is served to players as canon.
- The DM-path tests (**B4**) and invariant D (**B7**).

New pieces:
- **`iris-gm`**: a new container and a second Discord bot application.
  - Persona shared with Player-Iris, plus a GM rules card.
  - Retrieval over the DM index (`azora`; rename it to `azora-dm`, as the Iris hardening offer already suggested). Player-Iris's retriever refuses any collection other than `azora-players`.
- **Re-index on publish**: after a reveal publishes, the hub triggers a rebuild of `azora-players` so Player-Iris knows the new facts the same night.

---

## 8. Privacy and consent

- Craig announces the recording. The table has agreed to being recorded.
- **Tell the table once**, plainly:
  - transcripts are made locally
  - text excerpts may be processed by OpenAI or Anthropic under Nate's account for the DM-side steps
  - Player-Iris's fallback sends questions to DeepSeek
- Audio never leaves Nate's machines.
- Transcripts and DM notes never reach Player-Iris, Azora-Players or Foundry.
- Credentials (Discord tokens ×2, Foundry `API_KEY`, hub device tokens, the CLI logins) live in `0600` secret files on atomsk and in Keychain on the Mac. They never go in a repo, a log, a proposal or an audit post.
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
8. **Player-Iris's container has no DM-vault mount and can't reach `scribe-hub` or `iris-gm` over the network.** An integration test asserts all three.
9. GM-Iris ignores messages from any Discord user except Nate's ID, and from any channel outside its allow-list. Tested with a non-Nate account.
10. GM-Iris cannot change a vault except through a proposal that Nate accepts.
11. One naota GPU job at a time, hub-wide. A test runs Whisper, GM-Iris and Player-Iris jobs concurrently and sees them serialized.
12. No secret appears in any repo, log, archive, proposal, `links.json` or Discord audit post.

---

## 10. Milestones

- **H0 — Trial and Iris safety (no hub code).**
  - Run the R0 trial on the next game night's Craig export: transcript quality, and Codex vs Claude vs local on the ingest step, graded blind.
  - In parallel, close the Iris blockers from §7.
  - Exit: a trusted transcript path, a chosen S4 provider, and Player-Iris cleared to meet players.
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
- **H5 — GM-Iris.** The `iris-gm` container and second bot; app chat; proposals from chat; the Player-Iris feed in the app.
- **H6 — Hardening.** Recovery after reboots (atomsk rebooted without warning on 09-26), backups of the session archive, and Player-Iris rollout to the game server.

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
| `azora-iris` | Player-Iris stays its own repo and container. It gains a GM rules card, and `iris-gm` is added either there or in `hub/` (open question 4). |
| `azora-homebrew` | Gains the structured-proposal output in its Session Ingestor contract, plus a `CLAUDE.md` that imports `AGENTS.md`. Pushing to it from Claude sessions needs the Claude GitHub App installed on it. |

---

## 12. Open questions for Nate

1. **What produced the transcripts for sessions 1–59?** This is the quality bar for H0.
2. **Do you have a recent Craig export** for the trial?
3. **Foundry scope:** a player codex, session recaps, or both? Any GM-only in-play entries?
4. **Where does GM-Iris's code live:** in `azora-iris`, sharing the persona files, or in this repo's `hub/`, keeping Player-Iris's repo purely player-side? Recommendation: `hub/`, with the persona card vendored in, so the player bot's repo never contains DM-side code.
5. **Should GM-Iris replace Futaba** (Hermes) for DM-side Azora questions, or live alongside her? Today Futaba owns the DM-side index and daily vault work.
6. **Player-Iris's DeepSeek fallback:** keep it, switch it to a subscription CLI, or have her say "I'm resting" when naota is gaming?
