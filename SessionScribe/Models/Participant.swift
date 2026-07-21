import Foundation

struct Participant: Identifiable, Equatable, Sendable {
    typealias DiscordUserID = String

    let id: DiscordUserID
    var displayName: String
    var isSpeaking: Bool
    var isConnected: Bool

    init(id: DiscordUserID, displayName: String, isSpeaking: Bool = false, isConnected: Bool = true) {
        self.id = id
        self.displayName = displayName
        self.isSpeaking = isSpeaking
        self.isConnected = isConnected
    }
}
