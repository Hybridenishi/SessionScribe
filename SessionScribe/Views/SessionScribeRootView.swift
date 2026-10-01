import SwiftUI

enum WorkspaceSection: String, CaseIterable, Identifiable {
    case dashboard
    case sessions
    case review
    case settings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .dashboard:
            "Dashboard"
        case .sessions:
            "Sessions"
        case .review:
            "Review"
        case .settings:
            "Settings"
        }
    }

    var systemImage: String {
        switch self {
        case .dashboard:
            "gauge.with.dots.needle.33percent"
        case .sessions:
            "waveform"
        case .review:
            "rectangle.stack.badge.person.crop"
        case .settings:
            "gearshape"
        }
    }
}

struct SessionScribeRootView: View {
    @State private var selection: WorkspaceSection? = .dashboard
    @State private var connection: HubConnection
    @State private var player: any AudioClipPlaying

    init(connection: HubConnection = HubConnection(), player: any AudioClipPlaying = AVAudioClipPlayer()) {
        _connection = State(initialValue: connection)
        _player = State(initialValue: player)
    }

    var body: some View {
        NavigationSplitView {
            List(WorkspaceSection.allCases, selection: $selection) { section in
                Label(section.title, systemImage: section.systemImage)
                    .tag(section)
            }
            .navigationTitle("SessionScribe")
        } detail: {
            detail(for: selection ?? .dashboard)
        }
        .onAppear {
            if connection.credentials == nil { selection = .settings }
        }
    }

    @ViewBuilder
    private func detail(for section: WorkspaceSection) -> some View {
        switch section {
        case .settings:
            SettingsView(viewModel: SettingsViewModel(connection: connection), connection: connection)
        case .review:
            PlaceholderWorkspaceView(
                title: "Review",
                systemImage: "rectangle.stack.badge.person.crop",
                message: "Proposed vault changes arrive here as before/after cards in H2."
            )
        case .dashboard, .sessions:
            if connection.isPaired {
                if section == .dashboard {
                    DashboardView(viewModel: DashboardViewModel(connection: connection))
                } else {
                    SessionsView(viewModel: SessionsViewModel(connection: connection),
                                 connection: connection, player: player)
                }
            } else {
                PlaceholderWorkspaceView(
                    title: "Not connected",
                    systemImage: "network.slash",
                    message: "Pair this Mac with the hub in Settings."
                )
            }
        }
    }
}

private struct PlaceholderWorkspaceView: View {
    let title: String
    let systemImage: String
    let message: String

    var body: some View {
        ContentUnavailableView(title, systemImage: systemImage, description: Text(message))
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

#Preview("Paired") {
    SessionScribeRootView(connection: PreviewHub.connection(), player: PreviewHub.Player())
        .frame(width: 1_100, height: 720)
}

#Preview("Unpaired") {
    SessionScribeRootView(connection: HubConnection(store: InMemoryHubCredentialStore()),
                          player: PreviewHub.Player())
        .frame(width: 1_100, height: 720)
}
