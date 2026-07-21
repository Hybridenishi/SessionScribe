import SwiftUI

struct LiveSessionView: View {
    @Bindable var viewModel: LiveSessionViewModel

    var body: some View {
        VStack(spacing: 0) {
            LiveSessionHeader(viewModel: viewModel)

            Divider()

            HStack(spacing: 0) {
                VStack(spacing: 16) {
                    ParticipantListView(participants: viewModel.participants)
                    HealthListView(health: viewModel.health)
                }
                .frame(width: 280)
                .padding(20)
                .background(.regularMaterial)

                Divider()

                TranscriptFeedView(segments: viewModel.transcriptSegments)
                    .padding(20)
            }
        }
        .navigationTitle("Live Session")
    }
}

private struct LiveSessionHeader: View {
    @Bindable var viewModel: LiveSessionViewModel

    var body: some View {
        HStack(alignment: .center, spacing: 20) {
            VStack(alignment: .leading, spacing: 6) {
                Text(viewModel.session.title)
                    .font(.title2.weight(.semibold))
                Text(viewModel.campaign.name)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            StateBadge(state: viewModel.lifecycleState)

            TimelineView(.periodic(from: .now, by: 1)) { context in
                Text(formatElapsed(viewModel.elapsedTime(at: context.date)))
                    .font(.system(.title3, design: .monospaced).weight(.semibold))
                    .frame(minWidth: 96, alignment: .trailing)
            }

            Button {
                Task { await viewModel.startRecording() }
            } label: {
                Label("Start", systemImage: "record.circle")
            }
            .disabled(!viewModel.canStart)
            .keyboardShortcut("r", modifiers: [.command])

            Button(role: .destructive) {
                Task { await viewModel.stopRecording() }
            } label: {
                Label("Stop", systemImage: "stop.circle")
            }
            .disabled(!viewModel.canStop)
        }
        .padding(20)
        .background(.background)
    }

    private func formatElapsed(_ duration: Duration) -> String {
        let totalSeconds = max(0, Int(duration.components.seconds))
        let hours = totalSeconds / 3_600
        let minutes = (totalSeconds % 3_600) / 60
        let seconds = totalSeconds % 60
        return String(format: "%02d:%02d:%02d", hours, minutes, seconds)
    }
}

private struct StateBadge: View {
    let state: RecordingLifecycleState

    var body: some View {
        Text(state.displayName)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .foregroundStyle(foregroundStyle)
            .background(backgroundStyle, in: Capsule())
    }

    private var foregroundStyle: Color {
        switch state {
        case .recording, .failed:
            .white
        default:
            .primary
        }
    }

    private var backgroundStyle: Color {
        switch state {
        case .idle:
            .gray.opacity(0.18)
        case .connecting, .stopping:
            .orange.opacity(0.22)
        case .ready:
            .teal.opacity(0.22)
        case .recording:
            .red
        case .completed:
            .green.opacity(0.22)
        case .failed:
            .red.opacity(0.85)
        }
    }
}

#Preview {
    LiveSessionView(viewModel: LiveSessionViewModel())
        .frame(width: 980, height: 680)
}
