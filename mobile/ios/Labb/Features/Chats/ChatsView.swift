import LabbDesign
import SwiftUI

/// Where the Chats tab can go
enum ChatRoute: Hashable {
    /// A saved chat
    case saved(String)
    /// A new chat, by its session's id
    case new(UUID)
}

/// The Chats tab: your chats, and labb sessions from your PCs
struct ChatsView: View {
    @Environment(AppModel.self) private var app
    @State private var path: [ChatRoute] = []
    @State private var search = ""
    @State private var renaming: Chat?
    @State private var newTitle = ""
    @State private var deleting: Chat?
    @State private var errors = ErrorAlert()
    @State private var exported: URL?

    var body: some View {
        NavigationStack(path: $path) {
            List {
                if !pinned.isEmpty {
                    Section("Pinned") { ForEach(pinned) { row($0) } }
                }
                Section(pinned.isEmpty ? "" : "Chats") {
                    ForEach(others) { row($0) }
                }
                if !sessions.isEmpty {
                    Section("On your PCs") {
                        ForEach(sessions) { session in
                            NavigationLink(value: ChatRoute.saved(session.threadId)) {
                                SessionRow(session: session)
                            }
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .labbList()
            .overlay {
                if app.chatList.isEmpty && app.cliSessions.isEmpty {
                    ContentUnavailableView {
                        Label("No chats yet", systemImage: "bubble.left.and.bubble.right")
                    } description: {
                        Text("Start one with the button at the top.")
                    } actions: {
                        Button("New chat") { startChat() }.buttonStyle(.labb(.primary))
                    }
                } else if !search.isEmpty && filtered.isEmpty && sessions.isEmpty {
                    ContentUnavailableView.search(text: search)
                }
            }
            .searchable(text: $search, prompt: "Search chats")
            .refreshable { await app.refreshChats() }
            .navigationTitle("Chats")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Button("New chat", systemImage: "square.and.pencil") { startChat() }
                        if !app.agents.isEmpty {
                            Section("With an agent") {
                                ForEach(app.agents) { agent in
                                    Button("\(agent.emoji) \(agent.name)") { startChat(agentId: agent.id) }
                                }
                            }
                        }
                    } label: {
                        Label("New chat", systemImage: "square.and.pencil")
                    } primaryAction: {
                        startChat()
                    }
                }
            }
            .navigationDestination(for: ChatRoute.self) { route in
                switch route {
                case .saved(let threadId): ChatView(session: app.chats.session(for: threadId, app: app))
                case .new(let id):
                    if let session = app.chats.newSession(id) { ChatView(session: session) }
                }
            }
            .alert("Rename chat", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
                TextField("Title", text: $newTitle)
                Button("Cancel", role: .cancel) {}
                Button("Rename") { if let chat = renaming { rename(chat) } }
            }
            .confirmationDialog("Delete this chat?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                                titleVisibility: .visible, presenting: deleting) { chat in
                Button("Delete", role: .destructive) { delete(chat) }
            } message: { chat in
                Text("“\(chat.title)” is deleted for good.")
            }
            .sheet(item: Binding(get: { exported.map(SharedFile.init) }, set: { if $0 == nil { exported = nil } })) { file in
                ActivityView(items: [file.url])
            }
            .errorAlert(errors)
            .task { await app.refreshChats() }
            .onChange(of: app.openChatRequest, initial: true) { _, request in
                guard let request else { return }
                path = [.saved(request)]
                app.openChatRequest = nil
            }
            .onChange(of: app.pendingNewChat, initial: true) { _, request in
                guard let request else { return }
                path = [.new(request)]
                app.pendingNewChat = nil
            }
        }
    }

    // MARK: - Rows

    private var filtered: [Chat] {
        let query = search.trimmingCharacters(in: .whitespaces)
        return query.isEmpty ? app.chatList : app.chatList.filter { $0.title.localizedCaseInsensitiveContains(query) }
    }

    private var pinned: [Chat] { filtered.filter(\.pinned) }
    private var others: [Chat] { filtered.filter { !$0.pinned } }

    private var sessions: [CliSession] {
        let query = search.trimmingCharacters(in: .whitespaces)
        return query.isEmpty ? app.cliSessions : app.cliSessions.filter { $0.title.localizedCaseInsensitiveContains(query) }
    }

    private func row(_ chat: Chat) -> some View {
        NavigationLink(value: ChatRoute.saved(chat.threadId)) {
            ChatRow(chat: chat, agent: app.agent(chat.agentId), answering: app.chats.answering.contains(chat.threadId))
        }
        .swipeActions(edge: .trailing) {
            Button("Delete", systemImage: "trash", role: .destructive) { deleting = chat }
        }
        .swipeActions(edge: .leading) {
            Button(chat.pinned ? "Unpin" : "Pin", systemImage: chat.pinned ? "pin.slash" : "pin") { setPinned(chat, !chat.pinned) }
                .tint(.labb.accentFill)
        }
        .contextMenu {
            Button("Rename", systemImage: "pencil") {
                newTitle = chat.title
                renaming = chat
            }
            Button(chat.pinned ? "Unpin" : "Pin", systemImage: chat.pinned ? "pin.slash" : "pin") { setPinned(chat, !chat.pinned) }
            Button("Export as Markdown", systemImage: "square.and.arrow.up") { export(chat) }
            Divider()
            Button("Delete", systemImage: "trash", role: .destructive) { deleting = chat }
        }
    }

    // MARK: - Actions

    private func startChat(agentId: String? = nil) {
        let session = app.chats.newSession(app: app, agentId: agentId)
        path = [.new(session.id)]
    }

    private func setPinned(_ chat: Chat, _ pinned: Bool) {
        Task {
            await errors.run {
                try await app.api?.perform("PATCH", "/threads/\(chat.threadId)", body: ["pinned": .bool(pinned)])
                await app.refreshChats()
            }
        }
    }

    private func rename(_ chat: Chat) {
        let title = newTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return }
        Task {
            await errors.run {
                try await app.api?.perform("PATCH", "/threads/\(chat.threadId)", body: ["title": .string(title)])
                await app.refreshChats()
            }
        }
    }

    private func delete(_ chat: Chat) {
        Task {
            await errors.run {
                try await app.api?.perform("DELETE", "/threads/\(chat.threadId)")
                app.chats.forget(chat.threadId)
                path.removeAll { $0 == .saved(chat.threadId) }
                await app.refreshChats()
            }
        }
    }

    private func export(_ chat: Chat) {
        Task {
            await errors.run { exported = try await exportChat(chat.threadId, title: chat.title, app: app) }
        }
    }
}

/// Downloads a chat as Markdown into a file to share
@MainActor
func exportChat(_ threadId: String, title: String, app: AppModel) async throws -> URL? {
    guard let api = app.api else { return nil }
    let (data, _) = try await api.data("GET", "/export", query: ["thread_id": threadId])
    let name = title.replacingOccurrences(of: "/", with: "-").prefix(60)
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("\(name.isEmpty ? "chat" : String(name)).md")
    try data.write(to: url)
    return url
}

struct ChatRow: View {
    let chat: Chat
    let agent: Agent?
    let answering: Bool

    var body: some View {
        HStack(spacing: LabbSpace.s6) {
            if let agent {
                Text(agent.emoji).font(.title3)
            }
            VStack(alignment: .leading, spacing: LabbSpace.s1) {
                HStack(spacing: LabbSpace.s3) {
                    if chat.unread == true { Circle().fill(Color.labb.accentFill).frame(width: 8, height: 8) }
                    Text(chat.title.isEmpty ? "New chat" : chat.title)
                        .labbText(.md, weight: chat.unread == true ? LabbWeight.bold : LabbWeight.medium)
                        .lineLimit(1)
                }
                HStack(spacing: LabbSpace.s3) {
                    if answering {
                        Text("Answering…").foregroundStyle(.labb.accent)
                    } else {
                        Text(relativeTime(chat.updatedAt))
                    }
                    if chat.shared == true { Image(systemName: "link") }
                }
                .labbText(.sm)
                .foregroundStyle(.labb.muted)
            }
            Spacer(minLength: 0)
            if answering { ProgressView().controlSize(.small) }
        }
    }
}

struct SessionRow: View {
    let session: CliSession

    var body: some View {
        VStack(alignment: .leading, spacing: LabbSpace.s1) {
            Text(session.title.isEmpty ? "Session" : session.title).labbText(.md, weight: LabbWeight.medium).lineLimit(1)
            HStack(spacing: LabbSpace.s3) {
                Image(systemName: "desktopcomputer")
                Text("\(session.machine) · \((session.directory as NSString).lastPathComponent)")
                Text(relativeTime(session.updatedAt))
            }
            .labbText(.sm)
            .foregroundStyle(.labb.muted)
            .lineLimit(1)
        }
    }
}

/// A file to hand to the share sheet
struct SharedFile: Identifiable {
    let url: URL
    var id: URL { url }
}

/// The system share sheet
struct ActivityView: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
