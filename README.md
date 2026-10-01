# SessionScribe

SessionScribe is the Mac app for the **Azora Hub** ([`docs/REBUILD-SPEC.md`](docs/REBUILD-SPEC.md), [Addendum 1](docs/REBUILD-SPEC-ADDENDUM-1.md)). The hub (`hub/`, a service on atomsk) turns Craig recordings of Discord sessions into transcripts and, later, proposed vault changes; this app is where the DM pairs with it, watches the pipeline, and reads and listens to sessions.

Milestone H1 is built: the hub service, the Mac transcription worker, and the app's Settings, Dashboard and Sessions screens.

## Repository layout

| Path | What it is |
| --- | --- |
| [`SessionScribe/`](SessionScribe/) | The SwiftUI macOS app, a client of the hub's API. |
| [`hub/`](hub/) | The `scribe-hub` service (Python) and the Mac transcription worker. See [hub/README.md](hub/README.md). |
| [`SessionScribeTests/`](SessionScribeTests/) | Unit tests for the app. |
| [`SessionScribeUITests/`](SessionScribeUITests/) | UI tests for the app. |
| [`SessionScribe.xcodeproj/`](SessionScribe.xcodeproj/) | Xcode project for the app. |
| [`DAVECaptureSpike/`](DAVECaptureSpike/) | Parked. A standalone C++ spike for receiving per-user audio from a DAVE-enabled Discord voice channel; Craig replaced it. Kept for reference. |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the app is put together. [docs/FOUNDRY-PUBLISHING-PLAN.md](docs/FOUNDRY-PUBLISHING-PLAN.md) keeps the Foundry publishing rules; that client moves into the hub (H4).

## The SwiftUI app

Open [`SessionScribe.xcodeproj`](SessionScribe.xcodeproj) in Xcode and run the `SessionScribe` scheme. To use it, pair it with a hub in Settings: on atomsk, `docker exec -it scribe-hub scribe-hub pair --device "<name>"` prints a one-time code. Previews and unit tests run against `MockHubClient` (`SessionScribe/PreviewSupport/PreviewHub.swift`) and need no hub.

## The DAVE capture spike

[`DAVECaptureSpike/`](DAVECaptureSpike/) is a disposable proof of the Discord receiver boundary: it joins a voice channel with a bot, writes each participant's decoded PCM audio to a WAV file, and logs structured lifecycle/audio events. It is deliberately kept outside the SwiftUI app.

See [DAVECaptureSpike/README.md](DAVECaptureSpike/README.md) for build, test, and live-capture-gate instructions.

## Status

- **Hub + app (H1)**: built and tested; not yet deployed on atomsk. Next is H2, structured proposals and the Review screen.
- **Capture spike**: parked; see its README for what it proved.
