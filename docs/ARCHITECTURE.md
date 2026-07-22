# Architecture

SessionScribe has two independent parts today: a SwiftUI app that renders the session-recording workflow against mock data, and a C++ command-line spike that proves out receiving real Discord audio. This doc covers each in turn, then how they're expected to meet.

## SwiftUI app (`SessionScribe/`)

The app follows a straightforward MVVM shape: `Models/` for plain data types, `Services/` for protocols (with mock implementations) the view model talks to, one `ViewModels/` type per screen, and `Views/` for SwiftUI.

### Models (`SessionScribe/Models/`)

- **`Campaign`** — a named campaign (id, name, RPG system).
- **`GameSession`** — one recorded session within a campaign, with a `ProcessingState` (`planned` → `live` → `finalizing` → `completed`/`needsReview`/`failed`).
- **`Participant`** — a Discord user in the session (`id` is the Discord user ID as a string), with `isSpeaking`/`isConnected` flags the UI reads live.
- **`TranscriptSegment`** — one utterance: participant, start/end `Duration`, text, and a `partial`/`final` status. `chronologicallySorted()` orders segments by start then end.
- **`ServiceHealth`** — status (`inactive`/`healthy`/`degraded`/`failed`) for one of five pipeline stages (`discordConnection`, `daveSecurity`, `audioReception`, `transcription`, `diskRecording`), shown in the UI as a health list.
- **`RecordingLifecycleState`** — the recording state machine: `idle → connecting → ready → recording → stopping → completed`, with `failed` reachable from `connecting`/`ready`/`recording`/`stopping` and `idle` reachable again from `completed`/`failed`. `RecordingLifecycle.transition(to:)` throws `RecordingLifecycleError.invalidTransition` for any move `canTransition(to:)` disallows — this is the only place recording state changes are allowed to happen.

### Services (`SessionScribe/Services/`)

Two protocols define everything the view model needs from the outside world; both currently ship only mock implementations:

- **`SidecarClient`** — `connect() / beginRecording() / stopRecording()`, each returning a `SidecarSnapshot` (participants + health). `MockSidecarClient` fabricates a plausible snapshot transition (all health checks flip to `healthy` on `beginRecording`, back to `inactive` on `stopRecording`).
- **`TranscriptionEngine`** — `segments(for:participants:)`. `MockTranscriptionEngine` just filters a canned segment list down to the given participants.

Because the view model only depends on these protocols, swapping in a real Discord/transcription backend later means writing a new conforming type — the view model and views don't need to change.

### View model (`SessionScribe/ViewModels/LiveSessionViewModel.swift`)

`LiveSessionViewModel` is the one stateful object in the app (`@Observable`, `@MainActor`). It owns the `RecordingLifecycle`, the current `Campaign`/`GameSession`/participants/transcript/health, and drives them from the two service protocols:

- `startRecording()` walks the lifecycle from wherever it is (resetting from `completed`/`failed` back to `idle` first) through `connecting → ready → recording`, calling `sidecarClient.connect()` then `.beginRecording()` and populating participants/health/transcript from the results.
- `stopRecording()` walks `recording → stopping → completed`, calling `sidecarClient.stopRecording()`.
- Any thrown error is caught by `markFailed`, which records a `failureMessage`, forces the lifecycle to `failed` if a transition is legal, and marks the Discord/audio health entries as failed.
- `elapsedTime(at:)` computes a live duration from `recordingStartedAt` only while `state == .recording`; otherwise it returns the session's stored duration. `LiveSessionView` polls this once a second via `TimelineView(.periodic...)`.

### Views (`SessionScribe/Views/`)

- **`SessionScribeRootView`** — a `NavigationSplitView` with three sections (`Campaigns`, `Sessions`, `Review Inbox`); only `Sessions` is wired up today, the other two render `ContentUnavailableView` placeholders.
- **`LiveSessionView`** — the main recording screen: a header (title, campaign, state badge, elapsed time, start/stop buttons bound to the view model), a left column (`ParticipantListView` + `HealthListView`), and `TranscriptFeedView` on the right.
- **`ParticipantListView` / `HealthListView` / `TranscriptFeedView`** — presentational lists driven purely by the arrays the view model exposes.

`PreviewSupport/PreviewFixture.swift` supplies the canned campaign/session/participants/health/transcript data used both by SwiftUI previews and by the mock services' defaults, so previews and the mock-driven app show the same fixture out of the box.

## DAVE capture spike (`DAVECaptureSpike/`)

A standalone CMake project (not linked into the app) that proves the app can receive decrypted per-user audio from a DAVE-enabled Discord voice channel, using [D++](https://github.com/brainboxdotcc/DPP) 10.1.5.

### Flow (`src/main.cpp`)

1. Parse `--guild-id`, `--channel-id`, `--output` and read the bot token from `SESSION_SCRIBE_SPIKE_TOKEN` or stdin (never a CLI argument, never written to disk; log lines are scrubbed of the token via `redacted()`).
2. Construct a `Recorder` and call `start()`, then a `dpp::cluster` with the `i_guilds | i_guild_voice_states` intents.
3. On `on_ready`, join the target voice channel. On `on_voice_ready`, tell the recorder the voice connection is `connected()`. On `on_voice_client_speaking` / `on_voice_client_disconnect`, forward speaking state. On `on_voice_receive`, hand each packet's user ID, parsed RTP header, and decoded PCM to `recorder.record(...)`.
4. Run until `SIGINT`/`SIGTERM`, then disconnect voice, shut the bot down, and call `recorder.finalize()`.

### Recorder (`include/session_scribe_spike/recorder.hpp`, `src/recorder.cpp`)

`Recorder` is the only stateful piece and owns all file I/O, guarded by a single mutex:

- **Per-user WAV tracks** — `audio/<safe-user-id>/epoch-<n>.wav`, 48 kHz/stereo/s16le. A new epoch (and new track) starts each time the voice connection reconnects (tracked via `connected()`'s `epoch_` counter), so a reconnect never corrupts an in-progress file.
- **`sidecar-events.ndjson`** — one JSON object per line (`schema: 1`), emitted for: `hello` (capabilities/build id, once at start), `connection` (`connecting`/`connected`/`disconnected` with an `epoch` and, for disconnects, a `reason`), `participant` (first time a user is seen), `speaking` (active/inactive with a timestamp), `audio` (per-packet: user, epoch, arrival time, byte count, path, and RTP sequence/timestamp when parseable), and `gap` (emitted when an RTP sequence jump is detected for a user — `parseRtpHeader` reads the sequence/timestamp out of the raw RTP header, and `record()` compares each new sequence to the last seen one, treating a jump under `0x8000` as a genuine forward gap rather than wraparound).
- **`spike-manifest.json`** — written once at `finalize()`: one entry per `(user, epoch)` track with its path and byte count, plus a `noncanonical_reason` explaining that this WAV/PCM output is evidence only, not the eventual production archive format.

`parseRtpHeader` is a free function (not a `Recorder` method) so it's independently unit-testable — see `tests/recorder_tests.cpp`.

### Build system (`CMakeLists.txt`)

Two targets share a static library, `session_scribe_spike_core` (just `recorder.cpp`, so it has no dependency on D++ or `dpp.h`):

- `session-scribe-dave-spike` — the CLI binary, links the core library plus `PkgConfig::DPP`.
- `session-scribe-dave-spike-tests` — links only the core library, so recorder/RTP-parsing logic can be tested without a Discord connection or bot token. Registered with CTest.

All three targets build with `-Wall -Wextra -Wpedantic -Werror`.

## How the two halves connect

Nothing in the SwiftUI app currently reads from the capture spike — they're deliberately decoupled while the receiver boundary is being proven out. The intended seam is the app's `SidecarClient` protocol: a future implementation would run (or talk to) a capture process built on the same receiver logic as the spike, translating its `sidecar-events.ndjson` stream (or a successor "sidecar" protocol) into `SidecarSnapshot` updates instead of `MockSidecarClient`'s canned data.

Two gaps to resolve before that integration happens:

1. **Canonical audio format.** The spike's WAV/PCM output is explicitly marked noncanonical in its manifest. Production recording needs to preserve raw decrypted Opus frames (the versioned local protocol referenced in [`DAVECaptureSpike/README.md`](../DAVECaptureSpike/README.md)), which D++'s public PCM receive callback doesn't expose.
2. **Live event delivery.** The spike writes events to a file (`sidecar-events.ndjson`) polled after the fact; a real `SidecarClient` conformance needs those same lifecycle/audio/gap events delivered live (e.g. over a socket or pipe) so the view model can update participants/health/transcript while a session is in progress, not just after it ends.
