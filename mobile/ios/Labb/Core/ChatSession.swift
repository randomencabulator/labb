import Foundation
import Observation

/// One chat: its turns, and the reply being written, if any. A reply keeps going when the chat
/// isn't on screen, and is joined again from its start when the connection breaks.
@MainActor
@Observable
final class ChatSession: Identifiable {
    /// Stable while the chat is open, also when a new chat gets its thread id
    let id = UUID()
    private(set) var threadId: String?
    private(set) var turns: [Turn] = []
    private(set) var streaming = false
    private(set) var stopping = false
    private(set) var loading = false
    private(set) var loadError: String?

    // What the next message is sent with
    var model: String?
    var answerMode: AnswerMode = .auto
    var web = true
    /// A new chat's agent; a chat keeps the one it started with
    var agentId: String?
    /// A new chat on a PC, in one of its folders
    var pc: PcTarget?

    private var stream: Task<Void, Never>?
    private weak var app: AppModel?

    /// How long Stop waits for the server to end the reply before giving up on it
    private static let stopTimeout: Duration = .milliseconds(1500)
    /// Waits between tries to join a reply again after losing its stream (about two minutes in all)
    private static let reconnectWaits: [Double] = [0.5, 1, 2, 4, 8, 15, 30, 30, 30]

    init(threadId: String?, app: AppModel) {
        self.threadId = threadId
        self.app = app
    }

    var isNew: Bool { threadId == nil && turns.isEmpty }
    /// A labb session in the terminal, synced or on a PC
    var isTerminalSession: Bool { threadId?.hasPrefix("cli-") ?? false || pc != nil }

    // MARK: - Loading

    /// The saved conversation, then the reply being written in it, if there is one
    func load() async {
        guard let api = app?.api, let threadId, !streaming else { return }
        loading = true
        defer { loading = false }
        do {
            let events: [ChatEvent] = try await api.get("/history", query: ["thread_id": threadId])
            var turns: [Turn] = []
            for event in events { turns.apply(event) }
            turns.replayed()
            self.turns = turns
            loadError = nil
            startFollowing()
        } catch {
            loadError = "Couldn't load the conversation: \(error.localizedDescription)"
        }
    }

    // MARK: - Sending

    /// Send a message. With `replaceFrom`, it replaces that user message and everything after it
    /// (editing a message, or regenerating the last reply).
    func send(_ message: String, attachments: [Attachment] = [], replaceFrom: Int? = nil, keepAttachments: Bool = false) {
        guard let app, let api = app.api, !streaming else { return }
        var kept: (files: [String], images: [String]) = ([], [])
        if let replaceFrom, keepAttachments, turns.indices.contains(replaceFrom) {
            kept = (turns[replaceFrom].files, turns[replaceFrom].images)
        }
        if let replaceFrom { turns = Array(turns.prefix(replaceFrom)) }
        turns.apply(.user(
            content: message,
            files: kept.files + attachments.filter { $0.kind == .text }.map(\.name),
            images: kept.images + attachments.filter { $0.kind == .image }.map { "data:\($0.mimeType ?? "image/jpeg");base64,\($0.data)" },
            turn: nil
        ), now: .now)
        streaming = true
        stopping = false

        var body: [String: JSONValue] = [
            "message": .string(message),
            "thread_id": .optional(threadId),
            "answer_mode": .string(answerMode.rawValue),
            "web": .bool(web),
            "keep_attachments": .bool(keepAttachments),
            "attachments": .array(attachments.map(\.json)),
        ]
        if let model { body["model"] = .string(model) }
        if let replaceFrom { body["replace_from"] = .number(Double(replaceFrom)) }
        if threadId == nil {
            if let agentId { body["agent_id"] = .string(agentId) }
            if let pc {
                body["pc_id"] = .string(pc.pcId)
                body["folder"] = .string(pc.folder)
            }
        }
        let sentFrom = threadId
        let refusedReplacement = replaceFrom != nil

        stream = Task { [weak self] in
            guard let self else { return }
            var ended = false
            var failure: Error?
            do {
                for try await event in api.events("POST", "/chat", body: .object(body)) {
                    switch event {
                    case .thread(let id):
                        self.threadId = id
                        app.chats.registered(self)
                        await app.refreshChats()
                    case .title:
                        await app.refreshChats()
                    default:
                        if case .done = event { ended = true }
                        if case .stopped = event { ended = true }
                        self.turns.apply(event, now: .now)
                    }
                }
            } catch {
                failure = error
            }
            let cancelled = Task.isCancelled
            // The connection went: the reply goes on on the server, so join it again. A new chat that
            // never got its id can't be found again.
            let lost = !ended && !cancelled && (failure.map(isConnectionLost) ?? true) && self.threadId != nil
            if let failure, !lost, !cancelled {
                self.turns.apply(.error(failure.localizedDescription))
                // A replacement the server refused left the conversation as it was
                if refusedReplacement, sentFrom != nil {
                    self.finish()
                    await self.load()
                    return
                }
            }
            if lost {
                self.stream = nil
                await self.keepFollowing()
                return
            }
            self.finish()
            await app.refreshChats()
        }
    }

    private func finish() {
        turns.abort()
        streaming = false
        stopping = false
        stream = nil
    }

    /// Stop the reply. The server stops at its next step and ends the stream; if that takes longer
    /// (a tool can run for a while), stop waiting.
    func stop() {
        guard streaming, let api = app?.api else { return }
        stopping = true
        let stream = self.stream
        if let threadId {
            Task { try? await api.perform("POST", "/stop", body: ["thread_id": .string(threadId)]) }
            Task {
                try? await Task.sleep(for: Self.stopTimeout)
                stream?.cancel()
            }
        } else {
            stream?.cancel()
        }
    }

    // MARK: - Joining a reply

    private enum Followed {
        case ended, none, lost
    }

    private func startFollowing() {
        guard stream == nil else { return }
        stream = Task { [weak self] in
            await self?.keepFollowing()
        }
    }

    /// Join the reply being written, from its start, and again while the stream keeps breaking off
    private func keepFollowing() async {
        for wait in [0] + Self.reconnectWaits {
            if wait > 0 { try? await Task.sleep(for: .seconds(wait)) }
            if Task.isCancelled { break }
            let followed = await follow()
            if followed != .lost {
                if followed == .none, streaming {
                    // It ended meanwhile: what it wrote is saved
                    finish()
                    stream = nil
                    await load()
                }
                stream = nil
                return
            }
        }
        if streaming {
            turns.apply(.error("Lost the connection to the server. The reply goes on there: open the chat again to see it."))
            finish()
        }
        stream = nil
    }

    private func follow() async -> Followed {
        guard let app, let api = app.api, let threadId else { return .none }
        let path = threadId.hasPrefix("cli-") ? "/pcs/live" : "/chat/live"
        var joined = false
        var ended = false
        var lost = false
        do {
            for try await event in api.events("GET", path, query: ["thread_id": threadId]) {
                if case .user(_, _, _, let turn) = event {
                    joined = true
                    streaming = true
                    stopping = false
                    // What's saved may have this turn already, or the ones it replaces
                    turns = Array(turns.prefix(turn ?? Int.max))
                }
                if case .done = event { ended = true }
                if case .stopped = event { ended = true }
                if joined { turns.apply(event, now: .now) }
            }
            lost = joined && !ended && !Task.isCancelled
        } catch {
            lost = !Task.isCancelled && isConnectionLost(error)
        }
        if joined && !lost {
            finish()
            await app.refreshChats()
        }
        return lost ? .lost : joined ? .ended : .none
    }

    // MARK: - Answers

    func answerApproval(_ id: String, allow: Bool) async throws {
        guard let api = app?.api else { return }
        try await api.perform("POST", "/chat/approve", body: ["id": .string(id), "allow": .bool(allow), "reason": nil])
        turns.answer(approval: id, allowed: allow)
    }

    func answerQuestion(_ id: String, text: String) async throws {
        guard let api = app?.api else { return }
        try await api.perform("POST", "/chat/answer", body: ["id": .string(id), "text": .string(text)])
        turns.answer(question: id, text: text)
    }
}

/// The chats open in the app, so each keeps its reply going on its own
@MainActor
@Observable
final class ChatStore {
    private var byThread: [String: ChatSession] = [:]
    /// New chats, by their own id, until the server gives them a thread id (and after)
    private var new: [UUID: ChatSession] = [:]

    /// A new chat: with an agent, or on a PC
    func newSession(app: AppModel, agentId: String? = nil, pc: PcTarget? = nil) -> ChatSession {
        let session = ChatSession(threadId: nil, app: app)
        session.agentId = agentId
        session.pc = pc
        new[session.id] = session
        return session
    }

    func newSession(_ id: UUID) -> ChatSession? {
        new[id]
    }

    /// The session of a saved chat, made the first time it's opened
    func session(for threadId: String, app: AppModel) -> ChatSession {
        if let session = byThread[threadId] { return session }
        let session = ChatSession(threadId: threadId, app: app)
        byThread[threadId] = session
        return session
    }

    /// A new chat got its thread id: find it by that from now on
    func registered(_ session: ChatSession) {
        if let threadId = session.threadId { byThread[threadId] = session }
    }

    func forget(_ threadId: String) {
        byThread[threadId] = nil
    }

    func removeAll() {
        byThread = [:]
        new = [:]
    }

    /// Chats with a reply being written
    var answering: Set<String> {
        Set(byThread.values.filter(\.streaming).compactMap(\.threadId))
    }
}
