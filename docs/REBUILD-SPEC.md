# SessionScribe Rebuild Specification

| | |
|---|---|
| **Date** | 2026-09-30 |
| **Status** | Draft for Nate's review. Nothing here is approved for implementation yet. |
| **Supersedes** | The recorder track of `docs/SessionScribe-MVP.md` (DAVE capture, live Apple Speech) and the codex store in `docs/CODEX-UPDATE-PIPELINE.md` (branch `docs/codex-update-pipeline`). |
| **Keeps** | The Foundry journal rules in `docs/FOUNDRY-PUBLISHING-PLAN.md`: preview first, show the resolved audience, re-preview on `409`, and never retry a spent token. |

> **In one sentence:** after a game night, turn the Craig recording into an attributed transcript on hardware Nate owns, feed it into the Azora vaults' existing session-ingest workflow for DM approval, and then publish the player-safe results to a Foundry journal set.

---

## 1. Why rebuild

- **The recorder track is stuck.** Milestone 0 (DAVE capture) has not passed since the July 22 live tests (`DAVECaptureSpike/LIVE-TEST-FINDINGS.md`). Everything useful after that point was queued behind it.
- **Most of what you need to keep track of the campaign already exists, outside this repo:**
  - **Azora-DM** (`Hybridenishi/azora-homebrew`) holds 561 notes and 59 ingested sessions. It already has:
    - a Session Ingestor agent (`.github/agents/session-ingest.agent.md`)
    - a Downstream Update Plan with a DM approval block
    - Open Question tickets for continuity conflicts
    - `> [!warning]- DM Only` callouts and `permanent-secrets` for secrets
  - **Azora-Players** (`hybridenishi/azora-players`) is the player-safe vault, with `tier: world | pc:<name>` and `revealed: <session>`. It is air-gapped from the DM vault by design (Second-Brain decision `Azora-Player-Vault-Architecture`, 2026-08-25). Iris already serves it to players.
  - **foundryvtt-mcp** already has a documented journal write API with preview and apply (`docs/JOURNAL-API.md`), including `create-entry`, `add-page` and `update-page`.
- **What's missing is the plumbing between them:** audio to transcript, transcript to vault, player vault to Foundry. This spec rebuilds SessionScribe as that plumbing and leaves the campaign knowledge where it already lives.

### "Won't the vault be too much for the app to keep track of?"

No. SessionScribe never loads the vault into its own memory or database.
- The DM vault is about 28 MB, mostly assets; the Markdown is a few MB.
- A session transcript is small. The session 58 transcript archived in the DM vault is 69 KB, roughly 17k tokens, so a whole session fits in one model context, even the 64K context of the local 27B model.
- The ingest step works like the existing agents already do: it reads the handful of notes a session touches, not the whole vault.

---

## 2. Decisions recorded (Nate, 2026-09-30)

1. **Rebuild, not patch.** The app was stuck, and this replaces its direction.
2. **Recording source: Craig.** No custom Discord voice client. The DAVE spike is parked (section 11).
3. **Knowledge store: Nate's vaults.** GM canon lives in Azora-DM. Player-facing content lives in Azora-Players and is published to a Foundry VTT journal set for the players and for use in play.
4. **Models: local first.** If local models aren't good enough for a step, use Nate's own **Codex (ChatGPT)** or **Claude** subscription through their official CLIs. A pay-per-token API (DeepSeek) is the last resort, not the default.

---

## 3. Pipeline overview

```
 Craig multi-track export (.zip, one file per speaker)
   │  S1 ingest          → session archive (outside git): audio + manifest
   ▼
 S2 transcribe (local Whisper, one track at a time, VAD-gated)
   │                     → utterances.ndjson (timed, attributed) + transcript.txt (legacy format)
   ▼
 S3 stage into Azora-DM  → _INBOX/Session-NNN-transcript.txt on a branch
   ▼
 S4 session ingest (existing Session Ingestor, run headless)
   │                     → session note draft + _INBOX/Session-NNN-Downstream-Plan.md
   │                     → PR on Azora-DM ◄── Nate approves or edits the plan
   │  (approved plan applied by the existing Downstream Update Orchestrator)
   ▼
 S5 reveal pass          → PR on Azora-Players (tier world / pc, revealed: NNN)
   │                     ◄── Nate does the secret sweep and merges
   ▼
 S6 Foundry sync (reads Azora-Players ONLY)
                         → preview → show audience → apply, one entry or page at a time
```

Every arrow that changes canon or reaches players passes through a human gate: a PR merge or a Foundry preview confirmation. No stage merges, publishes, or edits `main` of either vault on its own.

---

## 4. Stages

### S1 — Ingest a Craig recording

- **Input:** Craig's multi-track download, one audio file per Discord user, all aligned to the recording start. FLAC is preferred.
  - Craig deletes recordings after a short retention window, so download right after the session.
  - **Verify on the first real export:** track alignment, file naming, and whether a user who dropped and rejoined gets one track or two.
- **Speaker map:** a campaign config maps each Craig track (Discord username) to the transcript label the vault already uses, and marks the GM:

  ```yaml
  # campaign.yaml (in this repo, no secrets)
  campaign: azora
  speakers:
    <discord-username-1>: { label: "Nate/DM", role: gm }
    <discord-username-2>: { label: "Flame/Mortala", role: player, pc: Mortala }
    <discord-username-3>: { label: "Aaron/Exodus", role: player, pc: Exodus }
    <discord-username-4>: { label: "Jackie Daytona", role: player, pc: "Jackie Daytona" }
  ```

  These labels match `SPEAKER_MAP` in `azora-homebrew/Meta/Scripts/clean-classify-transcript.js`, so existing tooling keeps working.
  - An unmapped track stops the run with a clear message. It never guesses a name.
- **Output:** a session archive folder under a configurable root **outside any git repo** (for example an atomsk share), holding:
  - the original zip, untouched
  - `manifest.json`: session number, date played, tracks, durations, the speaker map snapshot, and tool versions
- The audio in this folder is the authoritative record. Everything after it can be regenerated.

### S2 — Transcribe locally

- **Engine:** Whisper large-v3-class models, run **one track at a time**. Because Craig gives each speaker a separate track, attribution comes free and needs no speaker diarization.
- **Where it runs, in order of preference:**
  1. **MacBook Pro (48 GB Apple Silicon)** using an MLX- or Metal-accelerated Whisper build.
  2. **naota (RX 7900 XT)** using a GPU-accelerated whisper.cpp build.
  3. **Not atomsk's container CPU.** It measured about 5× slower than realtime on 2 cores (Second-Brain `Homelab.md`, 2026-09-17), which is too slow for a four-hour, multi-track session.
- **VAD is required.** Per-speaker tracks are mostly silence, and Whisper makes up text on silence ("thanks for watching"). Transcribe only the speech regions a VAD finds, and drop segments that match the usual hallucination phrases or show no-speech signals.
- **Vocabulary:** seed Whisper's initial prompt with campaign names: PC names plus the `title` and `aliases` of the NPC, location and faction notes in Azora-DM, most recently seen first, trimmed to fit.
- **Output:**
  - `utterances.ndjson`, one line per utterance:
    - `utterance_id` (stable within this transcript revision)
    - `speaker_label`
    - `track`
    - `start_ms` and `end_ms` on the shared session clock
    - `text`
    - `no_speech_prob` and average log-probability, for diagnostics
    - `transcript_revision`
  - `transcript.txt` in the **legacy speaker-block format** the vault has already ingested 59 times: speaker label on one line, text on the next, blank line between. Utterances are merged across tracks in chronological order, and consecutive lines from the same speaker are joined.
- **No cloud in S2.** Audio never leaves Nate's machines.

### S3 — Stage into the DM vault

- On a new branch `scribe/session-NNN` in Azora-DM, write `_INBOX/Session-NNN-transcript.txt`.
  - The vault's own git rules (AGENTS.md §6) require a branch and PR for bulk `_INBOX` imports.
- Pull before writing. If the working tree is dirty or `main` has moved, stop and report instead of forcing.

### S4 — Session ingest (the LLM step)

- **Run the vault's existing Session Ingestor headlessly, inside the Azora-DM checkout**, so it follows that repo's `AGENTS.md`, skills and agent files instead of a second, drifting prompt kept here.
  - Its contract already requires a **Downstream Update Plan** with a DM approval block, and forbids auto-importing NPCs.
- **Output:**
  - a draft session note in `Chronicle/Sessions/` with `status: draft`
  - `_INBOX/Session-NNN-Downstream-Plan.md`
  - any Open Question tickets for continuity conflicts
  - all of it committed to `scribe/session-NNN`, with a **PR** opened on Azora-DM
- **Review happens on the PR.** Nate approves or edits the plan there. After approval, the existing Downstream Update Orchestrator applies it, as a second commit on the same branch or a follow-up run. Merging is always Nate's action.
- **Evidence:** the plan should cite transcript lines (speaker and timestamp from `utterances.ndjson`) for each proposed change, so Nate can check claims against what was actually said. This is a small addition to the ingest agent's output contract, made in the Azora-DM repo.
- **Provider choice:** see section 5.

### S5 — Reveal pass into the player vault

- **When:** after the S4 PR merges.
- **What:** propose updates to Azora-Players following the rules already in its `Build-Spec.md`:
  - `tier: world` for knowledge any well-informed inhabitant would have
  - `tier: pc:<name>` for things only one character knows
  - `revealed: NNN`, plus a `source:` list of DM-vault files
  - an in-world voice, with no "in session 52" meta commentary
- **The transcript is the reveal evidence.** Something said at the table, with the players present, is the strongest signal that it is now player knowledge. The reveal agent works from the merged session note and the transcript, not from the DM vault's DM-only blocks.
- **Hard exclusions** (from Build-Spec.md) are enforced by an automated tripwire before the PR opens:
  - no content from `permanent-secrets`
  - no text from inside `DM Only` callouts
  - nothing from `Meta/` or `_INBOX/`
  - The tripwire checks for normalized substrings and long shared runs of words. Paraphrase remains Nate's secret sweep to catch.
- **Output:** a PR on Azora-Players on branch `reveal/session-NNN`. Nate does the secret sweep there and merges.

### S6 — Foundry journal sync

- **Source:** the Azora-Players `main` branch **only**. The sync code refuses to read any path under Azora-DM. That makes the air gap structural rather than a matter of convention.
- **Mapping:**
  - each player-vault note becomes one Foundry `JournalEntry` with one "Overview" page
  - Markdown is rendered to HTML
  - wikilinks become `@UUID[JournalEntry.<id>]{Label}` when the target is already synced, and plain text otherwise
- **Visibility:**
  - `tier: world` notes → profile `party`
  - `tier: pc:<name>` notes → profile `players` with the owning character's name
  - Nothing is ever published with profile `gm` automatically. GM-only material stays in Obsidian.
- **Link state:** `.foundry/links.json` in Azora-Players, versioned in git. It maps note path to entry ID, page ID, last-published content hash, and the audience receipt (`visibleTo`) from the last apply.
  - Unchanged hash: skip.
  - Changed hash: `update-page`.
  - New note: `create-entry`.
- **Per-session recap (optional; Nate to decide):** a short, player-voiced recap, published as a new entry in a "Session Recaps" folder.
- **API rules (from `docs/JOURNAL-API.md`):**
  - Check `write-status` first. Apply needs Nate's Foundry tab open.
  - Preview every write and **show `visibleTo`** in the run summary.
  - Batch confirmation is allowed only after Nate has seen the audience list for the whole batch.
  - On `409`, preview again. Never retry the token.
  - The API can't create folders, so Nate creates the target folders in Foundry once, by hand.
  - The API can't delete entries, so the sync never tries. A note removed from the vault is reported as "orphaned in Foundry" for Nate to handle.
- **Credentials:** the sidecar URL and `API_KEY` come from the OS keychain, or a `0600` env file on atomsk. They are never stored in a repo, a log, or `links.json`.
- **Prerequisite:** the `foundry-sidecar` container was crash-looping with a `401` (Second-Brain `Homelab.md`, 2026-08-29 and 2026-09-10). Confirm it is healthy before S6 work starts.

---

## 5. Model and provider policy

| Step | Default | Fallback | Why |
|---|---|---|---|
| S2 transcription | Local Whisper (Mac or naota) | none | Free, private, and good enough. Audio never leaves the house. |
| S4 session ingest | **Codex CLI** on the ChatGPT subscription | Claude Code CLI on the Claude subscription, then a local model after the trial | This is an agent task: it reads and edits many files and follows long vault rules. Second-Brain `Local-Models.md` records that local models are "not yet good enough for the main coding workflow". Codex reads `AGENTS.md` natively. |
| S5 reveal pass | Same as S4 | Same as S4 | Same shape of task. The secret sweep stays human. |
| Recap drafting, light cleanup | Local Qwen3.8-27B on naota (64K context) | Subscription CLI | A short single-prompt job that fits local models well. |
| Anything | — | DeepSeek API (last resort) | Only if Nate opts in per campaign. It is the only option that bills per token and sends table talk to a third-party API. |

**Rules for using the subscriptions:**
- Use only the **official CLIs** (`codex exec`, `claude -p`), signed in by Nate on his own machines, for his own use.
  - Never extract, copy or proxy their login tokens.
  - Never call subscription endpoints directly from our code.
  - Check the current plan terms before relying on this.
- Headless Claude Code draws down the plan's usage faster than the interactive app (Second-Brain research note, 2026-09-22: about 1.7×). One ingest per session should be fine; batch re-runs may not be.
- **Where it runs:** on the MacBook, or on the `paperclip` VM, where both CLIs have been signed in since 2026-09-25. The Hermes container's `claude` CLI has never been signed in, and Hermes's bot surface can't use the subscription plugin (upstream issue #16), so don't plan on Hermes calling the subscription itself. Hermes can trigger the pipeline and report on it.
- For Claude Code, add a one-line `CLAUDE.md` to Azora-DM that imports `AGENTS.md`, so both CLIs read the same rules.

**The trial decides the local question, not guesses.** Section 9 (R0) runs the same transcript through Codex, Claude and local Qwen3.8-27B, and Nate grades the three Downstream Plans blind. A local model takes over the S4 role only if it matches on that grading.

---

## 6. Where it runs and what gets built

- **SessionScribe becomes a small command-line pipeline (`scribe`)**, written in Python to match the rest of Nate's automation and the Whisper tooling, living in this repo under `pipeline/`:

  ```
  scribe ingest <craig.zip> --session 60      # S1
  scribe transcribe <session-dir>             # S2 (runs on Mac or naota)
  scribe stage <session-dir>                  # S3
  scribe ingest-vault <session-dir> --provider codex|claude|local   # S4
  scribe reveal --session 60 --provider ...   # S5
  scribe foundry-sync [--dry-run]             # S6
  scribe run <craig.zip> --session 60         # S1→S4, stops at the first human gate
  ```

- Each stage is **idempotent and resumable**. It records its state in the session archive and refuses to overwrite a later stage's output without `--force`.
- **Hermes** can run `scribe run` and post status in Discord, like its other jobs. That's optional and comes later.
- **The SwiftUI app is parked, not deleted.** A native front-end can come back later as a thin shell over `scribe`: drop a zip, watch progress, open the PRs. Review happens on GitHub, which Nate already uses for both vaults.

---

## 7. Privacy and consent

- Craig announces the recording in Discord. The table has agreed to being recorded.
- **Tell the table once** that transcripts are processed by an AI service, and which one. The default path (local Whisper plus Codex or Claude) sends **text only**, never audio, to OpenAI or Anthropic under Nate's own account. DeepSeek is used only if the table has agreed.
- Transcripts live in the DM vault's `_INBOX` and are archived there after ingest, as today. They never enter Azora-Players or Foundry.
- Secrets never enter logs, repos, `links.json` or run summaries.

---

## 8. Acceptance criteria

1. Given a real Craig export and `campaign.yaml`, `scribe transcribe` produces `utterances.ndjson` and a `transcript.txt` that `clean-classify-transcript.js` parses with no unknown speakers.
2. An unmapped Craig track stops S1 with a message naming the track. Nothing is guessed.
3. Silence-only stretches produce no utterances. A fixture of a silent track with known hallucination bait yields zero lines.
4. S4 opens a PR on Azora-DM containing a draft session note and a Downstream Plan with a DM approval block. Nothing lands on `main` without Nate merging.
5. Every change proposed in the Downstream Plan cites at least one transcript speaker and timestamp.
6. The S5 tripwire blocks a reveal PR that contains a planted `permanent-secrets` string or a sentence copied from a `DM Only` callout.
7. S6 reads nothing under Azora-DM. A unit test proves the path guard.
8. S6 shows the resolved `visibleTo` for every write before applying. A `409` leads to a fresh preview, never a retried token.
9. Re-running S6 with no vault changes makes zero writes.
10. No API key or token appears in any repo, log, archive, or `links.json`.
11. The whole pipeline works with **no** subscription CLI signed in, as long as a local provider is configured. Only S4 and S5 quality changes.

---

## 9. Milestones

- **R0 — One real session, mostly by hand (the trial).**
  - Take the next game night's Craig export, or an older one if it's still available.
  - Transcribe it with a throwaway script.
  - Compare it against the previous transcript source for the same kind of session: speaker accuracy, name spelling, hallucinations.
  - Run the Session Ingestor with Codex, Claude and local Qwen on the same transcript.
  - Nate grades the plans blind (the `model-trial-harness` method from Second-Brain).
  - **Exit:** a transcript Nate considers at least as good as today's source, and a chosen S4 provider.
- **R1 — `scribe` S1–S3.** Ingest, transcribe and stage, with the section 8 criteria 1–3 as tests.
- **R2 — S4 headless ingest and PR.** Also add transcript citations to the Session Ingestor contract, in the Azora-DM repo.
- **R3 — S5 reveal pass and tripwire.**
- **R4 — S6 Foundry sync.** Blocked on a healthy `foundry-sidecar`. Dry-run first, then the world tier, then the PC tier.
- **R5 — Hardening and comfort.** Hermes trigger, resumable runs, run summaries in Discord, and optionally a thin Mac front-end.

R0 needs no code in this repo and can happen at the very next session.

---

## 10. How this resolves the review of `CODEX-UPDATE-PIPELINE.md`

| Problem in that spec | How the rebuild handles it |
|---|---|
| One visibility tier per record, though an NPC mixes public facts and secrets | Tiers are handled per fact by what the vaults already have: `DM Only` callouts and `permanent-secrets` inside a note, and the separate Azora-Players vault for player-safe facts. |
| Conflict citations couldn't point at older sessions or hand-written facts | Conflicts become Open Question tickets, the vault's existing mechanism, which link any note or session. |
| Model-written quotes were fragile | The plan cites a speaker and timestamp. The app, not the model, holds the text in `utterances.ndjson`. |
| Name matching missed ASR-mangled homebrew names | The vault's names seed Whisper's vocabulary, so names are spelled right before any matching happens. |
| Duplicate proposals within one run | One transcript produces one plan: the whole session fits in a single context, with no chunking. |
| A new codex store to design and keep in sync | None. The vaults are the store. |
| Blocked on DAVE | Craig replaces DAVE entirely. |

---

## 11. What happens to the existing repo

| Path | Fate |
|---|---|
| `DAVECaptureSpike/` | **Parked.** Kept for reference. Its findings stay valid if a native recorder is ever wanted again. |
| `SessionScribe/` (SwiftUI) and `SessionScribe.xcodeproj` | **Parked.** A possible future front-end over `scribe`. |
| `docs/SessionScribe-MVP.md` | **Superseded** for recording and live transcription. Its archive principles (audio is authoritative, append-only transcript revisions) carry over to S1 and S2. |
| `docs/FOUNDRY-PUBLISHING-PLAN.md` | **Rules kept, client rewritten.** S6 implements the same API contract in the pipeline instead of a Swift client. Step 0 (ATS and entitlements) only matters if the Mac app returns. |
| `docs/CODEX-UPDATE-PIPELINE.md` (other branch) | **Folded in** as described in section 10. Its safety rules survive: no model writes canon, human gates, audience receipts. |
| `pipeline/` | **New.** The `scribe` CLI, its tests and fixtures. |

---

## 12. Open questions for Nate

1. **What produced the transcripts for sessions 1–59?** They're in speaker-block format with no timestamps, for example `_INBOX/Archived-After-Ingest.zip` → `session 58.ingested-2026-06-02.txt`. If that tool is still available, R0 has a baseline to beat.
2. **Do you still have a recent Craig export?** R0 is fastest with one in hand.
3. **Foundry journal scope:** a player codex (world tier plus PC tiers), session recaps, or both? Do you also want GM-only in-play reference entries in Foundry, which would be the one exception to "S6 never publishes `gm`"?
4. **Where should the session archive (audio) live:** an atomsk share such as `data` or `work`, or on the MacBook?
5. **Should Hermes run the pipeline eventually,** or do you prefer to run `scribe` yourself from the Mac?
