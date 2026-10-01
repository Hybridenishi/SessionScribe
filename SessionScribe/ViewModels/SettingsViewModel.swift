import Foundation
import Observation

@MainActor
@Observable
final class SettingsViewModel {
    /// One editable row of the speaker map. Usernames are edited as comma-separated text.
    struct SpeakerRow: Identifiable, Equatable {
        let id = UUID()
        var label: String
        var role: String
        var pc: String
        var discordID: String
        var usernames: String
    }

    var hubAddress = ""
    var pairingCode = ""
    private(set) var isPairing = false
    private(set) var pairingError: String?

    private(set) var devices: [HubDevice] = []
    var speakers: [SpeakerRow] = []
    var vocabularyPrompt = ""
    private(set) var campaignMessage: String?
    private(set) var errorMessage: String?

    private let connection: HubConnection

    init(connection: HubConnection) {
        self.connection = connection
        hubAddress = connection.credentials?.baseURL.absoluteString ?? ""
    }

    func pair() async {
        isPairing = true
        defer { isPairing = false }
        do {
            try await connection.pair(hubAddress: hubAddress, code: pairingCode)
            pairingCode = ""
            pairingError = nil
            await load()
        } catch {
            pairingError = error.localizedDescription
        }
    }

    func forget() {
        do {
            try connection.forget()
            devices = []
            speakers = []
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func load() async {
        guard let client = connection.client else { return }
        do {
            devices = try await client.devices()
            apply(try await client.campaign())
            errorMessage = nil
        } catch HubError.http(status: 404, _) {
            speakers = []    // no campaign.yaml on the hub yet
        } catch {
            errorMessage = connection.message(for: error)
        }
    }

    func revoke(_ device: HubDevice) async {
        guard let client = connection.client else { return }
        do {
            try await client.revokeDevice(device.id)
            devices = try await client.devices()
        } catch {
            errorMessage = connection.message(for: error)
        }
    }

    func addSpeaker() {
        speakers.append(SpeakerRow(label: "", role: "player", pc: "", discordID: "", usernames: ""))
    }

    func removeSpeakers(at offsets: IndexSet) {
        speakers = speakers.enumerated().filter { !offsets.contains($0.offset) }.map(\.element)
    }

    var campaign: CampaignConfig {
        func blankToNil(_ s: String) -> String? {
            let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
            return t.isEmpty ? nil : t
        }
        return CampaignConfig(
            speakers: speakers.map { row in
                let names = row.usernames.split(separator: ",")
                    .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
                return CampaignConfig.Speaker(label: row.label.trimmingCharacters(in: .whitespaces),
                                              role: row.role, pc: blankToNil(row.pc),
                                              discordId: blankToNil(row.discordID),
                                              usernames: names.isEmpty ? nil : names)
            },
            vocabularyPrompt: vocabularyPrompt.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    func saveCampaign() async {
        guard let client = connection.client else { return }
        do {
            apply(try await client.saveCampaign(campaign))
            campaignMessage = "Saved."
            errorMessage = nil
        } catch {
            campaignMessage = nil
            errorMessage = connection.message(for: error)
        }
    }

    private func apply(_ c: CampaignConfig) {
        speakers = c.speakers.map {
            SpeakerRow(label: $0.label, role: $0.role, pc: $0.pc ?? "", discordID: $0.discordId ?? "",
                       usernames: ($0.usernames ?? []).joined(separator: ", "))
        }
        vocabularyPrompt = c.vocabularyPrompt
    }
}
