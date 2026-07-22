# SessionScribe

SessionScribe is a macOS app for recording and transcribing tabletop RPG sessions played over Discord voice. It captures per-participant audio from a Discord voice channel, transcribes it, and surfaces a live view of the session (participants, transcript, pipeline health) so a table can review and later fold accepted transcript changes into campaign notes.

The project is in early development. The SwiftUI app is a working UI shell driven by mock data, and the real-time Discord capture pipeline is being proven out separately as a disposable spike before it's promoted into the app.

## Repository layout

| Path | What it is |
| --- | --- |
| [`SessionScribe/`](SessionScribe/) | The SwiftUI macOS app: views, view models, models, and the service protocols they depend on. |
| [`SessionScribeTests/`](SessionScribeTests/) | Unit tests for the app. |
| [`SessionScribeUITests/`](SessionScribeUITests/) | UI tests for the app. |
| [`SessionScribe.xcodeproj/`](SessionScribe.xcodeproj/) | Xcode project for the app. |
| [`DAVECaptureSpike/`](DAVECaptureSpike/) | A standalone C++ command-line spike that proves out receiving decrypted per-user audio from a DAVE-enabled Discord voice channel. Not linked into the app. |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the pieces fit together and where the two halves of the repo are expected to meet.

## The SwiftUI app

Open [`SessionScribe.xcodeproj`](SessionScribe.xcodeproj) in Xcode and run the `SessionScribe` scheme.

Today the app runs entirely against mock services (`MockSidecarClient`, `MockTranscriptionEngine`) defined alongside the protocols they implement, so the UI is fully explorable without a live Discord connection or transcription backend. Start/stop recording drives a `RecordingLifecycle` state machine and populates participants, health, and transcript panes from the mocks.

## The DAVE capture spike

[`DAVECaptureSpike/`](DAVECaptureSpike/) is a disposable proof of the Discord receiver boundary: it joins a voice channel with a bot, writes each participant's decoded PCM audio to a WAV file, and logs structured lifecycle/audio events. It is deliberately kept outside the SwiftUI app.

See [DAVECaptureSpike/README.md](DAVECaptureSpike/README.md) for build, test, and live-capture-gate instructions.

## Status

- **App**: UI and state machine in place; backed by mocks. No real Discord or transcription integration yet.
- **Capture spike**: proves per-user audio reception and reconnect/gap handling against Discord's DAVE-enabled voice API. Its WAV output is explicitly noncanonical — the eventual production pipeline needs to preserve raw Opus frames, which the spike's PCM callback path doesn't provide.

Neither half of the repo currently talks to the other; connecting them is the next major step (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#how-the-two-halves-connect)).
