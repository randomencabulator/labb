import LabbDesign
import SwiftUI

/// A conversation: its messages, and the composer at the bottom
struct ChatView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Bindable var session: ChatSession

    @State private var draft = ""
    @State private var attachments: [Attachment] = []
    /// Editing a sent message: the turn it replaces from
    @State private var editing: Int?
    @State private var toolShown: ToolShown?
    @State private var errors = ErrorAlert()
    @State private var renaming = false
    @State private var newTitle = ""
    @State private var deleting = false
    @State private var exported: URL?

    private var chat: Chat? { session.threadId.flatMap { id in app.chatList.first { $0.threadId == id } } }
    private var cliSession: CliSession? { session.threadId.flatMap { id in app.cliSessions.first { $0.threadId == id } } }
    private var agent: Agent? { app.agent(chat?.agentId ?? session.agentId) }

    private var title: String {
        if let title = chat?.title, !title.isEmpty { return title }
        if let title = cliSession?.title, !title.isEmpty { return title }
        if let pc = session.pc { return "\(pc.pcName): \((pc.folder as NSString).lastPathComponent)" }
        return agent.map { "\($0.emoji) \($0.name)" } ?? "New chat"
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: LabbSpace.s12) {
                    if session.turns.isEmpty && !session.loading {
                        EmptyChat(agent: agent, pc: session.pc) { starter in send(starter) }
                    }
                    ForEach(Array(session.turns.enumerated()), id: \.element.id) { index, turn in
                        TurnView(
                            turn: turn,
                            isLast: index == session.turns.count - 1,
                            canEdit: !session.streaming && !session.isTerminalSession,
                            onEdit: {
                                editing = index
                                draft = turn.user
                            },
                            onRegenerate: { session.send(turn.user, replaceFrom: index, keepAttachments: true) },
                            onTool: { id, name, args in toolShown = ToolShown(id: id, name: name, args: args) },
                            session: session
                        )
                        .id(turn.id)
                    }
                    if let loadError = session.loadError {
                        Label(loadError, systemImage: "exclamationmark.triangle").foregroundStyle(.labb.error)
                    }
                    Color.clear.frame(height: 1).id("end")
                }
                .padding(.horizontal, LabbSpace.s8)
                .padding(.vertical, LabbSpace.s8)
            }
            .defaultScrollAnchor(.bottom)
            .scrollDismissesKeyboard(.interactively)
            .background(Color.labb.bg)
            .overlay { if session.loading && session.turns.isEmpty { ProgressView() } }
            .onChange(of: session.turns.count) { _, _ in
                withAnimation { proxy.scrollTo("end", anchor: .bottom) }
            }
            .floatingBottom {
                ComposerView(
                    session: session,
                    text: $draft,
                    attachments: $attachments,
                    editing: $editing,
                    onSend: { send(draft) }
                )
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { toolbar }
        // The chat has the whole screen: the tabs come back on the way out
        .toolbar(.hidden, for: .tabBar)
        .sheet(item: $toolShown) { tool in
            ToolResultView(threadId: session.threadId, tool: tool)
        }
        .alert("Rename chat", isPresented: $renaming) {
            TextField("Title", text: $newTitle)
            Button("Cancel", role: .cancel) {}
            Button("Rename", action: rename)
        }
        .confirmationDialog("Delete this chat?", isPresented: $deleting, titleVisibility: .visible) {
            Button("Delete", role: .destructive, action: delete)
        } message: {
            Text("It's deleted for good.")
        }
        .sheet(item: Binding(get: { exported.map(SharedFile.init) }, set: { if $0 == nil { exported = nil } })) { file in
            ActivityView(items: [file.url])
        }
        .errorAlert(errors)
        .task(id: session.id) {
            if session.model == nil { session.model = agent?.model }
            if session.threadId != nil && !session.streaming { await session.load() }
        }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            Menu {
                if !session.isTerminalSession {
                    Picker(selection: $session.model) {
                        Text("Default (\(app.modelName(app.models?.default)))").tag(String?.none)
                        ForEach(app.models?.models ?? []) { model in
                            Text(model.name).tag(Optional(model.spec))
                        }
                    } label: {
                        Label("Model: \(session.model.map(app.modelName) ?? "Default")", systemImage: "cpu")
                    }
                    .pickerStyle(.menu)

                    Picker(selection: $session.answerMode) {
                        ForEach(AnswerMode.allCases) { mode in
                            Label(mode.label, systemImage: mode == .fast ? "hare" : mode == .thinking ? "brain" : "wand.and.stars")
                                .tag(mode)
                        }
                    } label: {
                        Label("Answer: \(session.answerMode.label)", systemImage: "brain")
                    }
                    .pickerStyle(.menu)

                    Toggle(isOn: $session.web) {
                        Label("Search the web", systemImage: "globe")
                    }
                }
                if let chat {
                    Divider()
                    Button("Rename", systemImage: "pencil") {
                        newTitle = chat.title
                        renaming = true
                    }
                    Button(chat.shared == true ? "Stop sharing" : "Share with everyone here", systemImage: "link") {
                        setShared(!(chat.shared == true))
                    }
                    if chat.shared == true, let url = app.serverURL?.appending(path: "shared/\(chat.threadId)") {
                        ShareLink("Send the link", item: url)
                    }
                    Button("Export as Markdown", systemImage: "square.and.arrow.up", action: export)
                    Button("Delete", systemImage: "trash", role: .destructive) { deleting = true }
                }
            } label: {
                Label("Options", systemImage: "ellipsis.circle")
            }
        }
    }

    // MARK: - Actions

    private func send(_ text: String) {
        let message = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !message.isEmpty || !attachments.isEmpty else { return }
        session.send(message, attachments: attachments, replaceFrom: editing, keepAttachments: editing != nil)
        draft = ""
        attachments = []
        editing = nil
    }

    private func rename() {
        guard let threadId = session.threadId else { return }
        let title = newTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty else { return }
        Task {
            await errors.run {
                try await app.api?.perform("PATCH", "/threads/\(threadId)", body: ["title": .string(title)])
                await app.refreshChats()
            }
        }
    }

    private func setShared(_ shared: Bool) {
        guard let threadId = session.threadId else { return }
        Task {
            await errors.run {
                try await app.api?.perform("PATCH", "/threads/\(threadId)", body: ["shared": .bool(shared)])
                await app.refreshChats()
            }
        }
    }

    private func export() {
        guard let threadId = session.threadId else { return }
        Task { await errors.run { exported = try await exportChat(threadId, title: title, app: app) } }
    }

    private func delete() {
        guard let threadId = session.threadId else { return }
        Task {
            await errors.run {
                try await app.api?.perform("DELETE", "/threads/\(threadId)")
                app.chats.forget(threadId)
                await app.refreshChats()
                dismiss()
            }
        }
    }
}

/// A new chat, before anything was sent: who you're talking to, and things to ask
private struct EmptyChat: View {
    let agent: Agent?
    let pc: PcTarget?
    let onStarter: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: LabbSpace.s8) {
            VStack(alignment: .leading, spacing: LabbSpace.s3) {
                if let agent {
                    Text(agent.emoji).font(.system(size: 44))
                    Text(agent.name).labbHeading(.xxl)
                    if !agent.description.isEmpty { Text(agent.description).foregroundStyle(.labb.muted) }
                } else if let pc {
                    Image(systemName: "desktopcomputer").font(.system(size: 36)).foregroundStyle(.labb.muted)
                    Text(pc.pcName).labbHeading(.xxl)
                    Text("labb runs on this PC, in \(pc.folder).").foregroundStyle(.labb.muted)
                } else {
                    Text("What's on your mind?").labbHeading(.xxl)
                    Text("Ask anything. labb can search the web, read your documents and remember what matters.")
                        .foregroundStyle(.labb.muted)
                }
            }
            if let starters = agent?.starters, !starters.isEmpty {
                VStack(spacing: LabbSpace.s4) {
                    ForEach(Array(starters.enumerated()), id: \.offset) { index, starter in
                        Button { onStarter(starter) } label: {
                            VStack(alignment: .leading, spacing: LabbSpace.s1) {
                                Text(String(format: "%02d", index + 1)).labbLabel()
                                Text(starter).multilineTextAlignment(.leading)
                            }
                            .labbCard()
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .padding(.top, LabbSpace.s16)
    }
}

private extension View {
    /// Content pinned over the bottom of a scroll view that it scrolls under: on iOS 26 the messages
    /// blur away behind it, as under a tab bar
    @ViewBuilder func floatingBottom<Bar: View>(@ViewBuilder _ bar: () -> Bar) -> some View {
        if #available(iOS 26, *) {
            safeAreaBar(edge: .bottom, content: bar)
        } else {
            safeAreaInset(edge: .bottom, content: bar)
        }
    }
}
