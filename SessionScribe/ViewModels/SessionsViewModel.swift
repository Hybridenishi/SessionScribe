import Foundation
import Observation

@MainActor
@Observable
final class SessionsViewModel {
    private(set) var sessions: [SessionSummary] = []
    private(set) var errorMessage: String?
    private(set) var isUploading = false
    private let connection: HubConnection

    init(connection: HubConnection) {
        self.connection = connection
    }

    /// The next session number to suggest for an upload.
    var suggestedNumber: Int {
        (sessions.map(\.number).max() ?? 0) + 1
    }

    func refresh() async {
        guard let client = connection.client else { return }
        do {
            sessions = try await client.sessions()
            errorMessage = nil
        } catch {
            errorMessage = connection.message(for: error)
        }
    }

    /// Upload a Craig export. Returns true when the hub accepted it.
    @discardableResult
    func upload(zip: URL, number: Int) async -> Bool {
        guard let client = connection.client else { return false }
        guard number > 0 else {
            errorMessage = "Session numbers start at 1."
            return false
        }
        guard zip.pathExtension.lowercased() == "zip" else {
            errorMessage = "Drop the Craig export as a .zip file."
            return false
        }
        isUploading = true
        defer { isUploading = false }
        let scoped = zip.startAccessingSecurityScopedResource()
        defer { if scoped { zip.stopAccessingSecurityScopedResource() } }
        do {
            _ = try await client.uploadSession(number: number, zipFile: zip)
            errorMessage = nil
            await refresh()
            return true
        } catch {
            errorMessage = connection.message(for: error)
            return false
        }
    }
}

extension SessionSummary {
    /// Plain words for the session states the hub reports.
    var stateLabel: String {
        switch state {
        case "ingesting": "Unpacking"
        case "blocked": "Needs you"
        case "transcribing": "Transcribing"
        case "staging": "Staging"
        case "ready": "Ready"
        case "failed": "Failed"
        default: state.capitalized
        }
    }
}
