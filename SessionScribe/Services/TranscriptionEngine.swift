import Foundation

protocol TranscriptionEngine: Sendable {
    func segments(for session: GameSession, participants: [Participant]) async throws -> [TranscriptSegment]
}

struct MockTranscriptionEngine: TranscriptionEngine {
    var segments: [TranscriptSegment]

    init(segments: [TranscriptSegment] = PreviewFixture.transcriptSegments) {
        self.segments = segments
    }

    func segments(for session: GameSession, participants: [Participant]) async throws -> [TranscriptSegment] {
        let participantIDs = Set(participants.map(\.id))
        return segments
            .filter { participantIDs.contains($0.participantID) }
            .chronologicallySorted()
    }
}
