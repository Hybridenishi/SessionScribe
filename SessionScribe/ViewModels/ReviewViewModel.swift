import Foundation
import Observation

/// One session's proposal cards: decisions, edits, evidence playback, Propose and Publish.
@MainActor
@Observable
final class ReviewViewModel {
    struct Group: Identifiable, Equatable {
        let entity: String
        let cards: [Proposal]
        var id: String { entity }
    }

    let number: Int
    private(set) var list: ProposalList?
    private(set) var errorMessage: String?
    private(set) var notice: String?
    private(set) var lastPublish: PublishResult?
    private(set) var isWorking = false
    private(set) var playingEvidence: String?

    private let connection: HubConnection
    private let player: any AudioClipPlaying

    init(number: Int, connection: HubConnection, player: any AudioClipPlaying) {
        self.number = number
        self.connection = connection
        self.player = player
    }

    var batch: ProposalBatch? { list?.batch }
    var isPublished: Bool { batch?.publishedAt != nil }

    /// Cards the DM decides on, grouped by the character / place / note they're about.
    var groups: [Group] {
        let cards = (list?.proposals ?? []).filter { $0.state != "rejected_by_checks" }
        var order: [String] = []
        var byEntity: [String: [Proposal]] = [:]
        for c in cards {
            if byEntity[c.entity] == nil { order.append(c.entity) }
            byEntity[c.entity, default: []].append(c)
        }
        return order.map { Group(entity: $0, cards: byEntity[$0] ?? []) }
    }

    /// Proposals the hub refused, with its reason. Shown, never silently dropped.
    var rejectedByChecks: [Proposal] {
        (list?.proposals ?? []).filter { $0.state == "rejected_by_checks" }
    }

    func count(_ state: String) -> Int {
        (list?.proposals ?? []).filter { $0.state == state }.count
    }

    var canPublish: Bool { !isPublished && count("accepted") > 0 && !isWorking }

    /// Reload the cards. `keepMessage` keeps a just-shown error (e.g. why Publish stopped).
    func refresh(keepMessage: Bool = false) async {
        guard let client = connection.client else { return }
        do {
            list = try await client.proposals(session: number)
            if !keepMessage { errorMessage = nil }
        } catch {
            errorMessage = connection.message(for: error)
        }
    }

    func decide(_ p: Proposal, _ action: String) async {
        await mutate { _ = try await $0.decide(proposal: p.id, action: action, after: nil) }
    }

    /// Save the DM's edit; an edit is an accept. The hub runs the same checks on it.
    func saveEdit(_ p: Proposal, text: String) async -> Bool {
        await mutate { _ = try await $0.decide(proposal: p.id, action: "accept", after: text) }
    }

    func propose() async {
        if await mutate({ try await $0.propose(session: self.number) }) {
            notice = "Proposing… cards appear here when the agent finishes."
        }
    }

    func publish(dryRun: Bool) async {
        guard let client = connection.client else { return }
        isWorking = true
        defer { isWorking = false }
        do {
            let result = try await client.publish(session: number, dryRun: dryRun)
            lastPublish = result
            notice = dryRun
                ? "Dry run: \(result.files.count) file(s) would change. Nothing was written."
                : "Published \(result.applied) change(s)" + (result.pushed ? " to the vault." : ".")
            errorMessage = nil
        } catch HubError.forbidden(let detail) {
            errorMessage = "Publishing needs write access to the vault (\(detail)). Use Dry run to preview."
        } catch HubError.http(status: 409, let detail) {
            errorMessage = detail + ". Cards that changed are marked below; review them again."
        } catch {
            errorMessage = connection.message(for: error)
        }
        await refresh(keepMessage: true)
    }

    func play(_ e: Proposal.Evidence) async {
        guard let client = connection.client else { return }
        do {
            let wav = try await client.audioClip(session: number, track: e.track,
                                                 fromMs: max(0, e.startMs - SessionDetailViewModel.paddingMs),
                                                 toMs: e.endMs + SessionDetailViewModel.paddingMs)
            try player.play(wav)
            playingEvidence = e.utteranceId
        } catch {
            errorMessage = connection.message(for: error)
        }
    }

    func stopPlayback() {
        player.stop()
        playingEvidence = nil
    }

    @discardableResult
    private func mutate(_ body: (any HubClient) async throws -> Void) async -> Bool {
        guard let client = connection.client else { return false }
        isWorking = true
        defer { isWorking = false }
        do {
            try await body(client)
            errorMessage = nil
            await refresh()
            return true
        } catch HubError.http(status: 422, let detail) {
            errorMessage = detail
            return false
        } catch {
            errorMessage = connection.message(for: error)
            return false
        }
    }

    /// Plain words for an operation, for the card header.
    static func describe(_ p: Proposal) -> String {
        switch p.op {
        case "update-section": "Update section · \(p.section ?? "")"
        case "add-to-list": "Add to list · \(p.section ?? "")"
        case "set-frontmatter": "Set \(p.key ?? "field")"
        case "create-note": "New note"
        case "open-question": "New open question"
        case "move-note": "Move note"
        default: p.op
        }
    }

    static func stateLabel(_ state: String) -> String {
        switch state {
        case "pending": "To review"
        case "accepted": "Accepted"
        case "rejected": "Rejected"
        case "deferred": "Deferred"
        case "applied": "Published"
        case "rejected_by_checks": "Rejected by checks"
        default: state.capitalized
        }
    }
}
