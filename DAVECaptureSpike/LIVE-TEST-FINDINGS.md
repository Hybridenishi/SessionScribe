# DAVE capture spike: live-test findings

**Test date:** July 22, 2026

**Spike build:** D++/DPP 10.1.5 on macOS

**Status:** The Milestone 0 DAVE capture gate has **not passed**.

This note records the evidence from the first three private voice-channel runs. It intentionally omits bot credentials, guild/channel identifiers, and participant identifiers.

## What the spike has proven

- The bot can authenticate, join the requested Discord voice channel with DAVE enabled, and report a voice-ready callback.
- Discord speaking-state events can be mapped to a stable participant ID.
- When DPP delivers decoded PCM, the recorder can write a valid per-user WAV file and finalize a complete manifest.
- The recorder can detect RTP sequence gaps in delivered packets. The gap logic is also covered by deterministic tests.
- Control-C finalizes the event log and manifest. The spike now requests an explicit voice disconnect before shutting down; that cleanup change still needs one live verification.

These are component proofs. They do not establish reliable DAVE-compatible recording.

## Run evidence

### Run 001 — bot joined before the human participant

- A human participant joined after the bot was already in the voice channel.
- DPP delivered 1,731 decoded PCM frames mapped to one participant.
- The spike wrote a valid 48 kHz, stereo, signed-16-bit WAV track lasting about 34.7 seconds.
- Six RTP gaps were recorded, representing about 0.9 seconds of missing sequence ranges.
- DPP repeatedly warned that decryption failed because a frame was not encrypted while passthrough was disabled.
- DPP emitted two voice-ready callbacks. The spike labeled the second callback as a reconnect and opened epoch 2, but no actual network disconnect/reconnect was performed. This run therefore does not prove reconnect behavior.

**Result:** Partial audio-path proof, with unresolved DAVE transition/decryption failures and packet loss.

### Run 002 — no received audio

- Discord emitted a speaking-state event before the voice-ready callback.
- DPP never delivered a decoded audio frame.
- The manifest finalized successfully with no tracks.
- At this point the spike did not explicitly request a voice disconnect, so Discord displayed the bot in the channel until the dead connection timed out. The spike has since been changed to request a voice disconnect before shutdown.

**Result:** Clean empty finalization; no recording proof.

### Run 003 — human joined before the bot

- The human participant joined the voice channel first.
- The spike then authenticated, joined, and printed `Voice connection ready. Recording is armed; you can speak now.`
- The participant spoke for approximately two minutes after that message.
- DPP delivered speaking state but never invoked the decoded-audio receive callback.
- The terminal correctly reported that no audio frames arrived, and the manifest finalized successfully with no tracks.
- No decryption warning explained the missing receive callbacks in this run.

**Result:** A voice-ready callback is not sufficient evidence of a healthy DAVE receive path. Stable recording failed even when speech began after the ready signal.

## Current interpretation

The results are order-dependent:

| Join order | Observed result |
| --- | --- |
| Bot first, participant joins later | Some PCM delivered, with repeated DAVE decrypt/passthrough warnings |
| Participant first, bot joins later | Voice-ready and speaking state, but no PCM delivered |

This pattern points to an unresolved DAVE membership, key-epoch, or transition-handling problem in the current DPP receive path. It does **not** look like a token, command-line, output-directory, microphone-timing, or Control-C problem. The exact fault has not yet been isolated, so this is a working diagnosis rather than a confirmed upstream defect.

The speaking-state callback must not be treated as proof that audio is recordable. For this spike, only the first successfully delivered audio frame changes the terminal state to `Recording started`.

## Milestone 0 gate status

| Requirement | Status | Evidence needed to pass |
| --- | --- | --- |
| Join a DAVE voice channel | Partial | Negotiated DAVE version and healthy receive state must be observable, not inferred only from voice-ready. |
| Receive per-user audio | Failed reliability gate | Audio must arrive in both participant join orders without unexplained decrypt failures. |
| Playable per-user tracks | Partial | One single-user WAV exists; every speaker must receive an independently playable track. |
| Preserve overlap | Not tested | At least two simultaneous speakers must remain separate and aligned. |
| Survive reconnect | Not tested | Force a real disconnect/reconnect and verify a new epoch plus resumed audio. |
| Report gaps | Partial | Logic is tested and gaps were observed, but loss behavior must be verified during a controlled interruption. |
| Preserve canonical Opus | Blocked by current API path | DPP's public receive callback exposes decoded PCM; the MVP requires preserved decrypted Opus in a valid container. |
| Clean shutdown/leave | Implemented, live verification pending | Confirm the bot leaves promptly after Control-C and all files remain complete. |

The spike must not be integrated into the SwiftUI app while the reliable receive and canonical Opus gates remain unresolved.

## Recommended next investigation

The smallest useful next step is a diagnostic build of the standalone sidecar. It should record, in redacted structured logs:

- negotiated DAVE protocol version;
- DAVE prepare/execute transition events and key epochs;
- passthrough enabled/disabled transitions;
- decryption successes and failures by reason;
- voice-ready callbacks distinguished from actual disconnect/reconnect events;
- first speaking-state event versus first successfully received media frame; and
- packet/frame counters before and after each participant joins or leaves.

Repeat a short matrix after those diagnostics exist:

1. Participant present before the bot joins.
2. Bot present before the participant joins.
3. Participant leaves and rejoins during capture.
4. A controlled voice/network reconnect.

Each case should include speech before and after the membership change. Repeating the existing test without protocol-state diagnostics is unlikely to reveal more.

## Possible solution directions

### 1. Validate or patch DPP's current DAVE transition path

Build against a pinned DPP source revision with additional DAVE logging. If the missing receive callbacks come from an identifiable transition-state defect, fix or carry a narrowly scoped patch and rerun the full gate. This is the smallest architectural change, but it still does not solve canonical Opus preservation unless the receive boundary is extended below DPP's decoded-PCM callback.

### 2. Extend or fork the C++ receiver boundary around `libdave`

Keep the C++ sidecar and recorder contract, but handle encoded media at a layer where decrypted Opus frames and DAVE state are available. Discord's `libdave` is the natural implementation candidate. This direction better matches the canonical-audio requirement, but it requires more ownership of Discord voice gateway, RTP/transport, sender mapping, and protocol transitions than the current DPP callback exposes.

### 3. Evaluate another sidecar implementation

The MVP explicitly permits another language or runtime behind the same local protocol. Any candidate must be tested—not selected from documentation alone—against the same join-order, overlap, reconnect, per-user mapping, and raw-Opus requirements. A library that only yields mixed audio or decoded PCM does not pass the product gate.

### 4. Preserve the recorder while replacing only transport

The WAV writer, event schema experiment, RTP gap logic, tests, and terminal readiness signals remain useful spike assets. They can be retained as evidence tooling while the Discord/DAVE transport layer is replaced. None of these components should be promoted as the production archive writer because the MVP requires canonical Opus containers owned by the native ingestion boundary.

## Update: candidate root cause found, local patch prepared

Reading D++ 10.1.5's own source turned up a concrete, plausible root cause for the
order-dependent behavior above, plus a fix. See [PATCHED-DPP.md](PATCHED-DPP.md) for
the full diagnosis and a locally patched D++ build to test against. Summary: D++'s
vendored DAVE decryptor has a passthrough-grace-period mechanism for exactly this
"peer's frames are briefly unencrypted during their own MLS onboarding" case, but
it has no caller anywhere in D++ — every transitional unencrypted frame hits a hard
failure path instead. **Not yet confirmed against a live Discord session** — the next
live-test round should run against `build-patched/` and check
`dpp-dave-diagnostics.log` alongside the existing evidence files.

## Decision checkpoint

Do not begin native app/sidecar integration until one transport direction demonstrates:

- clean audio receipt in both join orders;
- separate tracks for at least three participants, including overlap;
- a real reconnect with an explicit new epoch and resumed audio;
- gap reporting during a controlled interruption;
- access to decrypted Opus plus RTP/identity metadata; and
- no secrets in diagnostics or artifacts.
