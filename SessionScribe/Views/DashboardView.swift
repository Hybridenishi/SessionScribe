import SwiftUI

struct DashboardView: View {
    @State var viewModel: DashboardViewModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if viewModel.waitingForReview > 0 {
                    Label("\(viewModel.waitingForReview) session(s) need you (see Sessions)",
                          systemImage: "exclamationmark.bubble")
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.orange.opacity(0.15), in: RoundedRectangle(cornerRadius: 8))
                }

                HealthListView(rows: viewModel.health)

                if let recent = viewModel.status?.sessions.recent, !recent.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Recent sessions").font(.headline)
                        ForEach(recent) { s in
                            HStack {
                                Text("Session \(s.number)").font(.body.weight(.medium))
                                Spacer()
                                Text(s.stateLabel).foregroundStyle(.secondary)
                            }
                            .padding(10)
                            .background(.background, in: RoundedRectangle(cornerRadius: 8))
                        }
                    }
                }
            }
            .padding(20)
            .frame(maxWidth: 720, alignment: .leading)
        }
        .navigationTitle("Dashboard")
        .toolbar {
            Button {
                Task { await viewModel.refresh() }
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
            .disabled(viewModel.isLoading)
        }
        .task {
            while !Task.isCancelled {
                await viewModel.refresh()
                try? await Task.sleep(for: .seconds(15))
            }
        }
    }
}
