import Foundation
import Observation

/// The app's state: which server, who's signed in, and what several screens share (the chat list,
/// models, agents). Chats that are answering live in `chats` and keep going between screens.
@MainActor
@Observable
final class AppModel {
    enum Phase: Equatable {
        /// No server chosen yet
        case server
        /// Asking the server who we are
        case checking
        /// The server needs its first account, or a sign-in; `invite` when joining with one
        case signIn(setupNeeded: Bool, invite: String?)
        case ready
    }

    private(set) var phase: Phase = .server
    private(set) var api: APIClient?
    private(set) var user: User?
    /// Why the last check of the server failed
    var serverProblem: String?

    // Shared lists, refreshed as things change
    var chatList: [Chat] = []
    var cliSessions: [CliSession] = []
    var models: ModelList?
    var agents: [Agent] = []
    var pcs: [Pc] = []

    /// Chat sessions by thread id, and new ones by their own key, so a reply goes on while you look
    /// at something else
    let chats = ChatStore()

    /// Where the app goes next: a chat to open (from a task run, the usage list…)
    var openChatRequest: String?
    /// A new chat to open (from an agent, a PC folder), by its session's id
    var pendingNewChat: UUID?

    private static let serverKey = "server"

    var serverURL: URL? { api?.base }

    init() {
        if let saved = UserDefaults.standard.string(forKey: Self.serverKey), let url = URL(string: saved) {
            connect(to: url, invite: nil)
        }
    }

    // MARK: - Server and sign-in

    /// Turns what someone typed into a server address: a scheme if it has none (https, or http on a
    /// local network), and an invite token if it's an invite link
    static func parse(_ text: String) -> (url: URL, invite: String?)? {
        var text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if !text.contains("://") {
            let host = text.split(separator: "/").first.map(String.init) ?? text
            let local = host.hasPrefix("localhost") || host.hasPrefix("127.") || host.hasPrefix("192.168.") || host.hasPrefix("10.")
                || host.contains(".local") || (host.split(separator: ":").first.map { !$0.contains(".") } ?? false)
            text = (local ? "http://" : "https://") + text
        }
        guard var components = URLComponents(string: text), let scheme = components.scheme, ["http", "https"].contains(scheme),
              components.host?.isEmpty == false else { return nil }
        let invite = components.queryItems?.first(where: { $0.name == "invite" })?.value
        components.queryItems = nil
        components.fragment = nil
        if components.path == "/" { components.path = "" }
        guard let url = components.url else { return nil }
        return (url, invite)
    }

    /// Use this server: check it, then show what it needs (its first account, or a sign-in)
    func connect(to url: URL, invite: String?) {
        let api = APIClient(base: url) { [weak self] in
            Task { @MainActor in self?.sessionEnded() }
        }
        self.api = api
        serverProblem = nil
        phase = .checking
        Task { await check(invite: invite) }
    }

    func check(invite: String? = nil) async {
        guard let api else { return }
        do {
            let status: AuthStatus = try await api.get("/auth/status")
            UserDefaults.standard.set(api.base.absoluteString, forKey: Self.serverKey)
            if let user = status.user {
                signedIn(user)
            } else {
                phase = .signIn(setupNeeded: status.setupNeeded, invite: invite)
            }
        } catch {
            serverProblem = "Couldn't reach a labb server at \(api.base.absoluteString): \(error.localizedDescription)"
            phase = .server
        }
    }

    func signedIn(_ user: User) {
        self.user = user
        phase = .ready
        Task { await refreshAll() }
    }

    private func sessionEnded() {
        guard phase == .ready else { return }
        user = nil
        phase = .signIn(setupNeeded: false, invite: nil)
    }

    func signOut() async {
        guard let api else { return }
        try? await api.perform("POST", "/auth/logout")
        api.clearCookies()
        chats.removeAll()
        user = nil
        chatList = []
        cliSessions = []
        phase = .signIn(setupNeeded: false, invite: nil)
    }

    /// Choose another server: signs out of this one first
    func forgetServer() async {
        if user != nil { await signOut() }
        UserDefaults.standard.removeObject(forKey: Self.serverKey)
        api = nil
        phase = .server
    }

    // MARK: - Shared lists

    func refreshAll() async {
        async let chats: Void = refreshChats()
        async let models: Void = refreshModels()
        async let agents: Void = refreshAgents()
        async let pcs: Void = refreshPcs()
        _ = await (chats, models, agents, pcs)
    }

    func refreshChats() async {
        guard let api else { return }
        if let list: [Chat] = try? await api.get("/threads") { chatList = list }
        if let sessions: [CliSession] = try? await api.get("/cli_sessions") { cliSessions = sessions }
    }

    func refreshModels() async {
        guard let api else { return }
        if let list: ModelList = try? await api.get("/models") { models = list }
    }

    func refreshAgents() async {
        guard let api else { return }
        if let list: [Agent] = try? await api.get("/agents") { agents = list }
    }

    func refreshPcs() async {
        guard let api else { return }
        if let list: [Pc] = try? await api.get("/pcs") { pcs = list }
    }

    func agent(_ id: String?) -> Agent? {
        guard let id else { return nil }
        return agents.first { $0.id == id }
    }

    /// The name of a model by its spec, else the spec
    func modelName(_ spec: String?) -> String {
        guard let spec else { return "Default model" }
        return models?.models.first { $0.spec == spec }?.name ?? spec
    }
}
