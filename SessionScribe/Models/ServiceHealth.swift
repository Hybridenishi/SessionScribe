import Foundation

enum ServiceHealth {
    enum Status: String, Sendable {
        case inactive
        case healthy
        case degraded
        case failed
    }
}

/// One row in a health list: a name, a status and a short detail.
struct HealthRow: Identifiable, Equatable, Sendable {
    let name: String
    let status: ServiceHealth.Status
    let detail: String

    var id: String { name }
}
