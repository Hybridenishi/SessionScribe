import Foundation
import Testing
@testable import SessionScribe

struct SessionScribeTests {
    @Test func validRecordingLifecycleTransitions() throws {
        var lifecycle = RecordingLifecycle()

        try lifecycle.transition(to: .connecting)
        #expect(lifecycle.state == .connecting)

        try lifecycle.transition(to: .ready)
        #expect(lifecycle.state == .ready)

        try lifecycle.transition(to: .recording)
        #expect(lifecycle.state == .recording)

        try lifecycle.transition(to: .stopping)
        #expect(lifecycle.state == .stopping)

        try lifecycle.transition(to: .completed)
        #expect(lifecycle.state == .completed)

        try lifecycle.transition(to: .idle)
        #expect(lifecycle.state == .idle)
    }

    @Test func invalidRecordingLifecycleTransitionThrows() {
        var lifecycle = RecordingLifecycle()

        #expect(throws: RecordingLifecycleError.invalidTransition(from: .idle, to: .recording)) {
            try lifecycle.transition(to: .recording)
        }
        #expect(lifecycle.state == .idle)
    }

    @Test func transcriptSegmentsSortChronologically() {
        let first = TranscriptSegment(
            participantID: "discord-user-1",
            displayName: "Nate",
            start: .seconds(3),
            end: .seconds(5),
            text: "First",
            status: .final
        )
        let tiedStartEarlierEnd = TranscriptSegment(
            participantID: "discord-user-2",
            displayName: "Mara",
            start: .seconds(8),
            end: .seconds(9),
            text: "Second",
            status: .final
        )
        let tiedStartLaterEnd = TranscriptSegment(
            participantID: "discord-user-3",
            displayName: "Silas",
            start: .seconds(8),
            end: .seconds(12),
            text: "Third",
            status: .partial
        )

        let sorted = [tiedStartLaterEnd, first, tiedStartEarlierEnd].chronologicallySorted()

        #expect(sorted.map(\.id) == [first.id, tiedStartEarlierEnd.id, tiedStartLaterEnd.id])
    }

    @Test func mockTranscriptionPreservesParticipantAttribution() async throws {
        let participants = [
            Participant(id: "discord-user-1", displayName: "Nate"),
            Participant(id: "discord-user-3", displayName: "Silas")
        ]
        let engine = MockTranscriptionEngine(segments: PreviewFixture.transcriptSegments)

        let segments = try await engine.segments(for: PreviewFixture.session, participants: participants)

        #expect(segments.count == 2)
        #expect(segments.map(\.participantID) == ["discord-user-1", "discord-user-3"])
        #expect(segments.map(\.displayName) == ["Nate", "Silas"])
    }
}
