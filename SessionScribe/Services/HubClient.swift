import Foundation

enum HubError: LocalizedError, Equatable {
    /// The token was refused (revoked, or the hub was reset). The app must pair again.
    case unauthorized
    /// The hub refused the request's tailnet identity or network.
    case forbidden(String)
    case http(status: Int, detail: String)
    case transport(String)
    case invalidResponse
    case invalidHubURL

    var errorDescription: String? {
        switch self {
        case .unauthorized:
            "The hub no longer accepts this Mac. Pair it again in Settings."
        case let .forbidden(detail):
            "The hub refused this request: \(detail)"
        case let .http(status, detail):
            "Hub error \(status): \(detail)"
        case let .transport(message):
            "Can't reach the hub: \(message)"
        case .invalidResponse:
            "The hub sent a response this app doesn't understand."
        case .invalidHubURL:
            "Enter the hub's https:// address (for example https://atomsk.your-tailnet.ts.net:8443)."
        }
    }
}

protocol HubClient: Sendable {
    func status() async throws -> HubStatus
    func sessions() async throws -> [SessionSummary]
    func session(_ number: Int) async throws -> SessionDetail
    func utterances(session: Int, offset: Int, limit: Int) async throws -> UtterancePage
    func audioClip(session: Int, track: Int, fromMs: Int, toMs: Int) async throws -> Data
    func uploadSession(number: Int, zipFile: URL) async throws -> UploadResponse
    func retry(session: Int) async throws
    func retranscribe(session: Int) async throws
    func campaign() async throws -> CampaignConfig
    func saveCampaign(_ campaign: CampaignConfig) async throws -> CampaignConfig
    func devices() async throws -> [HubDevice]
    func revokeDevice(_ id: String) async throws
    // H2 review
    func proposals(session: Int) async throws -> ProposalList
    func decide(proposal id: Int, action: String, after: String?) async throws -> Proposal
    func propose(session: Int) async throws
    func publish(session: Int, dryRun: Bool) async throws -> PublishResult
}

enum HubURL {
    /// https only, except plain http to this Mac for local development.
    static func validated(_ raw: String) -> URL? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard
            let url = URL(string: trimmed),
            let scheme = url.scheme?.lowercased(),
            let host = url.host(), !host.isEmpty
        else { return nil }
        if scheme == "https" { return url }
        if scheme == "http", ["localhost", "127.0.0.1", "::1"].contains(host) { return url }
        return nil
    }
}

struct LiveHubClient: HubClient {
    let baseURL: URL
    private let token: String
    private let session: URLSession

    init(baseURL: URL, token: String, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.token = token
        self.session = session
    }

    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    /// Exchange a one-time pairing code for a device token. The only unauthenticated call.
    static func pair(baseURL: URL, code: String, session: URLSession = .shared) async throws -> PairResponse {
        var request = URLRequest(url: baseURL.appending(path: "auth/pair"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["code": code])
        let data = try await send(request, session: session)
        return try decode(PairResponse.self, data)
    }

    func status() async throws -> HubStatus {
        try await get("status")
    }

    func sessions() async throws -> [SessionSummary] {
        let list: SessionList = try await get("sessions")
        return list.sessions
    }

    func session(_ number: Int) async throws -> SessionDetail {
        try await get("sessions/\(number)")
    }

    func utterances(session: Int, offset: Int, limit: Int) async throws -> UtterancePage {
        try await get("sessions/\(session)/utterances",
                      query: ["offset": "\(offset)", "limit": "\(limit)"])
    }

    func audioClip(session: Int, track: Int, fromMs: Int, toMs: Int) async throws -> Data {
        try await raw("GET", "sessions/\(session)/audio/\(track)",
                      query: ["from": "\(fromMs)", "to": "\(toMs)"])
    }

    func uploadSession(number: Int, zipFile: URL) async throws -> UploadResponse {
        let boundary = "scribe-\(UUID().uuidString)"
        let body = try MultipartFile.write(fileField: "file", fileURL: zipFile,
                                           contentType: "application/zip", boundary: boundary)
        defer { try? FileManager.default.removeItem(at: body) }
        var request = authorized("POST", "sessions", query: ["number": "\(number)"])
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 3_600
        let data = try await Self.send(request, session: session, uploadFrom: body)
        return try Self.decode(UploadResponse.self, data)
    }

    func retry(session number: Int) async throws {
        _ = try await raw("POST", "sessions/\(number)/retry")
    }

    func retranscribe(session number: Int) async throws {
        _ = try await raw("POST", "sessions/\(number)/transcribe")
    }

    func campaign() async throws -> CampaignConfig {
        try await get("campaign")
    }

    func saveCampaign(_ campaign: CampaignConfig) async throws -> CampaignConfig {
        var request = authorized("PUT", "campaign")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try Self.encoder.encode(campaign)
        return try Self.decode(CampaignConfig.self, try await Self.send(request, session: session))
    }

    func devices() async throws -> [HubDevice] {
        let list: DeviceList = try await get("auth/devices")
        return list.devices
    }

    func revokeDevice(_ id: String) async throws {
        _ = try await raw("DELETE", "auth/devices/\(id)")
    }

    func proposals(session number: Int) async throws -> ProposalList {
        try await get("sessions/\(number)/proposals")
    }

    func decide(proposal id: Int, action: String, after: String?) async throws -> Proposal {
        var body: [String: Any] = ["action": action]
        if let after { body["after"] = after }
        return try Self.decode(Proposal.self, try await sendJSON("PATCH", "proposals/\(id)", body))
    }

    func propose(session number: Int) async throws {
        _ = try await raw("POST", "sessions/\(number)/propose")
    }

    func publish(session number: Int, dryRun: Bool) async throws -> PublishResult {
        try Self.decode(PublishResult.self,
                        try await sendJSON("POST", "sessions/\(number)/publish", ["dry_run": dryRun]))
    }

    private func sendJSON(_ method: String, _ path: String, _ body: [String: Any]) async throws -> Data {
        var request = authorized(method, path)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        return try await Self.send(request, session: session)
    }

    // MARK: - Plumbing

    private func authorized(_ method: String, _ path: String, query: [String: String] = [:]) -> URLRequest {
        var url = baseURL.appending(path: path)
        if !query.isEmpty {
            url.append(queryItems: query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) })
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return request
    }

    private func get<T: Decodable>(_ path: String, query: [String: String] = [:]) async throws -> T {
        try Self.decode(T.self, try await raw("GET", path, query: query))
    }

    private func raw(_ method: String, _ path: String, query: [String: String] = [:]) async throws -> Data {
        try await Self.send(authorized(method, path, query: query), session: session)
    }

    static func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do {
            return try decoder.decode(type, from: data)
        } catch {
            throw HubError.invalidResponse
        }
    }

    static func send(_ request: URLRequest, session: URLSession, uploadFrom file: URL? = nil) async throws -> Data {
        let data: Data
        let response: URLResponse
        do {
            if let file {
                (data, response) = try await session.upload(for: request, fromFile: file)
            } else {
                (data, response) = try await session.data(for: request)
            }
        } catch {
            throw HubError.transport(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else { throw HubError.invalidResponse }
        switch http.statusCode {
        case 200 ..< 300:
            return data
        case 401:
            throw HubError.unauthorized
        case 403:
            throw HubError.forbidden(detail(from: data))
        default:
            throw HubError.http(status: http.statusCode, detail: detail(from: data))
        }
    }

    static func detail(from data: Data) -> String {
        if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let detail = object["detail"] {
            if let nested = detail as? [String: Any], let message = nested["message"] as? String {
                return message
            }
            return "\(detail)"
        }
        return String(decoding: data.prefix(300), as: UTF8.self)
    }
}

/// Builds a multipart/form-data body on disk, so a 1 GB Craig zip is never held in memory.
enum MultipartFile {
    static func write(fileField: String, fileURL: URL, contentType: String, boundary: String) throws -> URL {
        let out = FileManager.default.temporaryDirectory.appending(path: "upload-\(UUID().uuidString).multipart")
        FileManager.default.createFile(atPath: out.path(), contents: nil)
        let writer = try FileHandle(forWritingTo: out)
        defer { try? writer.close() }
        let name = fileURL.lastPathComponent.replacingOccurrences(of: "\"", with: "")
        let head = "--\(boundary)\r\n"
            + "Content-Disposition: form-data; name=\"\(fileField)\"; filename=\"\(name)\"\r\n"
            + "Content-Type: \(contentType)\r\n\r\n"
        try writer.write(contentsOf: Data(head.utf8))
        let reader = try FileHandle(forReadingFrom: fileURL)
        defer { try? reader.close() }
        while let chunk = try reader.read(upToCount: 4 * 1024 * 1024), !chunk.isEmpty {
            try writer.write(contentsOf: chunk)
        }
        try writer.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
        return out
    }
}
