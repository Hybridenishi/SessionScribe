import Foundation
import Observation

/// One row in the health list: a name, a status and a short detail.
struct HealthRow: Identifiable, Equatable, Sendable {
    let name: String
    let status: ServiceHealth.Status
    let detail: String

    var id: String { name }
}

@MainActor
@Observable
final class DashboardViewModel {
    private(set) var status: HubStatus?
    private(set) var errorMessage: String?
    private(set) var isLoading = false
    private let connection: HubConnection
    private let now: @Sendable () -> Date

    /// The Mac worker counts as healthy if it polled within this window.
    static let workerFreshness: TimeInterval = 10 * 60

    init(connection: HubConnection, now: @escaping @Sendable () -> Date = Date.init) {
        self.connection = connection
        self.now = now
    }

    func refresh() async {
        guard let client = connection.client else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            status = try await client.status()
            errorMessage = nil
        } catch {
            errorMessage = connection.message(for: error)
        }
    }

    var waitingForReview: Int {
        status?.sessions.blocked ?? 0
    }

    var health: [HealthRow] {
        guard let s = status else {
            return [HealthRow(name: "Hub", status: errorMessage == nil ? .inactive : .failed,
                              detail: errorMessage ?? "Not checked yet")]
        }
        var rows = [HealthRow(name: "Hub", status: .healthy, detail: "Connected")]

        let gpuDetail = s.gpu.busy
            ? "Busy for \(Int(s.gpu.busyForS)) s, \(s.gpu.waiting) waiting"
            : "Idle, \(s.gpu.served) served since restart"
        rows.append(HealthRow(name: "naota GPU",
                              status: s.gpu.naotaHealthy ? (s.gpu.waiting > 2 ? .degraded : .healthy) : .failed,
                              detail: s.gpu.naotaHealthy ? gpuDetail : "Model server not answering (gaming?)"))

        if let w = s.macWorker {
            let age = now().timeIntervalSince1970 - w.lastSeen
            rows.append(HealthRow(name: "Mac worker",
                                  status: age <= Self.workerFreshness ? .healthy : .degraded,
                                  detail: age <= Self.workerFreshness
                                      ? "\(w.name), seen \(Self.ago(age))"
                                      : "\(w.name) last seen \(Self.ago(age)); transcription waits until it's back"))
        } else {
            rows.append(HealthRow(name: "Mac worker", status: .inactive, detail: "Never connected"))
        }

        let queued = s.jobs["mac"]?["queued"] ?? 0
        let running = (s.jobs["mac"]?["running"] ?? 0) + (s.jobs["hub"]?["running"] ?? 0)
        let failed = s.jobs.values.reduce(0) { $0 + ($1["failed"] ?? 0) }
        rows.append(HealthRow(name: "Pipeline",
                              status: failed > 0 ? .degraded : .healthy,
                              detail: "\(running) running, \(queued) waiting for the Mac"
                                  + (failed > 0 ? ", \(failed) failed" : "")))

        rows.append(HealthRow(name: "Vault",
                              status: s.vault.configured ? .healthy : .degraded,
                              detail: s.vault.configured
                                  ? (s.vault.pushBranches ? "Staging branches, pushing to GitHub" : "Staging branches locally (push off)")
                                  : "No vault clone configured; transcripts are not staged"))
        rows.append(HealthRow(name: "Foundry", status: .inactive, detail: "Arrives in H4"))
        return rows
    }

    static func ago(_ seconds: TimeInterval) -> String {
        switch seconds {
        case ..<60: "just now"
        case ..<3_600: "\(Int(seconds / 60)) min ago"
        case ..<86_400: "\(Int(seconds / 3_600)) h ago"
        default: "\(Int(seconds / 86_400)) d ago"
        }
    }
}
