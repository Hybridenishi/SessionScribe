# Foundry publishing: implementation plan

**Audience:** an engineer (or Codex) picking up SessionScribe next. This plan assumes no memory of how it was scoped.

## What this plan is, and isn't

This is the plan for the **Review Inbox → Foundry publish** leg of SessionScribe: the GM composes a campaign note by hand, sees exactly who in Foundry would be able to read it, and publishes it. It turns `SessionScribeRootView`'s "Review Inbox" placeholder into a working screen backed by a real network client, against the contract published in `foundryvtt-mcp`'s [`docs/JOURNAL-API.md`](https://github.com/Hybridenishi/foundryvtt-mcp/blob/main/docs/JOURNAL-API.md) (merging via [PR #12](https://github.com/Hybridenishi/foundryvtt-mcp/pull/12) — confirm it's merged before starting; if not, read the branch directly).

It is **not** the Discord audio pipeline. `DAVECaptureSpike/LIVE-TEST-FINDINGS.md` and `DAVECaptureSpike/PATCHED-DPP.md` document that Milestone 0 (reliable DAVE audio capture) has not passed, and both files are explicit that the spike must not be integrated into the app until it does. That gate is independent of this plan — the Foundry publish path doesn't need audio, live transcription, or the sidecar spike at all. This plan's "review inbox" is fed by the GM typing or pasting notes, not by transcript extraction. Automated "identify developments requiring note changes" (from the original workflow description) is downstream of working transcription and is out of scope here.

Two things are explicitly deferred, not forgotten:
- **Wiring the DAVE spike into the app.** Blocked on the Milestone 0 gate in `LIVE-TEST-FINDINGS.md`. A candidate fix is written up in `PATCHED-DPP.md` but unconfirmed against a live Discord session — that's the next step on *that* track, and it's separate work.
- **Testing against Craig-recorded audio** as an interim transcription source while native capture is blocked. That's a transcription-pipeline concern, not a publishing concern — it would feed the same Review Inbox this plan builds, but building the transcription source is not part of this plan.

## Step 0 — settle the networking question before writing any client code

`SessionScribe.xcodeproj` currently has `ENABLE_APP_SANDBOX = YES` and no `.entitlements` file, so the app has no network-client entitlement at all today — every request will fail closed, sandboxed or not, until one is added.

Once that entitlement exists, there's a second, unverified question: the sidecar is reachable over Tailscale, whose CGNAT range is `100.64.0.0/10` (e.g. `100.100.244.3`). App Transport Security blocks cleartext `http://` by default, and it's **unconfirmed** whether `NSAllowsLocalNetworking` — or plain ATS exceptions scoped to the sidecar's hostname — actually covers a Tailscale address the way it covers `192.168.x.x`/`10.x.x.x`. Tailscale addresses aren't classic RFC 1918 LAN ranges, and `NSAllowsLocalNetworking`'s documented behavior doesn't clearly extend to CGNAT.

Do this first, before any DTOs or view models:

1. Add a minimal `SessionScribe.entitlements` file with `com.apple.security.network.client` set `true`, and set `CODE_SIGN_ENTITLEMENTS` for the app target.
2. Add an `Info.plist` (there isn't one yet — Xcode's newer project template can omit it in favor of build settings; check `GENERATE_INFOPLIST_FILE` in `project.pbxproj`) with an ATS exception for the sidecar's actual Tailscale hostname/IP, or `NSAllowsLocalNetworking` if that's the path being tested.
3. Build a throwaway debug build and issue one real `URLSession` request from it to the deployed sidecar (`GET /api/mcp/write-status` with the GM `X-API-Key` header — see `docs/JOURNAL-API.md`'s auth section) over Tailscale, on the actual Mac that will run SessionScribe.
4. Confirm the response arrives. If it doesn't, the fix is almost certainly a more specific ATS exception dictionary keyed to the sidecar's hostname (`NSExceptionDomains`) rather than the blanket local-networking flag — try that next.

This gates everything below it. Don't write `FoundryClient` against an assumption about ATS that hasn't been checked against a real request.

## Step 1 — credential storage

The client authenticates with the sidecar's GM `API_KEY` (see `docs/JOURNAL-API.md`'s auth section — this key currently grants full sidecar access, not just journal routes; that's a documented open question in the doc, not something to work around here). `docs/SessionScribe-MVP.md` already states the rule for the (still-hypothetical) Discord bot token — apply the same rule here: **Keychain only, never `UserDefaults`, never in a plist, never logged.**

Add a small `CredentialStore` (or similar) service:
- `save(apiKey: String, sidecarBaseURL: URL) throws`
- `load() throws -> (apiKey: String, sidecarBaseURL: URL)?`
- Use `kSecClassGenericPassword` via the Keychain Services API (or `Security` framework directly — no third-party dependency needed for one credential).

A minimal Settings-style entry point is needed to *set* these values (the MVP doc's `Settings` screen already reserves "Bot credentials" and could reasonably also hold the sidecar URL/key — a single text field for URL and a secure field for the key is enough for this plan; it doesn't need to be the full Settings screen from the MVP doc).

## Step 2 — `FoundryClient`

Follow the existing `SidecarClient` pattern in `SessionScribe/Services/SidecarClient.swift`: a protocol plus a mock conformance, so the view model never depends on `URLSession` directly and the mock can back SwiftUI previews and tests. Match `docs/JOURNAL-API.md` exactly — every route, method, and status code documented there is normative; if what you observe from a live call disagrees with the doc, that's a bug to report against `foundryvtt-mcp`, not something to silently work around in the client.

```swift
protocol FoundryClient: Sendable {
    func writeStatus() async throws -> FoundryWriteStatus
    func players() async throws -> [FoundryPlayer]
    func previewJournalWrite(_ draft: JournalDraft) async throws -> JournalWritePreview
    func applyJournalWrite(_ preview: JournalWritePreview) async throws -> JournalWriteResult
}
```

(Exact shape is a judgment call — the point is one route per method, `Codable` DTOs decoded from the documented JSON shapes, and no leakage of `URLRequest`/HTTP details into callers.)

Routes to cover, all documented in `docs/JOURNAL-API.md`:
- `GET /api/mcp/write-status` — call before showing the compose UI at all. If `writeEnabled: false` or `bridge.available: false`, show that reason instead of a compose form ("Ask the GM to open Foundry" maps to `bridge.available: false`; a disabled-writes case maps to `writeEnabled: false`). This is the preflight the route was built for — use it as a gate, not just a status readout.
- `GET /api/mcp/players` — populate the visibility picker (who can this note be shown to).
- `POST /api/mcp/journal/write/preview` — call when the GM finishes composing and taps "Review." Show the resolved audience (`visibility.visibleTo` in the response) verbatim — this is the whole point of the preview step from the target architecture ("see the exact resolved audience").
- `POST /api/mcp/journal/write` — call when the GM confirms, passing back the `confirmationToken` from the preview response unchanged.

**DTO test fixtures:** copy `docs/examples/write-status.json`, `players.json`, `write-preview.json`, and `write-apply-create-entry.json` from `foundryvtt-mcp` into a `SessionScribeTests/Fixtures/` directory (or reference them via a build phase if the two repos end up sharing a workspace — plain copies are simpler to start with). Write a decode test per DTO against its real fixture. If `foundryvtt-mcp` bumps `schemaVersion` on any of these routes later, that's the signal to re-pull the fixtures and re-check the DTOs.

## Step 3 — data model

Add a `JournalDraft` model for what the GM is composing, separate from `foundryvtt-mcp`'s wire format — this is the app's editing state, not a network DTO:

```swift
struct JournalDraft: Identifiable, Equatable, Sendable {
    let id: UUID
    var name: String
    var pages: [DraftPage]           // name + HTML/plain content per page
    var visibilityProfile: VisibilityProfile   // gmOnly / specific players / all players — mirrors docs/JOURNAL-API.md's profile enum
    var selectedPlayers: [FoundryPlayer.ID]
}
```

Remember the API's **one-visibility-per-call** constraint (documented in `docs/JOURNAL-API.md`'s "five behaviors" section): a draft with a GM-only secret and a player-visible overview is two separate drafts/publish cycles in this model, not one. Don't build a UI that implies mixed visibility within a single publish — it can't be expressed by the API and the UI shouldn't promise something the write can't do.

## Step 4 — Review Inbox UI

Replace the `PlaceholderWorkspaceView` for `.reviewInbox` in `SessionScribeRootView.swift` with a real screen. Reasonable shape, following the existing `LiveSessionView`/`LiveSessionViewModel` pattern (one `@Observable @MainActor` view model owning state, views are presentational):

1. **List of drafts** (in-memory to start — no persistence requirement in this plan; `foundryvtt-mcp`'s journal *is* the durable store once published).
2. **Compose form** — title, page content (plain text is fine for a first pass; HTML enrichers like `@UUID[...]` from `docs/JOURNAL-API.md` are a nice-to-have, not required), visibility profile picker backed by `GET /api/mcp/players`.
3. **Preview step** — calls `previewJournalWrite`, renders the resolved `visibleTo` list plainly ("This will be visible to: ..."), holds the returned `confirmationToken` and its `expiresAt`.
4. **Publish step** — calls `applyJournalWrite` with the held token. On success, show the created `entryUuid` (useful for the GM to jump to it in Foundry). On `409` (documented as: token expired, single-use already spent, or the resolved audience changed between preview and apply), the client must not retry the same token — surface a clear "conditions changed, review again" message and go back to the preview step. The token TTL is 2 minutes (documented) — a compose session that takes longer than that just needs to re-preview; don't try to work around the TTL.
5. **Errors** — map the documented status codes (400 validation / ambiguous player name, 401 auth, 404 not found, 409 stale token, 503 Foundry disconnected, 504 bridge timeout) to distinct, specific messages. The error envelope is uniformly `{"error": "..."}` per the doc — surface that string directly for anything not worth a bespoke message.

## Step 5 — tests

- `FoundryClient` DTO decode tests against the copied fixtures (Step 2).
- A mock-backed `ReviewInboxViewModel` test suite mirroring how `SessionScribeTests` presumably already tests `LiveSessionViewModel` (check that file for the existing pattern before inventing a new one) — cover: preflight gate blocks compose when `writeEnabled: false`, preview→publish happy path, 409 sends the flow back to preview, one-visibility-per-call is enforced by the model shape (not just by convention).

## Acceptance criteria

- A GM can open SessionScribe, see the sidecar's write-status reflected honestly (including the "no GM Foundry tab open" case), compose a note, see the exact resolved player audience before publishing, and find the resulting entry in Foundry with the right ownership.
- No `API_KEY` or other credential appears in a log line, crash report, or committed file.
- Every network call's request/response shape traces back to a specific section of `docs/JOURNAL-API.md` — nothing invented or guessed.
- The build's entitlements and ATS configuration are committed (not just present in a working Xcode session state), so a fresh clone builds and connects without manual reconfiguration beyond entering the API key.

## Explicitly out of scope for this plan

- Any DAVE/Discord audio work (`DAVECaptureSpike/`) — separate track, gated on Milestone 0.
- Automated transcript-to-note-update suggestions — needs working transcription first, which needs the above.
- The Campaigns/Sessions library screens beyond what already exists.
- Resolving the credential-scope question `docs/JOURNAL-API.md` raises (full `API_KEY` vs. a narrower journal-only credential) — build against `API_KEY` as documented; that's a `foundryvtt-mcp`-side decision to revisit later, not blocking here.
