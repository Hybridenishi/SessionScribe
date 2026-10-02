import Foundation

// Wire types for the scribe-hub API (hub/README.md). Decoded with .convertFromSnakeCase.

struct HubStatus: Decodable, Equatable, Sendable {
    struct Sessions: Decodable, Equatable, Sendable {
        let recent: [SessionSummary]
        let blocked: Int
    }

    struct GPU: Decodable, Equatable, Sendable {
        let busy: Bool
        let waiting: Int
        let busyForS: Double
        let served: Int
        let naotaHealthy: Bool
    }

    struct Worker: Decodable, Equatable, Sendable {
        let name: String
        let lastSeen: Double
    }

    struct Vault: Decodable, Equatable, Sendable {
        let configured: Bool
        let pushBranches: Bool
    }

    let time: Double
    let sessions: Sessions
    /// lane -> state -> count
    let jobs: [String: [String: Int]]
    let gpu: GPU
    let macWorker: Worker?
    let vault: Vault
}

struct SessionSummary: Decodable, Equatable, Identifiable, Sendable {
    let number: Int
    let createdAt: Double
    let state: String
    let detail: String?

    var id: Int { number }
}

struct SessionList: Decodable, Sendable {
    let sessions: [SessionSummary]
}

struct SessionDetail: Decodable, Equatable, Sendable {
    struct Manifest: Decodable, Equatable, Sendable {
        struct Craig: Decodable, Equatable, Sendable {
            let recordingId: String?
            let startTime: String?
        }

        struct Track: Decodable, Equatable, Identifiable, Sendable {
            struct Speaker: Decodable, Equatable, Sendable {
                let label: String
                let role: String
                let pc: String?
            }

            let index: Int
            let file: String
            let username: String
            let discordId: String?
            let speaker: Speaker?
            let durationS: Double?

            var id: Int { index }
        }

        struct Unmapped: Decodable, Equatable, Sendable {
            let index: Int
            let username: String
            let discordId: String?
        }

        struct Stage3: Decodable, Equatable, Sendable {
            let utterances: Int
            let branch: String?
            let commit: String?
            let pushed: Bool
        }

        let craig: Craig
        let tracks: [Track]
        let unmapped: [Unmapped]
        let stage3: Stage3?
    }

    struct Job: Decodable, Equatable, Identifiable, Sendable {
        /// The Mac worker's last report for an S2 job: speech chunks done out of total, and when.
        struct Progress: Decodable, Equatable, Sendable {
            let done: Int
            let total: Int
            let at: Double
        }

        let id: Int
        let stage: String
        let lane: String
        let state: String
        let attempts: Int
        let error: String?
        let updatedAt: Double
        let track: Int?
        let progress: Progress?
    }

    let number: Int
    let createdAt: Double
    let state: String
    let detail: String?
    let manifest: Manifest?
    let jobs: [Job]
}

struct Utterance: Decodable, Equatable, Identifiable, Sendable {
    let id: String
    let track: Int
    let speaker: String
    let role: String
    let startMs: Int
    let endMs: Int
    let text: String
    let confidence: Double?
}

struct UtterancePage: Decodable, Sendable {
    let total: Int
    let offset: Int
    let utterances: [Utterance]
}

struct CampaignConfig: Codable, Equatable, Sendable {
    struct Speaker: Codable, Equatable, Identifiable, Sendable {
        var label: String
        var role: String
        var pc: String?
        var discordId: String?
        var usernames: [String]?

        var id: String { (discordId ?? "") + "|" + label }
    }

    var speakers: [Speaker]
    var vocabularyPrompt: String
}

struct PairResponse: Decodable, Sendable {
    let deviceId: String
    let deviceName: String
    let token: String
}

struct HubDevice: Decodable, Equatable, Identifiable, Sendable {
    let id: String
    let name: String
    let scope: String
    let createdAt: Double
    let lastSeen: Double?
    let revokedAt: Double?
}

struct DeviceList: Decodable, Sendable {
    let devices: [HubDevice]
}

struct UploadResponse: Decodable, Sendable {
    let session: Int
    let job: Int
}

// MARK: - H2 review

struct ProposalBatch: Decodable, Equatable, Sendable {
    let id: Int
    let session: Int
    let provider: String
    let createdAt: Double
    /// Files the agent touched that it must not; the hub reverted them.
    let violations: [String]
    let publishedAt: Double?
    let publishCommit: String?
}

struct Proposal: Decodable, Equatable, Identifiable, Sendable {
    struct Evidence: Decodable, Equatable, Hashable, Sendable {
        let utteranceId: String
        let track: Int
        let speaker: String
        let startMs: Int
        let endMs: Int
        let text: String
    }

    struct Conflict: Decodable, Equatable, Hashable, Sendable {
        let target: String?
        let section: String?
    }

    let id: Int
    let proposalId: String
    let op: String
    let target: String
    let section: String?
    let key: String?
    let fromPath: String?
    let after: String?
    let editedAfter: String?
    /// Read by the hub from the vault file, never written by the model.
    let before: String?
    let entity: String
    let secret: Bool
    let rationale: String
    let evidence: [Evidence]
    let conflicts: [Conflict]
    /// pending | accepted | rejected | deferred | rejected_by_checks | applied
    let state: String
    let checkError: String?

    /// What would be written: the DM's edit if there is one, otherwise the agent's text.
    var effectiveAfter: String { editedAfter ?? after ?? "" }
}

struct ProposalList: Decodable, Sendable {
    let batch: ProposalBatch?
    let proposals: [Proposal]
}

struct PublishResult: Decodable, Equatable, Sendable {
    let dryRun: Bool
    let commit: String?
    let pushed: Bool
    let files: [String]
    let applied: Int
}
