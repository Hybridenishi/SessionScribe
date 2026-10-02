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

    /// One track's transcription, for the progress bars.
    struct TrackProgress: Identifiable, Equatable {
        let id: Int
        let speaker: String
        /// 0...1, or nil while the worker hasn't reported a chunk count yet.
        let fraction: Double?
        let status: ServiceHealth.Status
        let text: String
    }

    /// No progress report for this long while running means something is wrong. The worker
    /// reports every 15 s, and one chunk is at most 28 s of audio.
    static let stallAfter: TimeInterval = 120
    /// Downloading and decoding a track happens before the first report; allow longer.
    static let preparingStallAfter: TimeInterval = 600

    let number: Int
    private(set) var detail: SessionDetail?
    private(set) var utterances: [Utterance] = []
    private(set) var errorMessage: String?
    private(set) var playingID: String?
    private(set) var isLoadingTranscript = false

    private let connection: HubConnection
    private let player: any AudioClipPlaying
    private let now: @Sendable () -> Date
    static let pageSize = 500
    /// Extra audio around a line, so the first and last words aren't clipped.
    static let paddingMs = 250

    init(number: Int, connection: HubConnection, player: any AudioClipPlaying,
         now: @escaping @Sendable () -> Date = Date.init) {
        self.number = number
        self.connection = connection
        self.player = player
        self.now = now
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
        let tracks = trackProgress
        if !tracks.isEmpty {
            let done = tracks.filter { $0.fraction == 1 && $0.status == .healthy }.count
            let failed = tracks.filter { $0.status == .failed }.count
            let stalled = tracks.contains { $0.status == .degraded }
            let overall = tracks.map { $0.fraction ?? 0 }.reduce(0, +) / Double(tracks.count)
            let status: ServiceHealth.Status = failed > 0 ? .failed
                : (done == tracks.count ? .healthy : (stalled ? .degraded : .inactive))
            var text = "\(done) of \(tracks.count) tracks done"
            if done < tracks.count { text += " · \(Int((overall * 100).rounded()))% overall" }
            if failed > 0 { text += ", \(failed) failed" }
            if stalled { text += " · no recent progress" }
            out.append(Stage(id: "s2", title: "Transcribe", status: status, detail: text))
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

    /// Per-track transcription state, with "is it stuck?" answered from the time of the last report.
    var trackProgress: [TrackProgress] {
        guard let d = detail else { return [] }
        let latest = Dictionary(grouping: d.jobs.filter { $0.stage == "s2" && $0.state != "cancelled" },
                                by: { $0.track ?? -1 })
            .compactMapValues { $0.max { $0.id < $1.id } }
        let speakers = Dictionary(uniqueKeysWithValues: (d.manifest?.tracks ?? []).map {
            ($0.index, $0.speaker?.label ?? $0.username)
        })
        let t = now().timeIntervalSince1970
        return latest.keys.sorted().compactMap { track in
            guard let job = latest[track] else { return nil }
            let name = speakers[track] ?? "Track \(track)"
            switch job.state {
            case "done":
                return TrackProgress(id: track, speaker: name, fraction: 1, status: .healthy, text: "Done")
            case "failed":
                return TrackProgress(id: track, speaker: name, fraction: nil, status: .failed,
                                     text: "Failed: \(job.error ?? "unknown error")")
            case "running":
                if let p = job.progress, p.total > 0 {
                    let fraction = Double(p.done) / Double(p.total)
                    let age = t - p.at
                    if age > Self.stallAfter {
                        return TrackProgress(id: track, speaker: name, fraction: fraction, status: .degraded,
                                             text: "No progress for \(Self.duration(age)): is the Mac awake and on Tailscale?")
                    }
                    return TrackProgress(id: track, speaker: name, fraction: fraction, status: .inactive,
                                         text: "\(Int((fraction * 100).rounded()))% · \(p.done) of \(p.total) chunks · updated \(Self.duration(age)) ago")
                }
                let age = t - job.updatedAt
                if age > Self.preparingStallAfter {
                    return TrackProgress(id: track, speaker: name, fraction: nil, status: .degraded,
                                         text: "Preparing for \(Self.duration(age)) with no progress: check the Mac worker")
                }
                return TrackProgress(id: track, speaker: name, fraction: nil, status: .inactive,
                                     text: "Downloading and preparing audio…")
            default:
                return TrackProgress(id: track, speaker: name, fraction: 0, status: .inactive,
                                     text: "Waiting for the Mac worker")
            }
        }
    }

    static func duration(_ seconds: TimeInterval) -> String {
        let s = max(0, Int(seconds))
        return s < 60 ? "\(s) s" : (s < 3_600 ? "\(s / 60) min" : "\(s / 3_600) h \(s % 3_600 / 60) min")
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
