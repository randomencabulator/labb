import Foundation

// The server's types, as the web app has them (frontend/web/src/api.ts). Fields the server may
// leave out are optional, so a newer server doesn't break the app.

// MARK: - Accounts

enum Role: String, Codable, Sendable, CaseIterable {
    case admin, member
}

struct User: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let username: String
    let role: Role
    var disabled: Bool?
    var createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, username, role, disabled
        case createdAt = "created_at"
    }

    var isAdmin: Bool { role == .admin }
}

struct AuthStatus: Codable, Sendable {
    let setupNeeded: Bool
    let user: User?

    enum CodingKeys: String, CodingKey {
        case setupNeeded = "setup_needed"
        case user
    }
}

struct ApiKey: Codable, Sendable, Identifiable {
    let id: String
    let name: String
    let shown: String
    let createdAt: String
    let lastUsedAt: String?
    let revokedAt: String?
    var username: String?
    /// Only when it's just been made
    var key: String?

    enum CodingKeys: String, CodingKey {
        case id, name, shown, username, key
        case createdAt = "created_at"
        case lastUsedAt = "last_used_at"
        case revokedAt = "revoked_at"
    }
}

struct Invite: Codable, Sendable, Identifiable {
    let id: String
    let role: Role
    let createdAt: String
    let expiresAt: String
    /// Only when it's just been made
    var url: String?

    enum CodingKeys: String, CodingKey {
        case id, role, url
        case createdAt = "created_at"
        case expiresAt = "expires_at"
    }
}

struct InviteInfo: Codable, Sendable {
    let role: Role
    let expiresAt: String

    enum CodingKeys: String, CodingKey {
        case role
        case expiresAt = "expires_at"
    }
}

struct DeviceLogin: Codable, Sendable {
    let machine: String
    let expiresAt: String

    enum CodingKeys: String, CodingKey {
        case machine
        case expiresAt = "expires_at"
    }
}

// MARK: - Chats

struct Chat: Codable, Sendable, Identifiable, Hashable {
    let threadId: String
    var title: String
    var updatedAt: String
    var pinned: Bool
    var agentId: String?
    var unread: Bool?
    var shared: Bool?

    var id: String { threadId }

    enum CodingKeys: String, CodingKey {
        case title, pinned, unread, shared
        case threadId = "thread_id"
        case updatedAt = "updated_at"
        case agentId = "agent_id"
    }
}

/// A labb session from the terminal: synced from a PC, or started here on one (`pcId`)
struct CliSession: Codable, Sendable, Identifiable, Hashable {
    let threadId: String
    let machine: String
    let directory: String
    var title: String
    var updatedAt: String
    var synced: Bool?
    var pcId: String?

    var id: String { threadId }

    enum CodingKeys: String, CodingKey {
        case machine, directory, title, synced
        case threadId = "thread_id"
        case updatedAt = "updated_at"
        case pcId = "pc_id"
    }
}

struct ModelInfo: Codable, Sendable, Identifiable, Hashable {
    let spec: String
    let name: String
    var vision: Bool?
    var tools: Bool?

    var id: String { spec }
}

struct ModelList: Codable, Sendable {
    let `default`: String
    var browser: Bool?
    let models: [ModelInfo]
}

enum AnswerMode: String, Codable, Sendable, CaseIterable, Identifiable {
    case fast, auto, thinking

    var id: String { rawValue }

    var label: String {
        switch self {
        case .fast: "Fast"
        case .auto: "Auto"
        case .thinking: "Thinking"
        }
    }

    var detail: String {
        switch self {
        case .fast: "Answers right away"
        case .auto: "Thinks when it helps"
        case .thinking: "Thinks before answering"
        }
    }
}

struct Stats: Codable, Sendable, Hashable {
    var model: String?
    var durationMs: Double?
    var firstTokenMs: Double?
    var inputTokens: Double?
    var outputTokens: Double?
    var llmCalls: Double?
    var toolCalls: Double?
    var stopped: Bool?
    var error: Bool?
    var engine: String?

    enum CodingKeys: String, CodingKey {
        case model, stopped, error, engine
        case durationMs = "duration_ms"
        case firstTokenMs = "first_token_ms"
        case inputTokens = "input_tokens"
        case outputTokens = "output_tokens"
        case llmCalls = "llm_calls"
        case toolCalls = "tool_calls"
    }
}

struct Attachment: Codable, Sendable, Identifiable {
    var id = UUID()
    let name: String
    let kind: Kind
    let data: String
    var mimeType: String?

    enum Kind: String, Codable, Sendable {
        case text, image
    }

    enum CodingKeys: String, CodingKey {
        case name, kind, data
        case mimeType = "mime_type"
    }

    var json: JSONValue {
        var object: [String: JSONValue] = ["name": .string(name), "kind": .string(kind.rawValue), "data": .string(data)]
        if let mimeType { object["mime_type"] = .string(mimeType) }
        return .object(object)
    }
}

struct ToolResult: Codable, Sendable {
    let name: String
    let args: [String: JSONValue]?
    let content: String?
}

struct SharedChatInfo: Codable, Sendable {
    let title: String
}

// MARK: - Agents

struct Agent: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let owner: String?
    let mine: Bool
    var name: String
    var emoji: String
    var description: String
    var instructions: String
    var model: String?
    var toolsets: [String]?
    var memories: Bool
    var starters: [String]
    var shared: Bool
    var documents: JSONValue?
    var skills: JSONValue?
    var engineId: String?

    enum CodingKeys: String, CodingKey {
        case id, owner, mine, name, emoji, description, instructions, model, toolsets, memories, starters, shared, documents, skills
        case engineId = "engine_id"
    }
}

struct ToolChoice: Codable, Sendable, Identifiable, Hashable {
    let name: String
    let label: String
    let description: String

    var id: String { name }
}

// MARK: - Tasks

struct TaskRun: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let taskId: String
    let threadId: String?
    let status: String
    let error: String?
    let trigger: String
    let queuedAt: String
    let startedAt: String?
    let finishedAt: String?
    let inputTokens: Double?
    let outputTokens: Double?

    enum CodingKeys: String, CodingKey {
        case id, status, error, trigger
        case taskId = "task_id"
        case threadId = "thread_id"
        case queuedAt = "queued_at"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case inputTokens = "input_tokens"
        case outputTokens = "output_tokens"
    }
}

/// When a task runs, in its time zone; Monday is day 0
struct Schedule: Codable, Sendable, Hashable {
    enum Kind: String, Codable, Sendable, CaseIterable, Identifiable {
        case once, minutes, hours, daily, weekdays, weekly, days, monthly
        var id: String { rawValue }

        var label: String {
            switch self {
            case .once: "Once"
            case .minutes: "Every few minutes"
            case .hours: "Every few hours"
            case .daily: "Every day"
            case .weekdays: "Weekdays"
            case .weekly: "On some days of the week"
            case .days: "Every few days"
            case .monthly: "Every month"
            }
        }
    }

    var kind: Kind
    var at: String?
    var every: Int?
    var time: String?
    var days: [Int]?
    var day: Int?
    var start: String?

    var json: JSONValue {
        var object: [String: JSONValue] = ["kind": .string(kind.rawValue)]
        switch kind {
        case .once: object["at"] = .optional(at)
        case .minutes, .hours: object["every"] = .number(Double(every ?? 1))
        case .daily, .weekdays: object["time"] = .optional(time)
        case .weekly:
            object["days"] = .array((days ?? []).map { .number(Double($0)) })
            object["time"] = .optional(time)
        case .days:
            object["every"] = .number(Double(every ?? 1))
            object["time"] = .optional(time)
        case .monthly:
            object["day"] = .number(Double(day ?? 1))
            object["time"] = .optional(time)
        }
        return .object(object)
    }
}

struct LabbTask: Codable, Sendable, Identifiable, Hashable {
    let id: String
    var name: String
    var prompt: String
    var agentId: String?
    var schedule: Schedule
    var when: String
    var tz: String
    var mode: String
    var threadId: String?
    var enabled: Bool
    var nextRun: String?
    var failures: Int?
    var disabledReason: String?
    var lastRun: TaskRun?

    enum CodingKeys: String, CodingKey {
        case id, name, prompt, schedule, when, tz, mode, enabled, failures
        case agentId = "agent_id"
        case threadId = "thread_id"
        case nextRun = "next_run"
        case disabledReason = "disabled_reason"
        case lastRun = "last_run"
    }
}

struct SchedulePreview: Codable, Sendable {
    let when: String
    let next: [String]
}

// MARK: - Library

struct DocumentInfo: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let name: String
    let bytes: Int
    let chars: Int?
    let pages: Int?
    let chunks: Int?
    let status: String
    let error: String?
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, name, bytes, chars, pages, chunks, status, error
        case createdAt = "created_at"
    }
}

struct DocumentText: Codable, Sendable {
    let id: String
    let name: String
    let pages: [String]
    let paged: Bool
}

struct Memory: Codable, Sendable, Identifiable, Hashable {
    let id: String
    var text: String
    var pinned: Bool
    let createdAt: String
    var updatedAt: String
    var reviewed: Bool?

    enum CodingKeys: String, CodingKey {
        case id, text, pinned, reviewed
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct Notes: Codable, Sendable {
    let text: String
    let digest: String
    let topics: [String]
}

struct Skill: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let owner: String?
    let mine: Bool
    let name: String
    let description: String
    var shared: Bool
    let files: [String]
    let updatedAt: String
    /// Only from GET /skills/<id>
    var skillMd: String?

    enum CodingKeys: String, CodingKey {
        case id, owner, mine, name, description, shared, files
        case updatedAt = "updated_at"
        case skillMd = "skill_md"
    }
}

// MARK: - PCs

struct Pc: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let name: String
    let folders: [String]
    let version: String?
    let allowAll: Bool?
    let lastSeen: String?
    let models: [String]?
    let online: Bool

    enum CodingKeys: String, CodingKey {
        case id, name, folders, version, models, online
        case allowAll = "allow_all"
        case lastSeen = "last_seen"
    }
}

/// A new chat that runs on a PC, in one of its folders
struct PcTarget: Hashable, Sendable {
    let pcId: String
    let pcName: String
    let folder: String
}

// MARK: - Usage

struct UsageTotals: Codable, Sendable {
    let replies: Int
    let inputTokens: Double
    let outputTokens: Double
    let durationMs: Double
    let toolCalls: Double
    let stopped: Int
    let errors: Int
    let conversations: Int?

    enum CodingKeys: String, CodingKey {
        case replies, stopped, errors, conversations
        case inputTokens = "input_tokens"
        case outputTokens = "output_tokens"
        case durationMs = "duration_ms"
        case toolCalls = "tool_calls"
    }
}

struct Usage: Codable, Sendable {
    struct Day: Codable, Sendable, Hashable {
        let day: String
        let source: String
        let replies: Int
        let inputTokens: Double
        let outputTokens: Double

        enum CodingKeys: String, CodingKey {
            case day, source, replies
            case inputTokens = "input_tokens"
            case outputTokens = "output_tokens"
        }
    }

    struct ByModel: Codable, Sendable, Hashable {
        let model: String
        let replies: Int
        let inputTokens: Double
        let outputTokens: Double
        let avgFirstTokenMs: Double?
        let tokensPerSecond: Double?

        enum CodingKeys: String, CodingKey {
            case model, replies
            case inputTokens = "input_tokens"
            case outputTokens = "output_tokens"
            case avgFirstTokenMs = "avg_first_token_ms"
            case tokensPerSecond = "tokens_per_second"
        }
    }

    struct ToolCount: Codable, Sendable, Hashable {
        let name: String
        let calls: Int
    }

    struct Conversation: Codable, Sendable, Hashable {
        let threadId: String
        let title: String
        let source: String
        let replies: Int
        let tokens: Double
        let exists: Bool

        enum CodingKeys: String, CodingKey {
            case title, source, replies, tokens, exists
            case threadId = "thread_id"
        }
    }

    let totals: UsageTotals
    let byDay: [Day]
    let byModel: [ByModel]
    let tools: [ToolCount]
    let topConversations: [Conversation]?

    enum CodingKeys: String, CodingKey {
        case totals, tools
        case byDay = "by_day"
        case byModel = "by_model"
        case topConversations = "top_conversations"
    }
}

// MARK: - Engines

struct Engine: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let name: String
    let description: String
    let builtIn: Bool
    let mine: Bool
    let owner: String?
    let version: Int
    let webProblems: [String]

    enum CodingKeys: String, CodingKey {
        case id, name, description, mine, owner, version
        case builtIn = "built_in"
        case webProblems = "web_problems"
    }
}

struct EngineList: Codable, Sendable {
    let engines: [Engine]
    let myDefault: String?
    let serverDefault: String?

    enum CodingKeys: String, CodingKey {
        case engines
        case myDefault = "my_default"
        case serverDefault = "server_default"
    }
}

// MARK: - Studio

struct WorkflowSummary: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let owner: String?
    let mine: Bool
    let name: String
    let emoji: String
    let description: String
    let publishedVersion: Int?
    let shared: Bool
    let enabled: Bool
    let nextRun: String?
    let updatedAt: String
    let steps: Int

    enum CodingKeys: String, CodingKey {
        case id, owner, mine, name, emoji, description, shared, enabled, steps
        case publishedVersion = "published_version"
        case nextRun = "next_run"
        case updatedAt = "updated_at"
    }
}

struct StepState: Codable, Sendable, Identifiable, Hashable {
    let nodeId: String
    let key: String
    let status: String
    let output: JSONValue?
    let error: String?
    let startedAt: String?
    let finishedAt: String?

    var id: String { nodeId }

    enum CodingKeys: String, CodingKey {
        case key, status, output, error
        case nodeId = "node_id"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
    }
}

struct WorkflowRun: Codable, Sendable, Identifiable, Hashable {
    let id: String
    let workflowId: String
    let trigger: String
    let status: String
    let error: String?
    let result: JSONValue?
    let queuedAt: String
    let startedAt: String?
    let finishedAt: String?
    let steps: [StepState]?
    let workflow: String?
    let emoji: String?

    enum CodingKeys: String, CodingKey {
        case id, trigger, status, error, result, steps, workflow, emoji
        case workflowId = "workflow_id"
        case queuedAt = "queued_at"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
    }
}

struct WaitingApproval: Codable, Sendable, Identifiable, Hashable {
    let runId: String
    let workflowId: String
    let workflow: String
    let nodeId: String
    let label: String
    let message: String
    let since: String

    var id: String { runId + nodeId }

    enum CodingKeys: String, CodingKey {
        case workflow, label, message, since
        case runId = "run_id"
        case workflowId = "workflow_id"
        case nodeId = "node_id"
    }
}

// MARK: - Settings and admin

struct AppConnection: Codable, Sendable, Identifiable, Hashable {
    struct Connection: Codable, Sendable, Hashable {
        let account: String
        let status: String
        let missing: [String]
    }

    let name: String
    let label: String
    let uses: [String]
    let available: Bool
    let connection: Connection?

    var id: String { name }
}

struct ConnectStart: Codable, Sendable {
    let url: String
    let mode: String
}

struct HealthCheck: Codable, Sendable, Identifiable, Hashable {
    let name: String
    let ok: Bool?
    let detail: String

    var id: String { name }
}

struct LimitInfo: Codable, Sendable, Identifiable, Hashable {
    let key: String
    let group: String
    let label: String
    let description: String
    let `default`: Double
    let min: Double
    let max: Double
    let unit: String
    var value: Double

    var id: String { key }
}
