import SwiftUI

struct SessionDetailView: View {
    @State var viewModel: SessionDetailViewModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private static let palette: [Color] = [.blue, .orange, .green, .purple, .pink, .teal, .brown, .indigo]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            transcript
        }
        .navigationTitle("Session \(viewModel.number)")
        .toolbar {
            if viewModel.canRetry {
                Button("Retry") { Task { await viewModel.retry() } }
            }
            if viewModel.canRetranscribe {
                Button("Re-run transcription") { Task { await viewModel.retranscribe() } }
            }
        }
        .task {
            while !Task.isCancelled {
                await viewModel.refresh()
                let settled = ["ready", "failed", "blocked"].contains(viewModel.detail?.state ?? "")
                try? await Task.sleep(for: .seconds(settled ? 30 : 5))
            }
        }
        .onDisappear { viewModel.stop() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let error = viewModel.errorMessage {
                Text(error).foregroundStyle(.red)
            }
            HealthListView(rows: viewModel.stages.map { HealthRow(name: $0.title, status: $0.status, detail: $0.detail) },
                           title: "Progress")
            let tracks = viewModel.trackProgress
            if tracks.contains(where: { $0.fraction != 1 }) {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(tracks) { t in
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 6) {
                                if t.isActive {
                                    Image(systemName: "circle.fill")
                                        .font(.system(size: 7))
                                        .foregroundStyle(color(for: t.id))
                                        .symbolEffect(.pulse, options: .repeating, isActive: !reduceMotion)
                                        .accessibilityLabel("Transcribing now")
                                }
                                Text(t.speaker).font(.callout.weight(.medium))
                                    .foregroundStyle(color(for: t.id))
                                Spacer()
                                Text(t.text).font(.caption)
                                    .foregroundStyle(t.status == .degraded ? .orange : (t.status == .failed ? .red : .secondary))
                                    .lineLimit(1)
                            }
                            if let f = t.fraction {
                                ProgressView(value: f)
                            } else if t.status != .failed {
                                ProgressView().progressViewStyle(.linear)
                            }
                        }
                    }
                }
                .padding(10)
                .background(.background, in: RoundedRectangle(cornerRadius: 8))
            }
            if let tracks = viewModel.detail?.manifest?.tracks {
                HStack(spacing: 14) {
                    ForEach(tracks) { t in
                        Label(t.speaker?.label ?? t.username, systemImage: "circle.fill")
                            .foregroundStyle(color(for: t.index))
                            .font(.caption)
                    }
                }
            }
        }
        .padding(16)
    }

    @ViewBuilder
    private var transcript: some View {
        if viewModel.isLoadingTranscript && viewModel.utterances.isEmpty {
            ProgressView("Loading transcript…").frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if viewModel.utterances.isEmpty {
            ContentUnavailableView("No transcript yet", systemImage: "text.bubble",
                                   description: Text("It appears here when transcription finishes."))
        } else {
            List(viewModel.utterances) { u in
                Button {
                    Task {
                        if viewModel.playingID == u.id { viewModel.stop() } else { await viewModel.play(u) }
                    }
                } label: {
                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text(SessionDetailViewModel.timestamp(u.startMs))
                            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                            .frame(width: 58, alignment: .trailing)
                        Text(u.speaker).font(.callout.weight(.semibold))
                            .foregroundStyle(color(for: u.track))
                            .frame(width: 150, alignment: .leading)
                            .lineLimit(1)
                        Text(u.text).textSelection(.enabled)
                        Spacer(minLength: 0)
                        Image(systemName: viewModel.playingID == u.id ? "stop.circle" : "play.circle")
                            .foregroundStyle(.secondary)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help("Play this moment from \(u.speaker)'s track")
            }
            .listStyle(.inset)
        }
    }

    private func color(for track: Int) -> Color {
        Self.palette[viewModel.colorSlot(for: track) % Self.palette.count]
    }
}
