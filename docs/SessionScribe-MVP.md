# SessionScribe

**MVP App Document**  ·  Native macOS recorder and transcription workspace

| **Working product name** | SessionScribe |
| --- | --- |
| **Xcode project name** | SessionScribe |
| **Platform** | macOS · SwiftUI · Swift |
| **Document status** | MVP definition · Draft 1 · July 21, 2026 |

> **MVP IN ONE SENTENCE:** A Mac app that joins a Discord voice channel through a bundled receiver, records synchronized per-user audio, produces a live speaker-attributed transcript using Apple Speech, and saves a recoverable session archive.

## 1. Product direction

SessionScribe is a native macOS application with one deliberately replaceable helper process: the Discord voice sidecar. The app owns the user experience, session state, durable files, transcription, and review workflow. The sidecar owns Discord voice transport and nothing above it.

The architectural rule is simple: audio is authoritative. Live transcription may be revised, regenerated, or replaced without losing the original per-user recordings.

### MVP outcome

At the end of the MVP, one person can launch SessionScribe, choose a Discord voice channel, visibly start a consensual recording, watch attributed speech appear during play, stop the session, and reopen a folder containing playable speaker tracks plus a chronological transcript.

### Success demonstration

- Run a 30-minute session with at least three participants.
- Record separate, synchronized tracks while two participants speak at once.
- Display provisional transcript updates tagged with stable Discord identities.
- End with a self-contained archive that remains usable if the app database is lost.
- Restart the app and reopen the completed session without retranscribing it.

## 2. MVP scope

### Included

- A native SwiftUI macOS app with a local session library.
- Discord bot authentication and voice-channel selection.
- A DAVE-capable sidecar that receives and identifies individual participant audio.
- Per-user Opus archives, session-relative timestamps, gap markers, and a session manifest.
- Apple-native streaming transcription with partial and final revisions.
- A live transcript view, recording timer, participant state, and health indicators.
- Explicit recording notice and a consent-ready start flow.
- Readable transcript export plus an append-only NDJSON event log.
- Basic post-session reprocessing from the saved audio.

### Explicitly deferred

- Automated campaign summaries, NPC extraction, rules lookup, initiative tracking, and other D&D intelligence.
- iPhone, iPad, Windows, Linux, or web clients.
- Cloud synchronization, accounts, multi-user editing, and remote hosting.
- A full waveform editor or professional audio mixing interface.
- A custom Discord voice implementation written from scratch in Swift.
- App Store distribution, polished onboarding, automatic updates, and production telemetry.
- CRDT-based transcript collaboration. Append-only revisions are sufficient for MVP.

> **SCOPE GUARDRAIL:** Do not build transcript intelligence until the recorder survives overlap, packet loss, reconnects, long sessions, and app restarts. Reliable capture is the product foundation.

## 3. User experience

1. **Open.** The app shows recent sessions and a prominent New Session action.
2. **Connect.** The user selects a configured Discord server and voice channel. Connection health and joined participants are visible before recording.
3. **Confirm.** The app displays the recording disclosure and requires an explicit confirmation before writing participant audio.
4. **Record.** A persistent red recording state shows elapsed time, participants, per-user speech activity, sidecar health, disk status, and transcription status.
5. **Review.** The transcript scrolls chronologically. Overlapping utterances remain independently attributed rather than being forced into a single speaker lane.
6. **Stop.** The app finalizes containers and manifests, marks incomplete segments, and presents the saved session.
7. **Reopen.** The user can read the transcript, play a participant track near an utterance, and request retranscription.

### Primary screens

| **Screen** | **MVP responsibility** |
| --- | --- |
| **Session Library** | Recent sessions, date, duration, participant count, processing state, and storage location. |
| **New Session** | Discord connection, channel choice, recording disclosure, storage estimate, and Start control. |
| **Live Session** | Recording state, timer, participants, live transcript, sidecar health, speech-engine health, and Stop control. |
| **Session Detail** | Transcript, participant filter, playback jump, export, retranscribe, reveal-in-Finder, and archive health. |
| **Settings** | Bot credentials, storage directory, speech locale/model status, retention preference, and diagnostic logging. |

## 4. System boundary

The native app and the sidecar communicate only through a small, versioned local protocol.

1. **Discord voice channel.** Remote source of participant identities, speaking state, and encrypted Opus media.
2. **Voice sidecar.** Joins the channel, handles DAVE and transport details, maps audio streams to Discord user IDs, and emits normalized events and frames.
3. **Native ingestion layer.** Validates messages, anchors timestamps to the session clock, journals metadata, and writes audio safely.
4. **Speech pipeline.** Decodes and converts audio, detects utterance activity, manages per-active-user recognizers, and emits revisions.
5. **Session store.** Persists manifest, audio, transcript events, diagnostics, and human-readable exports.
6. **SwiftUI workspace.** Controls the session and presents status, transcript, playback, and recovery actions.

### Native app responsibilities

- Own all visible state and user actions.
- Launch, supervise, restart, and terminate the sidecar.
- Create the session clock and session directory before capture begins.
- Write canonical archives and commit manifests atomically.
- Decode/resample audio for Apple Speech without changing the canonical Opus archive.
- Throttle partial transcript updates and preserve final revisions.
- Surface gaps, failures, resource exhaustion, and degraded modes honestly.

## 5. Discord sidecar notes

> **WHY A SIDECAR:** Discord voice transport is volatile infrastructure. Isolating it lets the project choose the strongest DAVE-capable library without binding the UI, session files, or speech pipeline to that language or ecosystem.

### Sidecar owns

- Discord gateway and voice connection lifecycle.
- Voice protocol negotiation, UDP/RTP handling, DAVE participation and frame decryption.
- Opus packet receipt, RTP sequence/timestamp extraction, and packet-loss detection.
- SSRC-to-user mapping and participant join, leave, mute, and speaking events.
- Reconnect/resume attempts and explicit connection-epoch changes.
- Minimal diagnostics needed to explain transport failures.

### Sidecar does not own

- Apple Speech, VAD policy, transcript revisions, or D&D vocabulary.
- The authoritative session directory or long-term storage policy.
- Consent decisions, UI, playback, summaries, or transcript editing.
- Product-level recovery policy beyond reporting transport facts.

### Process model

| **Concern** | **MVP decision** |
| --- | --- |
| **Packaging** | Bundled helper executable inside the macOS app during MVP development. |
| **Launch** | Spawned only for an active connection; app passes a short-lived configuration through a protected local channel. |
| **Transport** | Prefer a Unix domain socket. A localhost socket is acceptable for the spike if authentication and port ownership are explicit. |
| **Framing** | Length-prefixed envelopes: small JSON control/events plus binary Opus media frames. |
| **Supervision** | Version handshake, heartbeat, process-exit monitoring, bounded restart policy, and visible degraded state. |
| **Logging** | Structured local diagnostics with secret redaction; session logs remain separate from transcript events. |
| **Compatibility** | Protocol version is negotiated at startup. Unknown major versions fail closed with an actionable message. |

### Minimum sidecar event contract

| **Envelope** | **Required content** |
| --- | --- |
| **hello** | Protocol version, build ID, capabilities, supported DAVE version, and audio format. |
| **connection** | Connecting, connected, resuming, disconnected, failed, and connection epoch. |
| **participant** | Stable Discord user ID, current display name, SSRC, and join/leave state. |
| **speaking** | User ID, active/inactive state, and sidecar monotonic timestamp. |
| **audio** | User ID, epoch, RTP sequence, RTP timestamp, arrival time, flags, and Opus payload. |
| **gap** | User ID, missing sequence range, estimated duration, and cause when known. |
| **metrics** | Packet counts, loss, jitter estimate, queue depth, reconnect count, and last-receive time. |
| **fatal** | Machine-readable reason, sanitized message, retryability, and relevant protocol state. |

### Technology selection gate

Do not select the sidecar language because it is familiar. Select it after a capture spike proves current Discord DAVE compatibility, individual stream mapping, reconnect behavior, packaging viability on macOS, and a maintainable dependency path. Rust, Node, Python, or another runtime are all acceptable behind the same contract.

## 6. Audio and transcription

### Canonical audio

- Preserve received Opus frames and transport metadata; do not make PCM the only archive.
- Segment tracks on connection-epoch changes or other discontinuities, then describe every segment in the manifest.
- Use stable Discord user IDs in paths; record display-name history separately.
- Generate seekable Ogg Opus or another valid container rather than merely naming raw bytes with an .opus extension.
- Treat decoded PCM as a transient speech/playback representation that can be regenerated.

### Apple Speech strategy

- Evaluate SpeechAnalyzer with SpeechTranscriber first on the minimum supported macOS version.
- Keep SFSpeechRecognizer as a compatibility implementation behind the same internal protocol.
- Create recognition work per active participant, not permanently for every person in the channel.
- Feed audio continuously while active; publish revisions on a throttled cadence rather than chopping audio into rigid files.
- Finalize utterances using silence/VAD boundaries and preserve a short audio pre-roll.
- When speech resources are exhausted, continue recording and mark that interval for post-session transcription.
- Load a small session vocabulary of character, NPC, location, spell, and homebrew names.

### Silence and overlap

Silence suppresses decoding and recognition work, not Discord transport heartbeats. Overlap is represented as two independently timed utterances. The UI may interleave them visually, but storage must not erase or reorder the original timing relationship.

## 7. Session archive and event model

Each session is self-describing and portable. A database may index sessions, but it is not required to reconstruct one.

| **Artifact** | **Purpose** |
| --- | --- |
| **manifest.json** | Schema version, session ID, start/end wall time, monotonic anchor, guild/channel, participants, track segments, gaps, app/sidecar versions, and completion state. |
| **events.ndjson** | Append-only transcript and lifecycle events with stable IDs and revisions. |
| **audio/<user-id>/** | Per-user, per-epoch canonical audio containers and optional indexes. |
| **transcript.md** | Readable chronological export generated from the latest accepted revisions. |
| **diagnostics.ndjson** | Redacted health and failure events; never mixed into transcript output. |

### Transcript event fields

- schema, event_id, session_id, utterance_id, revision, status, and source.
- user_id and display_name snapshot.
- start_ms and end_ms as integer offsets from the session monotonic clock.
- text, optional confidence, alternatives when useful, and locale.
- audio_ref, connection epoch, recognizer implementation, and emitted timestamp.

> **REVISION POLICY:** Live partials are replaceable. Live finals remain preserved. Post-session results may supersede machine finals. Human edits always win unless explicitly reverted. Every supersession retains provenance.

## 8. Privacy, consent, and failure behavior

### Recording posture

- The bot must be visibly present in the voice channel while capture is possible.
- Starting a session requires an explicit disclosure and confirmation in the app.
- Provide a clear mechanism to post or announce the recording state in Discord.
- Make the recording state unmistakable for the operator throughout the session.
- Do not upload audio or transcripts unless a future feature is separately enabled and explained.
- Retention and deletion controls belong in the product before broader distribution.

### Failure behavior

| **Condition** | **Required response** |
| --- | --- |
| **Sidecar crash** | App stops claiming healthy capture, records the gap, attempts a bounded restart, and starts a new connection epoch. |
| **Speech failure** | Audio recording continues; affected ranges are queued for later transcription. |
| **Disk pressure** | Warn early, stop safely before corruption, and finalize all containers that can be finalized. |
| **App crash** | On next launch, detect incomplete sessions and offer recovery/finalization. |
| **Packet loss** | Preserve timing, emit gap metadata, and never silently compress the timeline. |
| **Consent withdrawn** | Stop capture promptly, finalize the session, and surface deletion/export choices. |

## 9. Delivery milestones

1. **Milestone 0 — DAVE capture spike.** Join one test channel, receive individual participant audio, map frames to users, survive one reconnect, and write playable per-user tracks. No UI polish and no transcription.
2. **Milestone 1 — Recorder core.** Create the SwiftUI shell, sidecar supervisor, session clock, archive writer, manifest, explicit start/stop flow, and recovery of incomplete sessions.
3. **Milestone 2 — Live transcription.** Add audio conversion, Apple Speech adapter, VAD boundaries, transcript revision events, vocabulary hints, and degraded post-session fallback.
4. **Milestone 3 — Usable session workflow.** Build the live transcript, participant status, session library, session detail, playback jump, export, and diagnostics surfaces.
5. **Milestone 4 — Hardening.** Test long sessions, overlap, packet loss, renames, reconnects, sleep/wake, disk pressure, speech resource exhaustion, app crash, and sidecar crash.

## 10. MVP acceptance criteria

- A 30-minute, three-participant test produces a playable track for every participant who spoke.
- Simultaneous speakers remain correctly attributed and independently timed.
- The live transcript typically updates within three seconds while healthy; latency degradation is visible rather than silent.
- Silence produces no transcript records while the voice connection remains healthy.
- A Discord reconnect creates an explicit epoch/gap and capture resumes without corrupting prior audio.
- Stopping the session results in a complete manifest, finalized audio, NDJSON events, and readable transcript.
- An interrupted session is detected and recoverable after relaunch.
- Speech failure never causes loss of canonical audio.
- Display-name changes do not split or merge stable participant identity incorrectly.
- No credential, encryption secret, or raw token appears in transcript or diagnostic files.
- The operator can tell at a glance whether recording, connection, transcription, and disk health are good or degraded.

## 11. Decisions and risks to close

| **Decision** | **MVP posture** |
| --- | --- |
| **DAVE receiver** | Highest risk · Prove before investing in the app shell beyond a minimal supervisor. |
| **Speech concurrency** | Measure simultaneous active-speaker capacity on the target Mac; define the fallback threshold. |
| **Minimum macOS** | Choose after testing SpeechAnalyzer availability and the Macs expected to run sessions. |
| **Sandboxing** | Use the least restrictive development setup needed for the spike, then design entitlements, helper packaging, notarization, and distribution intentionally. |
| **Credential storage** | Use Keychain; never store a bot token in preferences, manifests, logs, or process arguments. |
| **Long-session drift** | Measure Discord RTP time against the session monotonic clock over several hours. |
| **Consent policy** | Define the exact Discord notice and operator workflow before use outside a private test group. |
| **Product name** | SessionScribe is the working project name. Perform naming/trademark checks only before public distribution. |

## 12. Xcode starting point

| **Xcode field** | **Recommended value** |
| --- | --- |
| **Template** | macOS App |
| **Product name** | SessionScribe |
| **Interface** | SwiftUI |
| **Language** | Swift |
| **Tests** | Include unit tests and UI tests |
| **Persistence option** | Start without template-generated SwiftData/Core Data. Keep session folders and NDJSON authoritative; add an index later. |
| **Initial target** | Choose the oldest macOS version that supports the selected Apple Speech implementation after the speech spike. |
| **First app types** | SessionController, SidecarSupervisor, SessionClock, SessionArchive, ParticipantState, TranscriptEvent, and SpeechEngine protocol. |

> **FIRST ENGINEERING MOVE:** Create the SessionScribe SwiftUI shell, but keep it intentionally thin. The first real proof should be a command-line sidecar spike that records DAVE voice into valid per-user audio. The app can supervise that spike as soon as the transport works.

## Appendix A — Reference links

- Discord voice connections and DAVE: https://docs.discord.com/developers/topics/voice-connections
- discord.py VoiceClient API: https://discordpy.readthedocs.io/en/stable/api.html#voiceclient
- Apple Speech framework: https://developer.apple.com/documentation/speech/
- Apple SpeechAnalyzer: https://developer.apple.com/documentation/speech/speechanalyzer
- Apple SpeechTranscriber: https://developer.apple.com/documentation/speech/speechtranscriber
- Apple SFTranscriptionSegment: https://developer.apple.com/documentation/speech/sftranscriptionsegment
