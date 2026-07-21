import Foundation

struct SidecarSnapshot: Equatable, Sendable {
    var participants: [Participant]
    var health: [ServiceHealth]
}

protocol SidecarClient: Sendable {
    func connect() async throws -> SidecarSnapshot
    func beginRecording() async throws -> SidecarSnapshot
    func stopRecording() async throws -> SidecarSnapshot
}

struct MockSidecarClient: SidecarClient {
    var snapshot: SidecarSnapshot

    init(snapshot: SidecarSnapshot = PreviewFixture.sidecarSnapshot) {
        self.snapshot = snapshot
    }

    func connect() async throws -> SidecarSnapshot {
        snapshot
    }

    func beginRecording() async throws -> SidecarSnapshot {
        var recordingSnapshot = snapshot
        recordingSnapshot.health = ServiceHealth.Kind.allCases.map { kind in
            ServiceHealth(kind: kind, status: .healthy, detail: healthyDetail(for: kind))
        }
        return recordingSnapshot
    }

    func stopRecording() async throws -> SidecarSnapshot {
        var stoppedSnapshot = snapshot
        stoppedSnapshot.participants = stoppedSnapshot.participants.map { participant in
            var participant = participant
            participant.isSpeaking = false
            return participant
        }
        stoppedSnapshot.health = ServiceHealth.Kind.allCases.map { kind in
            ServiceHealth(kind: kind, status: .inactive, detail: "Stopped")
        }
        return stoppedSnapshot
    }

    private func healthyDetail(for kind: ServiceHealth.Kind) -> String {
        switch kind {
        case .discordConnection:
            "Mock sidecar connected"
        case .daveSecurity:
            "Encrypted voice boundary ready"
        case .audioReception:
            "Per-user frames arriving"
        case .transcription:
            "Mock engine available"
        case .diskRecording:
            "Archive writer ready"
        }
    }
}
