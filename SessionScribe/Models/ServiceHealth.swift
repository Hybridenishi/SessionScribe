import Foundation

struct ServiceHealth: Identifiable, Equatable, Sendable {
    enum Kind: String, CaseIterable, Identifiable, Sendable {
        case discordConnection
        case daveSecurity
        case audioReception
        case transcription
        case diskRecording

        var id: String { rawValue }

        var displayName: String {
            switch self {
            case .discordConnection:
                "Discord"
            case .daveSecurity:
                "DAVE"
            case .audioReception:
                "Audio"
            case .transcription:
                "Transcription"
            case .diskRecording:
                "Disk"
            }
        }
    }

    enum Status: String, Sendable {
        case inactive
        case healthy
        case degraded
        case failed
    }

    let kind: Kind
    var status: Status
    var detail: String

    var id: Kind { kind }
}

extension Array where Element == ServiceHealth {
    static var inactiveDefaults: [ServiceHealth] {
        ServiceHealth.Kind.allCases.map { kind in
            ServiceHealth(kind: kind, status: .inactive, detail: "Not started")
        }
    }
}
