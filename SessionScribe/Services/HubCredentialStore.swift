import Foundation
import Security

/// What the app keeps after pairing. The token is the only credential the app holds (spec §3.1).
struct HubCredentials: Codable, Equatable, Sendable {
    let baseURL: URL
    let token: String
    let deviceID: String
    let deviceName: String
}

protocol HubCredentialStore: Sendable {
    func load() throws -> HubCredentials?
    func save(_ credentials: HubCredentials) throws
    func delete() throws
}

enum HubCredentialStoreError: LocalizedError, Equatable {
    case keychain(OSStatus)
    case corrupt

    var errorDescription: String? {
        switch self {
        case let .keychain(status):
            (SecCopyErrorMessageString(status, nil) as String?) ?? "Keychain error \(status)."
        case .corrupt:
            "The saved hub connection is unreadable. Pair this Mac again."
        }
    }
}

/// Keychain storage: this device only, never synced to iCloud (Addendum 1 §4).
struct KeychainHubCredentialStore: HubCredentialStore {
    private let service: String
    private let account: String

    init(service: String = "com.natedavis.SessionScribe.hub", account: String = "device") {
        self.service = service
        self.account = account
    }

    private var query: [CFString: Any] {
        [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: account,
            kSecAttrSynchronizable: kCFBooleanFalse as Any
        ]
    }

    func load() throws -> HubCredentials? {
        var q = query
        q[kSecReturnData] = true
        q[kSecMatchLimit] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else {
            throw HubCredentialStoreError.keychain(status)
        }
        guard let creds = try? JSONDecoder().decode(HubCredentials.self, from: data) else {
            throw HubCredentialStoreError.corrupt
        }
        return creds
    }

    func save(_ credentials: HubCredentials) throws {
        let data = try JSONEncoder().encode(credentials)
        let attributes: [CFString: Any] = [
            kSecValueData: data,
            kSecAttrAccessible: kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        ]
        let update = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if update == errSecSuccess { return }
        guard update == errSecItemNotFound else { throw HubCredentialStoreError.keychain(update) }
        var add = query
        attributes.forEach { add[$0.key] = $0.value }
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess else { throw HubCredentialStoreError.keychain(status) }
    }

    func delete() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw HubCredentialStoreError.keychain(status)
        }
    }
}

/// For previews and tests.
final class InMemoryHubCredentialStore: HubCredentialStore, @unchecked Sendable {
    private let lock = NSLock()
    private var value: HubCredentials?

    init(_ value: HubCredentials? = nil) {
        self.value = value
    }

    func load() throws -> HubCredentials? {
        lock.withLock { value }
    }

    func save(_ credentials: HubCredentials) throws {
        lock.withLock { value = credentials }
    }

    func delete() throws {
        lock.withLock { value = nil }
    }
}
