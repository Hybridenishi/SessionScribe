import Foundation
import Observation

@MainActor
@Observable
final class LiveSessionViewModel {
    private var lifecycle: RecordingLifecycle
    private let sidecarClient: any SidecarClient
    private let transcriptionEngine: any TranscriptionEngine
    private var recordingStartedAt: Date?

    var campaign: Campaign
    var session: GameSession
    var participants: [Participant]
    var transcriptSegments: [TranscriptSegment]
    var health: [ServiceHealth]
    var failureMessage: String?

    var lifecycleState: RecordingLifecycleState {
        lifecycle.state
    }

    var canStart: Bool {
        lifecycle.state == .idle || lifecycle.state == .ready || lifecycle.state == .completed || lifecycle.state == .failed
    }

    var canStop: Bool {
        lifecycle.state == .recording
    }

    init(
        campaign: Campaign = PreviewFixture.campaign,
        session: GameSession = PreviewFixture.session,
        participants: [Participant] = PreviewFixture.participants,
        transcriptSegments: [TranscriptSegment] = PreviewFixture.transcriptSegments,
        health: [ServiceHealth] = PreviewFixture.health,
        lifecycle: RecordingLifecycle = RecordingLifecycle(state: .ready),
        sidecarClient: any SidecarClient = MockSidecarClient(),
        transcriptionEngine: any TranscriptionEngine = MockTranscriptionEngine()
    ) {
        self.campaign = campaign
        self.session = session
        self.participants = participants
        self.transcriptSegments = transcriptSegments.chronologicallySorted()
        self.health = health
        self.lifecycle = lifecycle
        self.sidecarClient = sidecarClient
        self.transcriptionEngine = transcriptionEngine
    }

    func elapsedTime(at date: Date = Date()) -> Duration {
        guard lifecycle.state == .recording, let recordingStartedAt else {
            return session.duration
        }

        let milliseconds = Int64(date.timeIntervalSince(recordingStartedAt) * 1_000)
        return session.duration + .milliseconds(milliseconds)
    }

    func startRecording() async {
        do {
            if lifecycle.state == .completed || lifecycle.state == .failed {
                try lifecycle.transition(to: .idle)
                transcriptSegments.removeAll()
                session.duration = .zero
            }

            if lifecycle.state == .idle {
                try lifecycle.transition(to: .connecting)
                let connectedSnapshot = try await sidecarClient.connect()
                participants = connectedSnapshot.participants
                health = connectedSnapshot.health
                try lifecycle.transition(to: .ready)
            }

            try lifecycle.transition(to: .recording)
            let recordingSnapshot = try await sidecarClient.beginRecording()
            participants = recordingSnapshot.participants
            health = recordingSnapshot.health
            recordingStartedAt = Date()
            session.processingState = .live
            session.participantCount = participants.count
            transcriptSegments = try await transcriptionEngine.segments(for: session, participants: participants)
        } catch {
            markFailed(error)
        }
    }

    func stopRecording() async {
        do {
            try lifecycle.transition(to: .stopping)
            session.duration = elapsedTime()
            let stoppedSnapshot = try await sidecarClient.stopRecording()
            participants = stoppedSnapshot.participants
            health = stoppedSnapshot.health
            recordingStartedAt = nil
            session.processingState = .completed
            try lifecycle.transition(to: .completed)
        } catch {
            markFailed(error)
        }
    }

    private func markFailed(_ error: any Error) {
        failureMessage = error.localizedDescription
        recordingStartedAt = nil
        if lifecycle.state.canTransition(to: .failed) {
            try? lifecycle.transition(to: .failed)
        }
        health = health.map { item in
            var item = item
            if item.kind == .discordConnection || item.kind == .audioReception {
                item.status = .failed
                item.detail = "Mock workflow failed"
            }
            return item
        }
    }
}
