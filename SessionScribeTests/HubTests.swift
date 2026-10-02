import Foundation
import SwiftUI
import Testing
@testable import SessionScribe

private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try LiveHubClient.decoder.decode(type, from: Data(json.utf8))
}

/// Every fixture is a real hub response (hub/tools/make_app_fixtures.py), so these fail if the
/// hub and the app disagree about the API.
struct HubContractTests {
    @Test func decodesEveryRealHubResponse() throws {
        _ = try decode(PairResponse.self, HubFixtures.pair)
        let status = try decode(HubStatus.self, HubFixtures.status)
        #expect(status.gpu.naotaHealthy)
        #expect(status.macWorker?.name == "mac-worker")
        #expect(try decode(SessionList.self, HubFixtures.sessions).sessions.map(\.number) == [61, 60])
        let ready = try decode(SessionDetail.self, HubFixtures.sessionReady)
        #expect(ready.state == "ready")
        #expect(ready.manifest?.stage3?.branch == "scribe/session-060")
        #expect(ready.manifest?.tracks.map { $0.speaker?.label } == ["Gamemaster/DM", "Pat/Alpha"])
        let blocked = try decode(SessionDetail.self, HubFixtures.sessionBlocked)
        #expect(blocked.manifest?.unmapped.first?.username == "stranger")
        let page = try decode(UtterancePage.self, HubFixtures.utterances)
        #expect(page.utterances.map(\.id) == ["t1-1000", "t2-2000"])
        _ = try decode(CampaignConfig.self, HubFixtures.campaign)
        #expect(try decode(DeviceList.self, HubFixtures.devices).devices.map(\.scope) == ["app", "worker"])
        #expect(try decode(UploadResponse.self, HubFixtures.upload).session == 60)
    }

    @Test func campaignRoundTripsThroughTheWireFormat() throws {
        let c = try decode(CampaignConfig.self, HubFixtures.campaign)
        let data = try LiveHubClient.encoder.encode(c)
        let object = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(object["vocabulary_prompt"] as? String == c.vocabularyPrompt)
        #expect(try LiveHubClient.decoder.decode(CampaignConfig.self, from: data) == c)
    }

    @Test(arguments: [
        ("https://atomsk.tailnet.ts.net:8443", true),
        ("http://localhost:8780", true),
        ("http://192.168.4.50:8780", false),
        ("atomsk", false),
        ("ftp://atomsk", false)
    ])
    func hubAddressMustBeHTTPS(address: String, ok: Bool) {
        #expect((HubURL.validated(address) != nil) == ok)
    }

    @Test func multipartBodyWrapsTheZipBytesExactly() throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let zip = dir.appending(path: "craig.zip")
        let payload = Data((0 ..< 10_000).map { UInt8($0 % 251) })
        try payload.write(to: zip)
        let body = try MultipartFile.write(fileField: "file", fileURL: zip, contentType: "application/zip",
                                           boundary: "B")
        let data = try Data(contentsOf: body)
        let head = Data("--B\r\nContent-Disposition: form-data; name=\"file\"; filename=\"craig.zip\"\r\nContent-Type: application/zip\r\n\r\n".utf8)
        #expect(data == head + payload + Data("\r\n--B--\r\n".utf8))
    }
}

@MainActor
struct HubConnectionTests {
    @Test func pairingStoresCredentialsAndConnects() async throws {
        let store = InMemoryHubCredentialStore()
        let connection = HubConnection(
            store: store,
            pairer: { url, code in
                #expect(url.absoluteString == "https://hub.test:8443")
                #expect(code == "ABCD-EFGH")
                return PairResponse(deviceId: "d1", deviceName: "Mac", token: "secret")
            },
            makeClient: { _ in MockHubClient() })
        try await connection.pair(hubAddress: " https://hub.test:8443 ", code: " ABCD-EFGH ")
        #expect(connection.isPaired)
        #expect(try store.load()?.token == "secret")
    }

    @Test func plainHTTPToAnotherMachineIsRefused() async {
        let connection = HubConnection(store: InMemoryHubCredentialStore(),
                                       pairer: { _, _ in Issue.record("should not pair"); throw HubError.invalidResponse },
                                       makeClient: { _ in MockHubClient() })
        await #expect(throws: HubError.invalidHubURL) {
            try await connection.pair(hubAddress: "http://192.168.4.50:8780", code: "X")
        }
    }

    @Test func aRefusedTokenAsksToPairAgain() async {
        let mock = MockHubClient()
        mock.failAll = .unauthorized
        let connection = PreviewHub.connection(client: mock)
        let dashboard = DashboardViewModel(connection: connection)
        await dashboard.refresh()
        #expect(connection.state == .needsRepair(PreviewHub.credentials))
        #expect(connection.client == nil)
        #expect(dashboard.errorMessage?.contains("Pair it again") == true)
    }

    @Test func forgetRemovesTheToken() throws {
        let store = InMemoryHubCredentialStore(PreviewHub.credentials)
        let connection = HubConnection(store: store, makeClient: { _ in MockHubClient() })
        try connection.forget()
        #expect(connection.state == .unpaired)
        #expect(try store.load() == nil)
    }
}

@MainActor
struct DashboardTests {
    @Test func healthRowsReflectTheHub() async {
        let mock = MockHubClient()
        mock.statusResult = .success(HubStatus(
            time: 0, sessions: .init(recent: [], blocked: 2),
            jobs: ["mac": ["queued": 3], "hub": ["failed": 1]],
            gpu: .init(busy: true, waiting: 4, busyForS: 12, served: 9, naotaHealthy: true),
            macWorker: .init(name: "mac-worker", lastSeen: 1_000),
            vault: .init(configured: true, pushBranches: false)))
        let vm = DashboardViewModel(connection: PreviewHub.connection(client: mock),
                                    now: { Date(timeIntervalSince1970: 1_000 + 3_600) })
        await vm.refresh()
        let rows = Dictionary(uniqueKeysWithValues: vm.health.map { ($0.name, $0) })
        #expect(vm.waitingForReview == 2)
        #expect(rows["naota GPU"]?.status == .degraded)          // more than 2 waiting
        #expect(rows["Mac worker"]?.status == .degraded)         // last seen an hour ago
        #expect(rows["Mac worker"]?.detail.contains("1 h ago") == true)
        #expect(rows["Pipeline"]?.detail == "0 running, 3 waiting for the Mac, 1 failed")
        #expect(rows["Foundry"]?.status == .inactive)
    }

    @Test func naotaDownIsAFailure() async {
        let mock = MockHubClient()
        var s = PreviewHub.status
        s = HubStatus(time: s.time, sessions: s.sessions, jobs: s.jobs,
                      gpu: .init(busy: false, waiting: 0, busyForS: 0, served: 0, naotaHealthy: false),
                      macWorker: nil, vault: .init(configured: false, pushBranches: false))
        mock.statusResult = .success(s)
        let vm = DashboardViewModel(connection: PreviewHub.connection(client: mock))
        await vm.refresh()
        let rows = Dictionary(uniqueKeysWithValues: vm.health.map { ($0.name, $0) })
        #expect(rows["naota GPU"]?.status == .failed)
        #expect(rows["Mac worker"]?.status == .inactive)
        #expect(rows["Vault"]?.status == .degraded)
    }
}

@MainActor
struct SessionTests {
    @Test func uploadChecksTheFileAndNumber() async {
        let mock = MockHubClient()
        let vm = SessionsViewModel(connection: PreviewHub.connection(client: mock))
        #expect(await vm.upload(zip: URL(fileURLWithPath: "/tmp/notes.txt"), number: 5) == false)
        #expect(await vm.upload(zip: URL(fileURLWithPath: "/tmp/craig.zip"), number: 0) == false)
        #expect(await vm.upload(zip: URL(fileURLWithPath: "/tmp/craig.zip"), number: 62))
        #expect(mock.calls.contains("upload 62 craig.zip"))
    }

    @Test func suggestsTheNextSessionNumber() async {
        let vm = SessionsViewModel(connection: PreviewHub.connection())
        await vm.refresh()
        #expect(vm.suggestedNumber == 62)
    }

    @Test func transcriptLoadsEveryPage() async {
        let mock = MockHubClient()
        mock.allUtterances = (0 ..< 1_203).map {
            Utterance(id: "t1-\($0)", track: 1, speaker: "S", role: "player", startMs: $0, endMs: $0 + 1,
                      text: "x", confidence: nil)
        }
        let vm = SessionDetailViewModel(number: 60, connection: PreviewHub.connection(client: mock),
                                        player: PreviewHub.Player())
        await vm.refresh()
        #expect(vm.utterances.count == 1_203)
        #expect(mock.calls.filter { $0.hasPrefix("utterances") }.count == 3)
    }

    @Test func clickingALinePlaysThatSpeakersTrack() async {
        let mock = MockHubClient()
        let player = PreviewHub.Player()
        let vm = SessionDetailViewModel(number: 60, connection: PreviewHub.connection(client: mock), player: player)
        await vm.refresh()
        await vm.play(PreviewHub.utterances[1])                  // track 2, 2600–4100 ms
        #expect(mock.calls.contains("clip 60 2 2350 4350"))
        #expect(player.played.count == 1)
        #expect(vm.playingID == "t2-2600")
        await vm.play(PreviewHub.utterances[0])                  // starts at 0: never negative
        #expect(mock.calls.contains("clip 60 1 0 2650"))
        vm.stop()
        #expect(vm.playingID == nil)
    }

    @Test func stagesReadAsPlainProgress() async {
        let vm = SessionDetailViewModel(number: 60, connection: PreviewHub.connection(), player: PreviewHub.Player())
        await vm.refresh()
        #expect(vm.stages.map(\.title) == ["Unpack", "Transcribe", "Stage to vault"])
        #expect(vm.stages[1].detail == "2 of 2 tracks done")
        #expect(vm.stages[2].detail == "Committed to scribe/session-060")
        #expect(vm.canRetranscribe && !vm.canRetry)
        #expect(vm.colorSlot(for: 2) == 1)
    }

    @Test func blockedSessionShowsWhyAndOffersRetry() async throws {
        let mock = MockHubClient()
        mock.sessionResult = .success(try decode(SessionDetail.self, HubFixtures.sessionBlocked))
        let vm = SessionDetailViewModel(number: 61, connection: PreviewHub.connection(client: mock), player: PreviewHub.Player())
        await vm.refresh()
        #expect(vm.canRetry)
        #expect(vm.stages.first?.detail.contains("stranger") == true)
        await vm.retry()
        #expect(mock.calls.contains("retry 61"))
    }
}

@MainActor
struct SettingsTests {
    @Test func speakerMapEditsRoundTrip() async {
        let mock = MockHubClient()
        let vm = SettingsViewModel(connection: PreviewHub.connection(client: mock))
        await vm.load()
        #expect(vm.speakers.map(\.label) == ["Gamemaster/DM", "Pat/Alpha"])
        vm.addSpeaker()
        vm.speakers[2].label = " Sam/Gamma "
        vm.speakers[2].usernames = "sam, , sam_old "
        vm.speakers[2].discordID = "3"
        vm.removeSpeakers(at: IndexSet(integer: 0))
        await vm.saveCampaign()
        #expect(mock.campaignValue.speakers.map(\.label) == ["Pat/Alpha", "Sam/Gamma"])
        #expect(mock.campaignValue.speakers[1].usernames == ["sam", "sam_old"])
        #expect(mock.campaignValue.speakers[1].pc == nil)
        #expect(vm.campaignMessage == "Saved.")
    }
}

@MainActor
struct TranscriptionProgressTests {
    private static let t0: Double = 1_790_000_000

    private func job(_ id: Int, track: Int, state: String, updated: Double = t0,
                     progress: SessionDetail.Job.Progress? = nil, error: String? = nil) -> SessionDetail.Job {
        .init(id: id, stage: "s2", lane: "mac", state: state, attempts: 1, error: error,
              updatedAt: updated, track: track, progress: progress)
    }

    private func viewModel(jobs: [SessionDetail.Job], nowOffset: Double) async -> SessionDetailViewModel {
        let base = PreviewHub.detail
        let tracks = (1 ... 5).map {
            SessionDetail.Manifest.Track(index: $0, file: "\($0).flac", username: "u\($0)", discordId: nil,
                                         speaker: .init(label: "P\($0)", role: "player", pc: nil), durationS: nil)
        }
        let detail = SessionDetail(number: 990, createdAt: Self.t0, state: "transcribing", detail: nil,
                                   manifest: .init(craig: base.manifest!.craig, tracks: tracks, unmapped: [], stage3: nil),
                                   jobs: jobs)
        let mock = MockHubClient()
        mock.sessionResult = .success(detail)
        let fixedNow = Date(timeIntervalSince1970: Self.t0 + nowOffset)
        let vm = SessionDetailViewModel(number: 990, connection: PreviewHub.connection(client: mock),
                                        player: PreviewHub.Player(), now: { fixedNow })
        await vm.refresh()
        return vm
    }

    @Test func everyStateReadsPlainly() async {
        let vm = await viewModel(jobs: [
            job(1, track: 1, state: "done"),
            job(2, track: 2, state: "running", progress: .init(done: 30, total: 120, at: Self.t0 + 50)),
            job(3, track: 3, state: "running", progress: .init(done: 10, total: 100, at: Self.t0)),
            job(4, track: 4, state: "queued"),
            job(5, track: 5, state: "failed", error: "mlx crashed")
        ], nowOffset: 60)
        let t = Dictionary(uniqueKeysWithValues: vm.trackProgress.map { ($0.id, $0) })
        #expect(t[1]?.fraction == 1 && t[1]?.text == "Done")
        #expect(t[2]?.fraction == 0.25 && t[2]?.status == .inactive)
        #expect(t[2]?.text == "25% · 30 of 120 chunks · updated 10 s ago")
        #expect(t[3]?.status == .inactive)                       // 60 s old: still fine
        #expect(t[4]?.text == "Waiting for the Mac worker")
        #expect(t[5]?.status == .failed && t[5]?.text.contains("mlx crashed") == true)
        #expect(t[2]?.speaker == "P2")
        #expect(t[2]?.isActive == true && t[3]?.isActive == true)        // both moving
        #expect([1, 4, 5].allSatisfy { t[$0]?.isActive == false })        // done, waiting, failed
    }

    @Test func noReportForTwoMinutesIsFlaggedAsStuck() async {
        let vm = await viewModel(jobs: [
            job(1, track: 1, state: "running", progress: .init(done: 10, total: 100, at: Self.t0))
        ], nowOffset: 300)
        let track = vm.trackProgress[0]
        #expect(track.status == .degraded)
        #expect(!track.isActive)                                       // a stalled track doesn't pulse
        #expect(track.text == "No progress for 5 min: is the Mac awake and on Tailscale?")
        #expect(vm.stages.first { $0.id == "s2" }?.detail.contains("no recent progress") == true)
        #expect(vm.stages.first { $0.id == "s2" }?.status == .degraded)
    }

    @Test func preparingGetsLongerBeforeItCountsAsStuck() async {
        let fresh = await viewModel(jobs: [job(1, track: 1, state: "running", updated: Self.t0)], nowOffset: 300)
        #expect(fresh.trackProgress[0].text == "Downloading and preparing audio…")
        #expect(fresh.trackProgress[0].fraction == nil)
        #expect(fresh.trackProgress[0].isActive)
        let stuck = await viewModel(jobs: [job(1, track: 1, state: "running", updated: Self.t0)], nowOffset: 900)
        #expect(stuck.trackProgress[0].status == .degraded)
    }

    @Test func overallPercentAveragesTheTracks() async {
        let vm = await viewModel(jobs: [
            job(1, track: 1, state: "done"),
            job(2, track: 2, state: "running", progress: .init(done: 50, total: 100, at: Self.t0)),
            job(3, track: 3, state: "queued")
        ], nowOffset: 5)
        #expect(vm.stages.first { $0.id == "s2" }?.detail == "1 of 3 tracks done · 50% overall")
    }

    @Test func aReRunUsesTheNewestJobPerTrack() async {
        let vm = await viewModel(jobs: [
            job(1, track: 1, state: "done"),
            job(7, track: 1, state: "queued")
        ], nowOffset: 5)
        #expect(vm.trackProgress.map(\.text) == ["Waiting for the Mac worker"])
    }

    @Test func progressDecodesFromARealHubResponse() throws {
        let d = try LiveHubClient.decoder.decode(SessionDetail.self, from: Data(HubFixtures.sessionReady.utf8))
        #expect(d.jobs.contains { $0.progress?.done == 1 && $0.progress?.total == 1 })
    }
}

struct TextDiffTests {
    private func join(_ s: [TextDiff.Segment]) -> String { s.map(\.text).joined() }

    @Test func eachSideRebuildsItsTextExactly() {
        let before = "Tall.\n\n- Trusts Angelica.\n- Wary of Talos."
        let after = "Tall, with a scar.\n\n- Trusts Angelica.\n- Owes Fang a favor."
        let d = TextDiff.diff(before, after)
        #expect(join(d.before) == before && join(d.after) == after)
    }

    @Test func marksOnlyTheChangedWords() {
        let d = TextDiff.diff("The gate is shut.", "The gate is open.")
        #expect(d.before.filter { $0.kind == .removed }.map(\.text) == ["shut"])
        #expect(d.after.filter { $0.kind == .added }.map(\.text) == ["open"])
        #expect(!d.after.contains { $0.kind == .removed } && !d.before.contains { $0.kind == .added })
    }

    @Test func punctuationDoesNotHideAKeptWord() {
        let d = TextDiff.diff("Tall.", "Tall, with a scar.")
        #expect(d.before == [.init(text: "Tall.", kind: .same)])          // nothing was removed
        #expect(d.after == [.init(text: "Tall", kind: .same), .init(text: ", with a scar", kind: .added),
                            .init(text: ".", kind: .same)])
    }

    @Test func markdownIsRenderedAndHighlightsLandOnTheRenderedText() {
        let r = TextDiff.rendered("Tall.", "Tall, with a **new** scar.")
        #expect(String(r.after.characters) == "Tall, with a new scar.")     // ** markers rendered away
        let bold = r.after.runs.first { String(r.after[$0.range].characters) == "new" }
        #expect(bold?.inlinePresentationIntent == .stronglyEmphasized)
        let added = r.after.runs.filter { $0.backgroundColor != nil }.map { String(r.after[$0.range].characters) }
        #expect(added.joined() == ", with a new scar")
        #expect(String(r.before.characters) == "Tall.")
    }

    @Test func emptySidesAndHugeTexts() {
        #expect(TextDiff.diff("", "New text").after == [.init(text: "New text", kind: .added)])
        let huge = String(repeating: "word ", count: 2_000)
        let d = TextDiff.diff(huge, huge + "more")
        #expect(d.after.count == 1 && d.after[0].kind == .added)           // fallback, still exact
        #expect(join(d.after) == huge + "more")
    }
}

@MainActor
struct ReviewTests {
    private func cards() throws -> ProposalList {
        try LiveHubClient.decoder.decode(ProposalList.self, from: Data(HubFixtures.proposals.utf8))
    }

    private func setUp(_ mock: MockHubClient = MockHubClient()) async throws -> (ReviewViewModel, MockHubClient, PreviewHub.Player) {
        mock.proposalsValue = try cards()
        let player = PreviewHub.Player()
        let vm = ReviewViewModel(number: 60, connection: PreviewHub.connection(client: mock), player: player)
        await vm.refresh()
        return (vm, mock, player)
    }

    @Test func realHubResponsesDecode() throws {
        let list = try cards()
        #expect(list.batch?.violations == [] && list.proposals.count == 2)
        let good = try #require(list.proposals.first { $0.state == "pending" })
        #expect(good.before == "Tall." && good.effectiveAfter == "Tall, with a **new** scar.")
        #expect(good.evidence.first?.speaker == "Pat/Alpha")
        let decided = try LiveHubClient.decoder.decode(Proposal.self, from: Data(HubFixtures.decided.utf8))
        #expect(decided.state == "accepted")
        let dry = try LiveHubClient.decoder.decode(PublishResult.self, from: Data(HubFixtures.publishDryRun.utf8))
        #expect(dry.dryRun && dry.files.contains("Characters/PCs/Pat-Alpha.md"))
    }

    @Test func checksFailuresAreShownApartFromTheCards() async throws {
        let (vm, _, _) = try await setUp()
        #expect(vm.groups.map(\.entity) == ["Pat Alpha"])
        #expect(vm.groups[0].cards.count == 1)
        #expect(vm.rejectedByChecks.count == 1)
        #expect(vm.rejectedByChecks[0].checkError?.contains("no section") == true)
    }

    @Test func decisionsAndEditsGoToTheHub() async throws {
        let (vm, mock, _) = try await setUp()
        let card = vm.groups[0].cards[0]
        await vm.decide(card, "reject")
        #expect(await vm.saveEdit(card, text: "Tall."))
        #expect(mock.calls.contains("decide \(card.id) reject"))
        #expect(mock.calls.contains("decide \(card.id) accept after=Tall."))
    }

    @Test func aRefusedEditKeepsTheSheetOpenWithTheReason() async throws {
        let mock = MockHubClient()
        mock.decideError = .http(status: 422, detail: "edit rejected: missing after")
        let (vm, _, _) = try await setUp(mock)
        #expect(await vm.saveEdit(vm.groups[0].cards[0], text: "") == false)
        #expect(vm.errorMessage == "edit rejected: missing after")
    }

    @Test func publishOnlyWithSomethingAccepted() async throws {
        let (vm, mock, _) = try await setUp()
        #expect(!vm.canPublish)                                  // nothing accepted yet
        var list = try cards()
        list = ProposalList(batch: list.batch, proposals: [try LiveHubClient.decoder.decode(
            Proposal.self, from: Data(HubFixtures.decided.utf8))])
        mock.proposalsValue = list
        await vm.refresh()
        #expect(vm.canPublish)
        mock.publishValue = PublishResult(dryRun: true, commit: nil, pushed: false,
                                          files: ["CHANGELOG.md", "Characters/PCs/Pat-Alpha.md"], applied: 1)
        await vm.publish(dryRun: true)
        #expect(mock.calls.contains("publish 60 dry=true"))
        #expect(vm.notice == "Dry run: 2 file(s) would change. Nothing was written.")
    }

    @Test func publishProblemsReadPlainly() async throws {
        let mock = MockHubClient()
        mock.publishError = .forbidden("publishing needs push access (SCRIBE_PUSH_BRANCHES)")
        let (vm, _, _) = try await setUp(mock)
        await vm.publish(dryRun: false)
        #expect(vm.errorMessage?.hasPrefix("Publishing needs write access") == true)
        mock.publishError = .http(status: 409, detail: "1 proposal(s) no longer match the vault")
        await vm.publish(dryRun: false)
        #expect(vm.errorMessage == "1 proposal(s) no longer match the vault. Cards that changed are marked below; review them again.")
    }

    @Test func evidencePlaysThatSpeakersMoment() async throws {
        let (vm, mock, player) = try await setUp()
        let e = try #require(vm.groups[0].cards[0].evidence.first)
        await vm.play(e)
        #expect(mock.calls.contains("clip 60 2 1750 3050"))
        #expect(player.played.count == 1 && vm.playingEvidence == e.utteranceId)
    }

    @Test func plainWordsForOperations() throws {
        let p = try #require(try cards().proposals.first)
        #expect(ReviewViewModel.describe(p) == "Update section · Appearance")
        #expect(ReviewViewModel.stateLabel("rejected_by_checks") == "Rejected by checks")
    }
}
