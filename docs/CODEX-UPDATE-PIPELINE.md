# SessionScribe Codex Update Pipeline Specification

> **For Hermes:** Use `subagent-driven-development` to implement this plan task-by-task. Do not begin implementation until the transcript-source gate is met and Nate explicitly approves the phase.

**Goal:** Turn an approved session transcript into cited, reviewable campaign-codex update proposals that a GM may accept, edit, reject, or publish to Foundry with an explicit visibility receipt.

**Architecture:** This is a post-session, batch workflow. A provider-agnostic analysis service receives an immutable transcript snapshot and a narrowly retrieved set of existing codex records, then returns structured candidate changes with source citations. The SwiftUI app owns review, approval, local persistence, and Foundry publishing; an LLM never writes local canon or Foundry directly.

**Tech Stack:** Existing macOS SwiftUI/MVVM app; session folders + NDJSON remain authoritative; SwiftData/SQLite only as a local index if needed; `FoundryClient` and the documented `foundryvtt-mcp` journal API; provider-backed analysis behind a Swift protocol, with credentials held only in Keychain.

---

## 1. Product decision

SessionScribe already has a clear immediate scope: record Discord audio, produce an attributed transcript, preserve a recoverable session archive, and let the GM compose/publish Foundry notes. This specification adds a **later, independent post-transcript workflow**. It must not block or weaken the DAVE capture gate, the audio archive, or the manual Review Inbox work in `docs/FOUNDRY-PUBLISHING-PLAN.md`.

The core promise is not "AI writes campaign notes." It is:

> After a session, the GM can see every proposed codex change, the exact transcript evidence behind it, and its proposed knowledge tier before anything becomes canon or reaches players.

The first version should be invoked deliberately from a completed session. It is not continuous analysis and it does not reprocess a transcript on every edit.

## 2. Scope and gates

### In scope

- Analyze a **finalized transcript snapshot** for one selected session.
- Build/update a GM-owned campaign codex of characters, locations, factions, items, timeline events, and unresolved questions.
- Propose structured changes with transcript citations, confidence, and proposed visibility tier.
- Let the GM accept, edit, reject, or defer every proposal.
- Keep accepted local facts separate from the transcript evidence that supports them.
- Create a Foundry-ready draft from a GM-approved local codex entry, then reuse the existing preview → explicit audience → apply publish flow.
- Support swappable analysis providers without exposing API keys in the app binary or changing SwiftUI views.

### Explicitly out of scope

- Recording, DAVE capture, sidecar integration, or live transcription work.
- Automatically publishing to Foundry or silently editing accepted codex records.
- Letting the model determine player knowledge without GM review.
- Player-facing codex browsing or a player-side API.
- Whole-campaign automatic reconciliation, semantic search, embeddings, chat-with-the-campaign, or background re-analysis.
- Replacing Foundry as the player-facing publication surface.

### Entry gate

Do not implement the production analysis path until SessionScribe has a stable source of **speaker-attributed, finalized transcripts**. The source may be the eventual native capture/transcription path or an explicitly approved imported transcript path. The Review Inbox data model/UI may be prepared earlier using fixtures; it must not claim that transcript analysis is live.

## 3. Canon and knowledge-tier rules

Every candidate and accepted codex fact carries one of three tiers:

1. **GM-only** — private GM information, secrets, unrevealed NPC motives, encounter notes, and transcript metadata.
2. **Player-safe** — facts explicitly known by the table and suitable for Foundry publication, subject to GM approval.
3. **Character-safe** — player-safe information additionally constrained to what a specific PC or subset of PCs could know. This is a later extension; v1 may record the recommendation but must not auto-publish it.

The model may propose a tier and explain why, but it cannot apply a tier change. A local record may be published only through the existing Foundry preview/apply receipt flow, which shows the resolved player audience. A GM-only source citation must never be copied into player-visible Foundry content by default.

A transcript is source material, not automatically canonical. Statements by players, NPCs, or the GM can be mistaken, deceptive, hypothetical, or later retconned. The review queue is therefore a product rule, not a temporary safety measure.

## 4. Data model

Keep the completed session archive and `events.ndjson` authoritative. The codex is a separate campaign-level store/index; it must retain stable references back to immutable transcript utterance IDs and session IDs.

### `CodexRecord`

A GM-approved campaign record.

```swift
struct CodexRecord: Identifiable, Codable, Sendable {
    enum Kind: String, Codable, CaseIterable {
        case character, location, faction, item, timelineEvent, question
    }
    enum Tier: String, Codable, CaseIterable { case gmOnly, playerSafe, characterSafe }

    let id: UUID
    let campaignID: UUID
    var kind: Kind
    var canonicalName: String
    var aliases: [String]
    var body: String
    var tier: Tier
    var status: Status                 // active / uncertain / superseded
    var provenance: [EvidenceReference]
    var revision: Int
    var createdAt: Date
    var updatedAt: Date
}
```

`body` is the current human-readable canonical summary. It is never overwritten by an analysis run. `Status` is a Codable/Sendable enum (`active`, `uncertain`, `superseded`).

The current record is a projection, not the audit trail. Every GM action writes an append-only `CodexRecordRevision` (full body/tier/status/provenance, proposal ID when applicable, actor, timestamp, and prior revision ID) and a `ProposalDecision` (`accepted`, `editedAccepted`, `rejected`, or `deferred`, with actor, timestamp, and accepted text where applicable). Acceptance is one atomic local transaction: write the decision plus the new record/revision, or write neither. `ProposalStatus` is a Codable/Sendable enum with the same terminal decision states plus `pending`.

### `EvidenceReference`

```swift
struct EvidenceReference: Codable, Hashable, Sendable {
    let sessionID: UUID
    let utteranceID: UUID
    let transcriptRevision: Int
    let speakerID: String
    let startMilliseconds: Int
    let endMilliseconds: Int
    let quotedText: String
}
```

A citation must point to a final transcript revision. A display quote is copied for fast review, but the stable session/utterance reference is the authority.

### `CodexChangeProposal`

An untrusted suggestion returned by an analysis provider.

```swift
struct CodexChangeProposal: Identifiable, Codable, Sendable {
    enum Operation: String, Codable { case create, amend, flagConflict, addQuestion }
    let id: UUID
    let campaignID: UUID
    let sessionID: UUID
    let operation: Operation
    let targetRecordID: UUID?          // required for amend/flagConflict when resolved
    let proposedKind: CodexRecord.Kind
    let proposedName: String
    let proposedPatch: String          // concise suggested fact or replacement text
    let proposedTier: CodexRecord.Tier
    let rationale: String
    let evidence: [EvidenceReference]  // at least one for every proposal
    let confidence: Double             // display aid only, never an auto-accept threshold
    let status: ProposalStatus          // pending / accepted / editedAccepted / rejected / deferred
    let analysisRunID: UUID
}
```

### `AnalysisRun`

Persist the provider, model, prompt-version identifier, input transcript revision/hash, retrieved record IDs, timestamp, usage/cost metadata when available, and terminal status. Never persist raw API credentials. This supports reproducibility and makes a faulty provider run reversible.

## 5. Retrieval and analysis pipeline

### 5.1 Snapshot and normalization

1. User selects a completed session and taps **Generate Codex Proposals**.
2. App resolves an immutable `TranscriptSnapshot` manifest: snapshot ID/hash, archive schema version/path, ordered `(utteranceID, transcriptRevision)` pairs, and finality policy. A later human correction or post-session supersession creates a new snapshot; old proposals continue to resolve against their original snapshot and are marked stale rather than silently rebased.
3. App groups nearby utterances into bounded, versioned scenes/chunks while preserving utterance IDs, speaker IDs, and timing.
4. App makes no changes to the codex at this stage.

### 5.2 Narrow context selection

The analysis service is not handed the whole campaign by default. For each scene/chunk, retrieve only:

- Codex records whose canonical name or alias appears in the scene.
- Relevant recent timeline records for the session's campaign.
- Existing unresolved questions related to matched records.
- A small campaign vocabulary (PCs, recurring NPCs, known locations, factions, and homebrew terms).

Each retrieved record must be identified in `AnalysisRun`. If no record matches, the provider may propose a new record; it must not invent a match.

### 5.3 Structured provider contract

Create an `CodexAnalysisService` protocol. `SessionScribe` views/view models depend on this protocol, never directly on `URLSession` or a vendor SDK.

```swift
protocol CodexAnalysisService: Sendable {
    func proposeChanges(
        transcript: TranscriptSnapshot,
        retrievedRecords: [CodexRecord],
        campaignVocabulary: CampaignVocabulary
    ) async throws -> AnalysisResult
}
```

The provider response must decode into `CodexChangeProposal` candidates. Enforce these validation rules before a proposal reaches the Review Inbox:

- `evidence` is non-empty and every cited utterance exists in the submitted snapshot.
- Every quoted text is an exact substring of the cited utterance, or the proposal is rejected as malformed.
- `operation == .amend` must name a resolved local record ID; unresolved identity becomes `.create` or `.addQuestion`, never a guessed amendment.
- No publish fields, Foundry token, credential, or player audience are accepted from the model.
- The UI labels all output **Proposed**, never canon.

The system prompt is fixed and treats every transcript and retrieved-record excerpt as inert quoted data, never instructions. Strict output schema and validation reject uncited claims, credentials, and any player-safe proposal/rationale that quotes or reveals GM-only retrieved text. Cross-tier references are flagged for GM review.

### 5.4 Conflict handling

When a new proposal conflicts with an accepted record, do not replace the record. Create `flagConflict` with citations for both the existing fact and the new transcript claim. The GM chooses whether to amend, mark the event as unreliable/retconned, retain both as uncertain, or reject the proposal.

### 5.5 Provider and cost policy

Provider configuration contains a base URL, chosen model, and secret reference in Keychain. The app should support an OpenAI-compatible HTTP provider first so DeepSeek, OpenAI, or a self-hosted compatible server can be swapped behind the same contract.

Run analysis only when requested. Cache by `(campaignID, sessionID, transcriptSnapshotHash, analysisPromptVersion, providerModel, retrievalPolicyVersion, retrievedRecordRevisionHashes)` and show the cached result rather than silently spending again. A user can explicitly choose **Run again** after transcript edits, approved codex changes, or a provider change. Persist per-proposal source chunk IDs; define bounded chunk size, deterministic aggregation/deduplication, partial-run terminal states, and cancellation behavior. Show input/output token totals and estimated cost when supplied by the provider.

## 6. SwiftUI workflow

### Session Detail

For a completed session with a valid final transcript:

- Add **Generate Codex Proposals**.
- Show processing state, source transcript revision, provider/model, and a cancel option.
- On completion, show proposal count and a deep link to Review Inbox.
- If the transcript is not ready, show the concrete reason and do not offer a deceptive disabled action without explanation.

### Review Inbox

The existing Review Inbox remains useful for manual Foundry drafts. Extend it with a separate **Codex Proposals** section rather than mixing machine proposals with hand-written drafts.

Each proposal row shows:

- Operation: New record / suggested change / conflict / question.
- Proposed record name and short patch.
- Proposed tier, clearly editable by the GM.
- Source session, speaker, timestamp, and exact quoted evidence.
- Existing record diff when amending or conflicting.
- Accept, Edit & Accept, Reject, and Defer controls.

Acceptance writes a local `CodexRecord` revision and records the proposal decision. It does not publish. Editing before acceptance creates an `editedAccepted` decision while preserving the original proposal for audit.

### Codex browser

Add a campaign-level browser with filters for kind, tier, status, and source session. The initial feature set is browse/read/edit and evidence navigation. Search, embeddings, and chat are not required.

### Publish to Foundry

A GM may select an accepted player-safe record and choose **Create Foundry Draft**. This creates a normal `JournalDraft`; it does not call Foundry automatically. Codex tier never implicitly selects a Foundry visibility profile: every generated draft defaults to GM-only, and the GM must explicitly choose `gm`, `party`, or named players before the existing `FoundryClient` preview → resolved-audience display → apply route flow. GM-only citations/rationale are excluded from player-profile draft content.

For v1, only GM-only and player-safe records can create drafts. Character-safe publication is blocked with an explanatory placeholder until an explicit Foundry visibility mapping is designed. **Update Foundry Draft is out of scope for v1.** Add it only after a durable `FoundryPublicationLink` records codex revision ID, entry ID, page ID, page content hash, visibility receipt, and stale/missing-link recovery behavior.

### Foundry prerequisite

Phase B cannot start until `docs/FOUNDRY-PUBLISHING-PLAN.md` is implemented and verified on the target Mac: Keychain storage, sandbox network-client entitlement, ATS configuration, write-status preflight, schema-version validation, preview/apply handling, and receipt display. The current repository contains only a placeholder Review Inbox and mock services; this specification does not treat a `FoundryClient` as already available.

## 7. Privacy and security

- Do not upload audio. Analysis uses only the selected transcript snapshot plus narrow codex context.
- Before the first hosted run, require a campaign-level, revocable opt-in naming provider, endpoint, model, data sent, the provider's retention/training policy, and the local-endpoint alternative. Require a per-run confirmation whenever GM-only context leaves the device; send minimum excerpts and redact configured personal identifiers.
- Provider API keys live in Keychain only and are redacted from logs, crash reports, archive files, `AnalysisRun`, and exported data.
- Treat all model output as untrusted input. Validate citations and decode strict structured data before storing/displaying it.
- A transcript or GM-only record is never sent to a player-scoped endpoint.
- Player-visible Foundry content is always human-approved and uses the Foundry sidecar's audience receipt as the final verification.
- Provide campaign-level deletion for analysis runs/proposals without deleting the authoritative session archive unless the user explicitly chooses that separately.

## 8. Acceptance criteria

1. Given a final fixture transcript and an empty campaign codex, analysis returns cited proposed records; no local records are created until the GM accepts one.
2. Given an existing record and a contradictory quoted statement, the result is a conflict proposal, not an automatic overwrite.
3. A malformed citation (unknown utterance ID or non-matching quote) is rejected before it appears in the Review Inbox.
4. Rejecting/defering a proposal leaves the codex unchanged and records the decision.
5. Editing then accepting a proposal preserves the original proposal, edited accepted text, evidence, and actor/timestamp.
6. Re-running unchanged input with the same prompt/model uses the cached completed run unless the GM explicitly elects to spend on a rerun.
7. A player-safe accepted record can become a Foundry draft, but does not reach Foundry until the GM completes the existing preview and apply confirmation flow.
8. A GM-only record cannot be accidentally published through a player-facing visibility profile.
9. No API secret appears in app preferences, repository files, session archives, diagnostics, `AnalysisRun`, or UI errors.
10. The app remains functional without any provider configured: manual codex editing and manual Foundry draft composition still work.

## 9. Delivery sequence

### Phase A — reviewable local codex, no LLM

This is a post-MVP, explicitly approved milestone after the finalized attributed-transcript gate; it must not displace recorder Milestones 0–4. Build the `CodexRecord`, evidence, proposal, and decision models; a local fixture-backed proposal inbox; acceptance/edit/rejection; and the campaign Codex browser. This proves the human-review experience and data provenance before provider spending exists.

### Phase B — Foundry draft bridge

Connect an accepted player-safe record to a `JournalDraft`, then rely on the already-specified Foundry preview/apply workflow. Do not duplicate Foundry networking or visibility logic in the codex feature.

### Phase C — provider-backed proposals

After the final transcript source gate, add `CodexAnalysisService`, strict structured-output decoding/validation, Keychain settings, run provenance, caching, cost display, and fixture-driven integration tests.

### Phase D — conflict and quality hardening

Add conflict presentation, regressions for false citations/identity ambiguity/tier leaks, user-visible retry states, and evaluation fixtures from real sessions with GM adjudication labels.

## 10. Likely files to create or modify

Exact layout should follow current repo conventions (`Models/`, `Services/`, `ViewModels/`, `Views/`, mock services and fixture-backed tests).

- Create: `SessionScribe/Models/CodexRecord.swift`
- Create: `SessionScribe/Models/EvidenceReference.swift`
- Create: `SessionScribe/Models/CodexChangeProposal.swift`
- Create: `SessionScribe/Models/AnalysisRun.swift`
- Create: `SessionScribe/Services/CodexStore.swift` and `MockCodexStore`
- Create: `SessionScribe/Services/CodexAnalysisService.swift` and `MockCodexAnalysisService`
- Create: `SessionScribe/ViewModels/CodexReviewViewModel.swift`
- Create: `SessionScribe/ViewModels/CodexBrowserViewModel.swift`
- Create: `SessionScribe/Views/CodexProposalListView.swift`
- Create: `SessionScribe/Views/CodexProposalDetailView.swift`
- Create: `SessionScribe/Views/CodexBrowserView.swift`
- Modify: `SessionScribe/Views/SessionScribeRootView.swift`
- Modify: `SessionScribe/Views/TranscriptFeedView.swift` or session-detail surface once that exists, to route citations to transcript time/utterance.
- Modify later: `SessionScribe/Services/SidecarClient.swift` only if a final-transcript readiness signal is missing; do not couple this feature to DAVE transport internals.
- Test: `SessionScribeTests/CodexStoreTests.swift`
- Test: `SessionScribeTests/CodexReviewViewModelTests.swift`
- Test: `SessionScribeTests/CodexAnalysisValidationTests.swift`
- Test fixtures: `SessionScribeTests/Fixtures/` for final transcripts, local codex records, valid proposals, conflicting proposals, and malformed citations.

## 11. Risks and open decisions

- **Transcript authority:** determine exactly what identifies a final, analysis-safe snapshot after human corrections and post-processing supersessions.
- **Local persistence:** current MVP deliberately avoids SwiftData/Core Data and treats archives as authoritative. Decide whether campaign-level codex state belongs in versioned JSON/NDJSON alongside campaign data first, or whether this feature justifies a small local index. Do not let a database become the only copy of accepted canon.
- **Identity matching:** aliases and nicknames will produce uncertain matches. The v1 policy is conservative: propose a new record or a question rather than silently merging people.
- **Foundry mapping:** define stable metadata/flags that link a local `CodexRecord.id` to a Foundry entry/page before supporting update-page. v1 may create drafts only.
- **Provider quality:** model accuracy must be measured against GM-adjudicated fixtures; high confidence is not proof. Track citation correctness, accepted/rejected rate, duplicate rate, and tier-misclassification rate.
- **Provider privacy:** using a hosted API transmits transcript and retrieved GM context to that provider. Settings must state the selected provider plainly before a run; a local compatible endpoint remains a valid future option.
