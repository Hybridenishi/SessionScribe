import Foundation

struct Campaign: Identifiable, Equatable, Sendable {
    let id: UUID
    var name: String
    var systemName: String

    init(id: UUID = UUID(), name: String, systemName: String = "Dungeons & Dragons") {
        self.id = id
        self.name = name
        self.systemName = systemName
    }
}
