import SwiftUI

struct SettingsView: View {
    @State var viewModel: SettingsViewModel
    let connection: HubConnection

    var body: some View {
        Form {
            hubSection
            if connection.isPaired {
                devicesSection
                speakersSection
                vocabularySection
            }
            if let error = viewModel.errorMessage {
                Section { Text(error).foregroundStyle(.red) }
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Settings")
        .task { await viewModel.load() }
    }

    @ViewBuilder
    private var hubSection: some View {
        Section("Hub") {
            switch connection.state {
            case let .paired(creds):
                LabeledContent("Address", value: creds.baseURL.absoluteString)
                LabeledContent("This Mac", value: "\(creds.deviceName) (\(creds.deviceID))")
                Button("Forget this Mac", role: .destructive) { viewModel.forget() }
                    .help("Deletes the token from this Mac's Keychain. Revoke it below to cancel it on the hub too.")
            case .unpaired, .needsRepair:
                if case .needsRepair = connection.state {
                    Text("The hub stopped accepting this Mac's token. Pair it again.")
                        .foregroundStyle(.orange)
                }
                TextField("Hub address", text: $viewModel.hubAddress,
                          prompt: Text("https://atomsk.your-tailnet.ts.net:8443"))
                TextField("Pairing code", text: $viewModel.pairingCode, prompt: Text("ABCD-EFGH"))
                    .font(.body.monospaced())
                Text("On atomsk, run: docker exec -it scribe-hub scribe-hub pair --device \"Nate's MacBook Pro\"")
                    .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                if let error = viewModel.pairingError {
                    Text(error).foregroundStyle(.red)
                }
                Button(viewModel.isPairing ? "Pairing…" : "Pair") {
                    Task { await viewModel.pair() }
                }
                .disabled(viewModel.isPairing || viewModel.pairingCode.isEmpty || viewModel.hubAddress.isEmpty)
            }
        }
    }

    private var devicesSection: some View {
        Section("Paired devices and tokens") {
            ForEach(viewModel.devices) { d in
                HStack {
                    VStack(alignment: .leading) {
                        Text(d.name)
                        Text("\(d.scope) · \(d.id)").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if d.revokedAt != nil {
                        Text("Revoked").foregroundStyle(.secondary)
                    } else {
                        Button("Revoke", role: .destructive) { Task { await viewModel.revoke(d) } }
                    }
                }
            }
        }
    }

    private var speakersSection: some View {
        Section {
            ForEach($viewModel.speakers) { $row in
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        TextField("Player/Character", text: $row.label)
                        Picker("", selection: $row.role) {
                            Text("Player").tag("player")
                            Text("DM").tag("dm")
                        }
                        .labelsHidden()
                        .frame(width: 100)
                    }
                    HStack {
                        TextField("PC slug", text: $row.pc).frame(width: 110)
                        TextField("Discord user ID", text: $row.discordID)
                        TextField("Discord usernames, comma-separated", text: $row.usernames)
                    }
                    .font(.callout)
                }
                .padding(.vertical, 4)
            }
            .onDelete { viewModel.removeSpeakers(at: $0) }
            HStack {
                Button("Add speaker") { viewModel.addSpeaker() }
                Spacer()
                if let msg = viewModel.campaignMessage { Text(msg).foregroundStyle(.secondary) }
                Button("Save") { Task { await viewModel.saveCampaign() } }
            }
        } header: {
            Text("Speakers")
        } footer: {
            Text("Each Craig track is matched by Discord user ID first, then username. An unmatched track stops the session until you add it here and press Retry.")
        }
    }

    private var vocabularySection: some View {
        Section {
            TextEditor(text: $viewModel.vocabularyPrompt)
                .frame(minHeight: 90)
                .font(.callout)
        } header: {
            Text("Names for transcription")
        } footer: {
            Text("Write campaign names into plain sentences. A bare list of names makes Whisper repeat them during silences. Saved with Speakers.")
        }
    }
}
