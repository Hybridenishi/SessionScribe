import Foundation

/// A scriptable stand-in for scribe-hub, used by previews and unit tests.
final class MockHubClient: HubClient, @unchecked Sendable {
    private let lock = NSLock()
    private var _calls: [String] = []

    var statusResult: Result<HubStatus, HubError> = .success(PreviewHub.status)
    var sessionsResult: Result<[SessionSummary], HubError> = .success(PreviewHub.sessions)
    var sessionResult: Result<SessionDetail, HubError> = .success(PreviewHub.detail)
    var allUtterances: [Utterance] = PreviewHub.utterances
    var clip = Data("RIFF".utf8)
    var campaignValue = PreviewHub.campaign
    var devicesValue = PreviewHub.devices
    var failAll: HubError?

    var calls: [String] { lock.withLock { _calls } }

    private func record(_ call: String) throws {
        lock.withLock { _calls.append(call) }
        if let failAll { throw failAll }
    }

    func status() async throws -> HubStatus {
        try record("status")
        return try statusResult.get()
    }

    func sessions() async throws -> [SessionSummary] {
        try record("sessions")
        return try sessionsResult.get()
    }

    func session(_ number: Int) async throws -> SessionDetail {
        try record("session \(number)")
        return try sessionResult.get()
    }

    func utterances(session: Int, offset: Int, limit: Int) async throws -> UtterancePage {
        try record("utterances \(session) \(offset) \(limit)")
        let end = min(allUtterances.count, offset + limit)
        let slice = offset < end ? Array(allUtterances[offset ..< end]) : []
        return UtterancePage(total: allUtterances.count, offset: offset, utterances: slice)
    }

    func audioClip(session: Int, track: Int, fromMs: Int, toMs: Int) async throws -> Data {
        try record("clip \(session) \(track) \(fromMs) \(toMs)")
        return clip
    }

    func uploadSession(number: Int, zipFile: URL) async throws -> UploadResponse {
        try record("upload \(number) \(zipFile.lastPathComponent)")
        return UploadResponse(session: number, job: 1)
    }

    func retry(session: Int) async throws {
        try record("retry \(session)")
    }

    func retranscribe(session: Int) async throws {
        try record("retranscribe \(session)")
    }

    func campaign() async throws -> CampaignConfig {
        try record("campaign")
        return campaignValue
    }

    func saveCampaign(_ campaign: CampaignConfig) async throws -> CampaignConfig {
        try record("saveCampaign")
        campaignValue = campaign
        return campaign
    }

    func devices() async throws -> [HubDevice] {
        try record("devices")
        return devicesValue
    }

    func revokeDevice(_ id: String) async throws {
        try record("revoke \(id)")
    }
}

enum PreviewHub {
    static let credentials = HubCredentials(baseURL: URL(string: "https://atomsk.example.ts.net:8443")!,
                                            token: "preview-token", deviceID: "a1b2c3d4",
                                            deviceName: "Preview Mac")

    @MainActor
    static func connection(client: MockHubClient = MockHubClient()) -> HubConnection {
        HubConnection(store: InMemoryHubCredentialStore(credentials),
                      pairer: { _, _ in PairResponse(deviceId: "a1b2c3d4", deviceName: "Preview Mac", token: "t") },
                      makeClient: { _ in client })
    }

    @MainActor
    final class Player: AudioClipPlaying {
        private(set) var played: [Data] = []
        private(set) var stops = 0
        func play(_ wav: Data) throws { played.append(wav) }
        func stop() { stops += 1 }
    }

    static let status = HubStatus(
        time: 1_790_000_000,
        sessions: .init(recent: Array(sessions.prefix(3)), blocked: 0),
        jobs: ["hub": ["done": 4], "mac": ["done": 8]],
        gpu: .init(busy: false, waiting: 0, busyForS: 0, served: 12, naotaHealthy: true),
        macWorker: .init(name: "mac-worker", lastSeen: 1_790_000_000 - 30),
        vault: .init(configured: true, pushBranches: false))

    static let sessions = [
        SessionSummary(number: 61, createdAt: 1_790_000_000, state: "transcribing", detail: nil),
        SessionSummary(number: 60, createdAt: 1_789_400_000, state: "ready", detail: nil)
    ]

    static let detail = SessionDetail(
        number: 60, createdAt: 1_789_400_000, state: "ready", detail: nil,
        manifest: .init(
            craig: .init(recordingId: "PREVIEW", startTime: "2026-09-27T00:50:00Z"),
            tracks: [
                .init(index: 1, file: "1-gm.flac", username: "gm", discordId: "1",
                      speaker: .init(label: "Gamemaster/DM", role: "dm", pc: nil), durationS: 9_000),
                .init(index: 2, file: "2-pat.flac", username: "pat", discordId: "2",
                      speaker: .init(label: "Pat/Alpha", role: "player", pc: "alpha"), durationS: 8_900)
            ],
            unmapped: [],
            stage3: .init(utterances: 3, branch: "scribe/session-060", commit: "abc1234", pushed: false)),
        jobs: [
            .init(id: 1, stage: "s1", lane: "hub", state: "done", attempts: 1, error: nil, track: nil),
            .init(id: 2, stage: "s2", lane: "mac", state: "done", attempts: 1, error: nil, track: 1),
            .init(id: 3, stage: "s2", lane: "mac", state: "done", attempts: 1, error: nil, track: 2),
            .init(id: 4, stage: "s3", lane: "hub", state: "done", attempts: 1, error: nil, track: nil)
        ])

    static let utterances = [
        Utterance(id: "t1-0", track: 1, speaker: "Gamemaster/DM", role: "dm", startMs: 0, endMs: 2_400,
                  text: "Welcome back. Last time, you reached the gate.", confidence: nil),
        Utterance(id: "t2-2600", track: 2, speaker: "Pat/Alpha", role: "player", startMs: 2_600, endMs: 4_100,
                  text: "Alpha knocks twice.", confidence: nil),
        Utterance(id: "t1-4500", track: 1, speaker: "Gamemaster/DM", role: "dm", startMs: 4_500, endMs: 6_000,
                  text: "The gate swings open.", confidence: nil)
    ]

    static let campaign = CampaignConfig(
        speakers: [
            .init(label: "Gamemaster/DM", role: "dm", pc: nil, discordId: "1", usernames: ["gm"]),
            .init(label: "Pat/Alpha", role: "player", pc: "alpha", discordId: "2", usernames: ["pat"])
        ],
        vocabularyPrompt: "A game in the world of Example. Alpha talks with the gatekeeper.")

    static let devices = [
        HubDevice(id: "a1b2c3d4", name: "Preview Mac", scope: "app", createdAt: 1_789_000_000,
                  lastSeen: 1_790_000_000, revokedAt: nil),
        HubDevice(id: "9f8e7d6c", name: "mac-worker", scope: "worker", createdAt: 1_789_000_000,
                  lastSeen: 1_790_000_000, revokedAt: nil)
    ]
}
