import Foundation
import Observation

@MainActor
@Observable
final class SessionDetailViewModel {
    struct Stage: Identifiable, Equatable {
        let id: String
        let title: String
        let status: ServiceHealth.Status
        let detail: String
    }

    let number: Int
    private(set) var detail: SessionDetail?
    private(set) var utterances: [Utterance] = []
    private(set) var errorMessage: String?
    private(set) var playingID: String?
    private(set) var isLoadingTranscript = false

    private let connection: HubConnection
    private let player: any AudioClipPlaying
    static let pageSize = 500
    /// Extra audio around a line, so the first and last words aren't clipped.
    static let paddingMs = 250

    init(number: Int, connection: HubConnection, player: any AudioClipPlaying) {
        self.number = number
        self.connection = connection
        self.player = player
    }

    func refresh() async {
        guard let client = connection.client else { return }
        do {
            let fresh = try await client.session(number)
            let becameReady = fresh.state == "ready" && detail?.state != "ready"
            detail = fresh
            errorMessage = nil
            if becameReady || (fresh.state == "ready" && utterances.isEmpty) {
                await loadTranscript()
            }
        } catch {
            errorMessage = connection.message(for: error)
        }
    }

    func loadTranscript() async {
        guard let client = connection.client else { return }
        isLoadingTranscript = true
        defer { isLoadingTranscript = false }
        var all: [Utterance] = []
        do {
            while true {
                let page = try await client.utterances(session: number, offset: all.count, limit: Self.pageSize)
                all += page.utterances
                if page.utterances.isEmpty || all.count >= page.total { break }
            }
            utterances = all
        } catch {
            errorMessage = connection.message(for: error)
        }
    }

    /// Play one line from that speaker's own track.
    func play(_ u: Utterance) async {
        guard let client = connection.client else { return }
        do {
            let from = max(0, u.startMs - Self.paddingMs)
            let wav = try await client.audioClip(session: number, track: u.track,
                                                 fromMs: from, toMs: u.endMs + Self.paddingMs)
            try player.play(wav)
            playingID = u.id
        } catch {
            errorMessage = connection.message(for: error)
        }
    }

    func stop() {
        player.stop()
        playingID = nil
    }

    func retry() async {
        await act { try await $0.retry(session: self.number) }
    }

    func retranscribe() async {
        await act { try await $0.retranscribe(session: self.number) }
        utterances = []
    }

    private func act(_ body: (any HubClient) async throws -> Void) async {
        guard let client = connection.client else { return }
        do {
            try await body(client)
            await refresh()
        } catch {
            errorMessage = connection.message(for: error)
        }
    }

    var canRetry: Bool {
        ["blocked", "failed"].contains(detail?.state ?? "")
    }

    var canRetranscribe: Bool {
        guard let d = detail else { return false }
        return d.manifest != nil && (d.manifest?.unmapped.isEmpty ?? false)
            && ["ready", "failed"].contains(d.state)
    }

    /// S1/S2/S3 as plain progress rows.
    var stages: [Stage] {
        guard let d = detail else { return [] }
        let jobs = d.jobs.filter { $0.state != "cancelled" }
        func latest(_ stage: String) -> SessionDetail.Job? { jobs.last { $0.stage == stage } }

        var out: [Stage] = []
        if let s1 = latest("s1") {
            let detailText = s1.state == "blocked"
                ? (d.detail ?? s1.error ?? "Blocked")
                : (s1.state == "done" ? "\(d.manifest?.tracks.count ?? 0) tracks unpacked" : Self.words(s1.state))
            out.append(Stage(id: "s1", title: "Unpack", status: Self.health(s1.state), detail: detailText))
        }
        let s2 = Dictionary(grouping: jobs.filter { $0.stage == "s2" }, by: { $0.track ?? -1 })
            .compactMapValues { $0.max { $0.id < $1.id } }
        if !s2.isEmpty {
            let done = s2.values.filter { $0.state == "done" }.count
            let failed = s2.values.filter { $0.state == "failed" }.count
            let status: ServiceHealth.Status = failed > 0 ? .failed : (done == s2.count ? .healthy : .degraded)
            out.append(Stage(id: "s2", title: "Transcribe", status: status,
                             detail: "\(done) of \(s2.count) tracks done" + (failed > 0 ? ", \(failed) failed" : "")))
        }
        if let s3 = latest("s3") {
            let st3 = d.manifest?.stage3
            let text = s3.state == "done"
                ? (st3?.branch.map { "Committed to \($0)" + ((st3?.pushed ?? false) ? " and pushed" : "") }
                    ?? (d.detail ?? "Done"))
                : Self.words(s3.state)
            out.append(Stage(id: "s3", title: "Stage to vault", status: Self.health(s3.state), detail: text))
        }
        return out
    }

    /// A stable color slot per track, so each player keeps one color.
    func colorSlot(for track: Int) -> Int {
        let tracks = (detail?.manifest?.tracks.map(\.index) ?? []).sorted()
        return tracks.firstIndex(of: track) ?? track
    }

    static func health(_ jobState: String) -> ServiceHealth.Status {
        switch jobState {
        case "done": .healthy
        case "failed": .failed
        case "blocked": .degraded
        default: .inactive
        }
    }

    static func words(_ jobState: String) -> String {
        switch jobState {
        case "queued": "Waiting"
        case "running": "Working…"
        case "failed": "Failed"
        case "blocked": "Needs you"
        default: jobState.capitalized
        }
    }

    static func timestamp(_ ms: Int) -> String {
        let s = ms / 1_000
        return String(format: "%d:%02d:%02d", s / 3_600, s % 3_600 / 60, s % 60)
    }
}
