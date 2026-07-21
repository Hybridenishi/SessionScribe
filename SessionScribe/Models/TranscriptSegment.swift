import Foundation

struct TranscriptSegment: Identifiable, Equatable, Sendable {
    enum Status: String, Sendable {
        case partial
        case final
    }

    let id: UUID
    var participantID: Participant.ID
    var displayName: String
    var start: Duration
    var end: Duration
    var text: String
    var status: Status

    init(
        id: UUID = UUID(),
        participantID: Participant.ID,
        displayName: String,
        start: Duration,
        end: Duration,
        text: String,
        status: Status
    ) {
        self.id = id
        self.participantID = participantID
        self.displayName = displayName
        self.start = start
        self.end = end
        self.text = text
        self.status = status
    }
}

extension Array where Element == TranscriptSegment {
    func chronologicallySorted() -> [TranscriptSegment] {
        sorted {
            if $0.start == $1.start {
                $0.end < $1.end
            } else {
                $0.start < $1.start
            }
        }
    }
}
