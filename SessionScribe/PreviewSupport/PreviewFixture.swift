import Foundation

enum PreviewFixture {
    private static func uuid(_ value: String) -> UUID {
        UUID(uuidString: value) ?? UUID()
    }

    static let campaign = Campaign(
        id: uuid("00000000-0000-0000-0000-000000000101"),
        name: "The Ashen Vale"
    )

    static let session = GameSession(
        id: uuid("00000000-0000-0000-0000-000000000201"),
        campaignID: campaign.id,
        title: "Session 12: The Door Beneath Blackroot",
        startedAt: Date(timeIntervalSinceReferenceDate: 804_470_400),
        duration: .seconds(2_738),
        participantCount: participants.count,
        processingState: .live
    )

    static let participants: [Participant] = [
        Participant(id: "discord-user-1", displayName: "Nate", isSpeaking: true),
        Participant(id: "discord-user-2", displayName: "Mara", isSpeaking: false),
        Participant(id: "discord-user-3", displayName: "Silas", isSpeaking: true),
        Participant(id: "discord-user-4", displayName: "June", isSpeaking: false)
    ]

    static let health: [ServiceHealth] = [
        ServiceHealth(kind: .discordConnection, status: .healthy, detail: "Mock sidecar connected"),
        ServiceHealth(kind: .daveSecurity, status: .healthy, detail: "Encrypted voice boundary ready"),
        ServiceHealth(kind: .audioReception, status: .healthy, detail: "Per-user frames arriving"),
        ServiceHealth(kind: .transcription, status: .healthy, detail: "Mock engine streaming"),
        ServiceHealth(kind: .diskRecording, status: .healthy, detail: "Archive writer ready")
    ]

    static let transcriptSegments: [TranscriptSegment] = [
        TranscriptSegment(
            id: uuid("00000000-0000-0000-0000-000000000301"),
            participantID: "discord-user-1",
            displayName: "Nate",
            start: .seconds(6),
            end: .seconds(12),
            text: "The passage narrows, and the carved stones are warm to the touch.",
            status: .final
        ),
        TranscriptSegment(
            id: uuid("00000000-0000-0000-0000-000000000302"),
            participantID: "discord-user-3",
            displayName: "Silas",
            start: .seconds(10),
            end: .seconds(15),
            text: "I want to check the door for a glyph before anyone touches it.",
            status: .partial
        ),
        TranscriptSegment(
            id: uuid("00000000-0000-0000-0000-000000000303"),
            participantID: "discord-user-2",
            displayName: "Mara",
            start: .seconds(18),
            end: .seconds(23),
            text: "Can I help with that using my jeweler's tools?",
            status: .final
        )
    ]

    static let sidecarSnapshot = SidecarSnapshot(participants: participants, health: health)
}
