import Foundation

enum RecordingLifecycleState: String, CaseIterable, Identifiable, Sendable {
    case idle
    case connecting
    case ready
    case recording
    case stopping
    case completed
    case failed

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .idle:
            "Idle"
        case .connecting:
            "Connecting"
        case .ready:
            "Ready"
        case .recording:
            "Recording"
        case .stopping:
            "Stopping"
        case .completed:
            "Completed"
        case .failed:
            "Failed"
        }
    }

    func canTransition(to nextState: RecordingLifecycleState) -> Bool {
        switch (self, nextState) {
        case (.idle, .connecting),
             (.connecting, .ready),
             (.connecting, .failed),
             (.ready, .recording),
             (.ready, .failed),
             (.recording, .stopping),
             (.recording, .failed),
             (.stopping, .completed),
             (.stopping, .failed),
             (.completed, .idle),
             (.failed, .idle):
            true
        default:
            false
        }
    }
}

enum RecordingLifecycleError: Error, Equatable, Sendable {
    case invalidTransition(from: RecordingLifecycleState, to: RecordingLifecycleState)
}

struct RecordingLifecycle: Sendable {
    private(set) var state: RecordingLifecycleState

    init(state: RecordingLifecycleState = .idle) {
        self.state = state
    }

    mutating func transition(to nextState: RecordingLifecycleState) throws {
        guard state.canTransition(to: nextState) else {
            throw RecordingLifecycleError.invalidTransition(from: state, to: nextState)
        }

        state = nextState
    }
}
