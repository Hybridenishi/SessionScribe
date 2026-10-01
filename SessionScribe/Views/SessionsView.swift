import SwiftUI
import UniformTypeIdentifiers

struct SessionsView: View {
    @State var viewModel: SessionsViewModel
    let connection: HubConnection
    let player: any AudioClipPlaying

    @State private var selection: Int?
    @State private var pendingZip: PendingUpload?
    @State private var pendingNumber = 1
    @State private var isImporting = false
    @State private var isDropTargeted = false

    var body: some View {
        NavigationSplitView {
            List(viewModel.sessions, selection: $selection) { s in
                VStack(alignment: .leading, spacing: 2) {
                    Text("Session \(s.number)").font(.body.weight(.medium))
                    Text(s.stateLabel).font(.caption).foregroundStyle(s.state == "blocked" ? .orange : .secondary)
                }
                .tag(s.number)
            }
            .overlay {
                if viewModel.sessions.isEmpty {
                    ContentUnavailableView("No sessions yet", systemImage: "waveform",
                                           description: Text("Drop a Craig export (.zip) here."))
                }
            }
            .safeAreaInset(edge: .bottom) { uploadBar }
            .onDrop(of: [.fileURL], isTargeted: $isDropTargeted, perform: handleDrop)
            .navigationSplitViewColumnWidth(min: 200, ideal: 240)
        } detail: {
            if let n = selection {
                SessionDetailView(viewModel: SessionDetailViewModel(number: n, connection: connection, player: player))
                    .id(n)
            } else {
                ContentUnavailableView("Pick a session", systemImage: "list.bullet.rectangle")
            }
        }
        .navigationTitle("Sessions")
        .fileImporter(isPresented: $isImporting, allowedContentTypes: [.zip]) { result in
            if case let .success(url) = result { stage(url) }
        }
        .sheet(item: $pendingZip) { pending in
            UploadSheet(zip: pending.url, number: $pendingNumber, isUploading: viewModel.isUploading) {
                Task {
                    if await viewModel.upload(zip: pending.url, number: pendingNumber) {
                        pendingZip = nil
                        selection = pendingNumber
                    }
                }
            } cancel: {
                pendingZip = nil
            }
        }
        .task {
            while !Task.isCancelled {
                await viewModel.refresh()
                try? await Task.sleep(for: .seconds(10))
            }
        }
    }

    private var uploadBar: some View {
        VStack(spacing: 6) {
            if let error = viewModel.errorMessage {
                Text(error).font(.caption).foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
            }
            Button {
                isImporting = true
            } label: {
                Label("Add Craig export…", systemImage: "plus")
                    .frame(maxWidth: .infinity)
            }
            .controlSize(.large)
        }
        .padding(10)
        .background(isDropTargeted ? Color.accentColor.opacity(0.2) : Color.clear)
    }

    private func stage(_ url: URL) {
        pendingNumber = viewModel.suggestedNumber
        pendingZip = PendingUpload(url: url)
    }

    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first else { return false }
        _ = provider.loadObject(ofClass: URL.self) { url, _ in
            guard let url, url.pathExtension.lowercased() == "zip" else { return }
            Task { @MainActor in stage(url) }
        }
        return true
    }
}

private struct PendingUpload: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}

private struct UploadSheet: View {
    let zip: URL
    @Binding var number: Int
    let isUploading: Bool
    let upload: () -> Void
    let cancel: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Add Craig export").font(.title3.weight(.semibold))
            Text(zip.lastPathComponent).foregroundStyle(.secondary)
            Stepper(value: $number, in: 1 ... 9_999) {
                LabeledContent("Session number") {
                    TextField("", value: $number, format: .number).frame(width: 70)
                }
            }
            Text("Check the number against the vault, not the recording date; they have disagreed before.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel, action: cancel)
                Button(isUploading ? "Uploading…" : "Upload", action: upload)
                    .keyboardShortcut(.defaultAction)
                    .disabled(isUploading)
            }
        }
        .padding(20)
        .frame(width: 420)
    }
}
