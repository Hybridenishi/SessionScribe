import Foundation

struct GameSession: Identifiable, Equatable, Sendable {
    enum ProcessingState: String, Sendable {
        case planned
        case live
        case finalizing
        case completed
        case needsReview
        case failed
    }

    let id: UUID
    var campaignID: Campaign.ID
    var title: String
    var startedAt: Date
    var duration: Duration
    var participantCount: Int
    var processingState: ProcessingState

    init(
        id: UUID = UUID(),
        campaignID: Campaign.ID,
        title: String,
        startedAt: Date,
        duration: Duration = .zero,
        participantCount: Int = 0,
        processingState: ProcessingState = .planned
    ) {
        self.id = id
        self.campaignID = campaignID
        self.title = title
        self.startedAt = startedAt
        self.duration = duration
        self.participantCount = participantCount
        self.processingState = processingState
    }
}
