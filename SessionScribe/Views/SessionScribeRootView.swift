import SwiftUI

enum WorkspaceSection: String, CaseIterable, Identifiable {
    case campaigns
    case sessions
    case reviewInbox

    var id: String { rawValue }

    var title: String {
        switch self {
        case .campaigns:
            "Campaigns"
        case .sessions:
            "Sessions"
        case .reviewInbox:
            "Review Inbox"
        }
    }

    var systemImage: String {
        switch self {
        case .campaigns:
            "map"
        case .sessions:
            "waveform.and.mic"
        case .reviewInbox:
            "tray.full"
        }
    }
}

struct SessionScribeRootView: View {
    @State private var selection: WorkspaceSection? = .sessions
    @State private var liveSessionViewModel = LiveSessionViewModel()

    var body: some View {
        NavigationSplitView {
            List(WorkspaceSection.allCases, selection: $selection) { section in
                Label(section.title, systemImage: section.systemImage)
                    .tag(section)
            }
            .navigationTitle("SessionScribe")
        } detail: {
            switch selection ?? .sessions {
            case .campaigns:
                PlaceholderWorkspaceView(
                    title: "Campaigns",
                    systemImage: "map",
                    message: "Campaign management will collect canonical notes after recorder reliability is proven."
                )
            case .sessions:
                LiveSessionView(viewModel: liveSessionViewModel)
            case .reviewInbox:
                PlaceholderWorkspaceView(
                    title: "Review Inbox",
                    systemImage: "tray.full",
                    message: "Extracted transcript changes will land here for human review before they update campaign notes."
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

#Preview {
    SessionScribeRootView()
        .frame(width: 1_100, height: 720)
}
