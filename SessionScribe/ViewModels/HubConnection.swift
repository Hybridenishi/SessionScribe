import Foundation
import Observation

/// The app's link to scribe-hub: pairing state, the Keychain-backed token, and the client.
@MainActor
@Observable
final class HubConnection {
    enum State: Equatable {
        case unpaired
        case paired(HubCredentials)
        /// Was paired, but the hub refused the token (revoked or reset). Pair again.
        case needsRepair(HubCredentials)
    }

    typealias Pairer = @Sendable (URL, String) async throws -> PairResponse
    typealias ClientFactory = @Sendable (HubCredentials) -> any HubClient

    private(set) var state: State = .unpaired
    private(set) var client: (any HubClient)?
    private(set) var loadError: String?

    private let store: any HubCredentialStore
    private let pairer: Pairer
    private let makeClient: ClientFactory

    init(
        store: any HubCredentialStore = KeychainHubCredentialStore(),
        pairer: @escaping Pairer = { try await LiveHubClient.pair(baseURL: $0, code: $1) },
        makeClient: @escaping ClientFactory = { LiveHubClient(baseURL: $0.baseURL, token: $0.token) }
    ) {
        self.store = store
        self.pairer = pairer
        self.makeClient = makeClient
        do {
            if let creds = try store.load() {
                state = .paired(creds)
                client = makeClient(creds)
            }
        } catch {
            loadError = error.localizedDescription
        }
    }

    var credentials: HubCredentials? {
        switch state {
        case .unpaired: nil
        case let .paired(c), let .needsRepair(c): c
        }
    }

    var isPaired: Bool {
        if case .paired = state { return true }
        return false
    }

    /// Exchange a one-time code for a device token and keep it in Keychain.
    func pair(hubAddress: String, code: String) async throws {
        guard let url = HubURL.validated(hubAddress) else { throw HubError.invalidHubURL }
        let cleaned = code.trimmingCharacters(in: .whitespacesAndNewlines)
        let response = try await pairer(url, cleaned)
        let creds = HubCredentials(baseURL: url, token: response.token,
                                   deviceID: response.deviceId, deviceName: response.deviceName)
        try store.save(creds)
        state = .paired(creds)
        client = makeClient(creds)
        loadError = nil
    }

    /// Delete the token from this Mac. (Revoking it on the hub is a separate step.)
    func forget() throws {
        try store.delete()
        state = .unpaired
        client = nil
    }

    /// Turn an error into a message for the screen; a refused token flips the app to re-pair.
    func message(for error: any Error) -> String {
        if case HubError.unauthorized = error, let creds = credentials {
            state = .needsRepair(creds)
            client = nil
        }
        return error.localizedDescription
    }
}
