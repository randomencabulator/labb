import LabbDesign
import SwiftUI

/// The Agents tab: yours and the ones shared with everyone
struct AgentsView: View {
    @Environment(AppModel.self) private var app
    @State private var editing: AgentEditTarget?
    @State private var search = ""

    var body: some View {
        NavigationStack {
            List {
                if !mine.isEmpty {
                    Section("Yours") { ForEach(mine) { row($0) } }
                }
                if !shared.isEmpty {
                    Section("Shared with everyone") { ForEach(shared) { row($0) } }
                }
            }
            .listStyle(.insetGrouped)
            .labbList()
            .overlay {
                if app.agents.isEmpty {
                    ContentUnavailableView {
                        Label("No agents yet", systemImage: "person.2")
                    } description: {
                        Text("An agent is an assistant with its own instructions, tools and starters.")
                    } actions: {
                        Button("New agent") { editing = .new }.buttonStyle(.labb(.primary))
                    }
                }
            }
            .searchable(text: $search, prompt: "Search agents")
            .refreshable { await app.refreshAgents() }
            .navigationTitle("Agents")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button("New agent", systemImage: "plus") { editing = .new }
                }
            }
            .navigationDestination(for: Agent.self) { agent in AgentDetailView(agentId: agent.id) }
            .sheet(item: $editing) { target in AgentEditor(target: target) }
            .task { await app.refreshAgents() }
        }
    }

    private var filtered: [Agent] {
        search.isEmpty ? app.agents : app.agents.filter {
            $0.name.localizedCaseInsensitiveContains(search) || $0.description.localizedCaseInsensitiveContains(search)
        }
    }

    private var mine: [Agent] { filtered.filter(\.mine) }
    private var shared: [Agent] { filtered.filter { !$0.mine } }

    private func row(_ agent: Agent) -> some View {
        NavigationLink(value: agent) {
            HStack(spacing: LabbSpace.s6) {
                Text(agent.emoji).font(.title2).frame(width: 40, height: 40)
                    .background(Color.labb.active, in: RoundedRectangle(cornerRadius: LabbRadius.md))
                VStack(alignment: .leading, spacing: LabbSpace.s1) {
                    Text(agent.name).labbText(.md, weight: LabbWeight.semibold)
                    Text(agent.description.isEmpty ? (agent.mine ? "No description" : "By \(agent.owner ?? "someone")") : agent.description)
                        .labbText(.sm).foregroundStyle(.labb.muted).lineLimit(2)
                }
            }
        }
    }
}

enum AgentEditTarget: Identifiable {
    case new
    case edit(Agent)

    var id: String {
        switch self {
        case .new: "new"
        case .edit(let agent): agent.id
        }
    }
}

/// An agent: what it does, and a way to chat with it
struct AgentDetailView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let agentId: String
    @State private var editing: AgentEditTarget?
    @State private var deleting = false
    @State private var errors = ErrorAlert()

    private var agent: Agent? { app.agent(agentId) }

    var body: some View {
        if let agent {
            List {
                Section {
                    VStack(alignment: .leading, spacing: LabbSpace.s4) {
                        Text(agent.emoji).font(.system(size: 48))
                        Text(agent.name).labbHeading(.xxl)
                        if !agent.description.isEmpty { Text(agent.description).foregroundStyle(.labb.muted) }
                        Button {
                            startChat(agent)
                        } label: {
                            Label("Chat with \(agent.name)", systemImage: "bubble.left.and.bubble.right").frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.labb(.primary))
                        .padding(.top, LabbSpace.s4)
                    }
                    .padding(.vertical, LabbSpace.s4)
                    .listRowBackground(Color.clear)
                }
                if !agent.starters.isEmpty {
                    Section("Starters") {
                        ForEach(agent.starters, id: \.self) { starter in
                            Button(starter) { startChat(agent, message: starter) }
                                .foregroundStyle(.labb.text)
                        }
                    }
                }
                if !agent.instructions.isEmpty {
                    Section("Instructions") {
                        Text(agent.instructions).labbText(.sm).textSelection(.enabled)
                    }
                }
                Section("Settings") {
                    LabeledContent("Model", value: app.modelName(agent.model))
                    LabeledContent("Tools", value: agent.toolsets.map { $0.isEmpty ? "None" : $0.joined(separator: ", ") } ?? "All")
                    LabeledContent("Memories", value: agent.memories ? "On" : "Off")
                    LabeledContent("Shared", value: agent.shared ? "With everyone" : "No")
                    if !agent.mine, let owner = agent.owner { LabeledContent("By", value: owner) }
                }
            }
            .labbList()
            .navigationTitle(agent.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        if agent.mine { Button("Edit", systemImage: "pencil") { editing = .edit(agent) } }
                        Button("Duplicate", systemImage: "plus.square.on.square") { duplicate(agent) }
                        if agent.mine {
                            Button("Delete", systemImage: "trash", role: .destructive) { deleting = true }
                        }
                    } label: {
                        Label("Options", systemImage: "ellipsis.circle")
                    }
                }
            }
            .sheet(item: $editing) { target in AgentEditor(target: target) }
            .confirmationDialog("Delete \(agent.name)?", isPresented: $deleting, titleVisibility: .visible) {
                Button("Delete", role: .destructive) { delete(agent) }
            } message: {
                Text("Chats with it stay; new ones can't use it.")
            }
            .errorAlert(errors)
        } else {
            ContentUnavailableView("This agent is gone", systemImage: "person.crop.circle.badge.questionmark")
        }
    }

    private func startChat(_ agent: Agent, message: String? = nil) {
        let session = app.chats.newSession(app: app, agentId: agent.id)
        session.model = agent.model
        if let message {
            session.send(message)
            // It gets a thread id as it's sent; open it then
            Task {
                for _ in 0 ..< 50 where session.threadId == nil { try? await Task.sleep(for: .milliseconds(100)) }
                app.openChatRequest = session.threadId
            }
        } else {
            app.pendingNewChat = session.id
        }
    }

    private func duplicate(_ agent: Agent) {
        Task {
            await errors.run {
                let _: Agent = try await app.api!.send("POST", "/agents/\(agent.id)/duplicate")
                await app.refreshAgents()
            }
        }
    }

    private func delete(_ agent: Agent) {
        Task {
            await errors.run {
                try await app.api?.perform("DELETE", "/agents/\(agent.id)")
                await app.refreshAgents()
                dismiss()
            }
        }
    }
}

/// Making or changing an agent
struct AgentEditor: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let target: AgentEditTarget

    @State private var name = ""
    @State private var emoji = "🤖"
    @State private var description = ""
    @State private var instructions = ""
    @State private var model: String?
    @State private var allTools = true
    @State private var toolsets: Set<String> = []
    @State private var memories = true
    @State private var starters: [String] = []
    @State private var shared = false
    @State private var tools: [ToolChoice] = []
    @State private var saving = false
    @State private var errors = ErrorAlert()

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack(spacing: LabbSpace.s6) {
                        TextField("🤖", text: $emoji)
                            .font(.system(size: 32))
                            .multilineTextAlignment(.center)
                            .frame(width: 56)
                            .onChange(of: emoji) { _, value in if value.count > 1 { emoji = String(value.suffix(1)) } }
                        TextField("Name", text: $name).labbText(.lg, weight: LabbWeight.semibold)
                    }
                    TextField("What it's for", text: $description, axis: .vertical)
                }
                Section {
                    TextField("How it should answer, what it knows, what to do and not do", text: $instructions, axis: .vertical)
                        .lineLimit(6 ... 20)
                } header: {
                    Text("Instructions")
                }
                Section("Model") {
                    Picker("Model", selection: $model) {
                        Text("The server's default").tag(String?.none)
                        ForEach(app.models?.models ?? []) { model in Text(model.name).tag(Optional(model.spec)) }
                    }
                }
                Section {
                    Toggle("All tools", isOn: $allTools)
                    if !allTools {
                        ForEach(tools) { tool in
                            Toggle(isOn: Binding(get: { toolsets.contains(tool.name) }, set: { on in
                                if on { toolsets.insert(tool.name) } else { toolsets.remove(tool.name) }
                            })) {
                                VStack(alignment: .leading, spacing: LabbSpace.s1) {
                                    Text(tool.label)
                                    Text(tool.description).labbText(.sm).foregroundStyle(.labb.muted)
                                }
                            }
                        }
                    }
                    Toggle("Memories", isOn: $memories)
                } header: {
                    Text("Tools")
                } footer: {
                    Text("With memories on, it sees what you asked labb to remember, and can remember more.")
                }
                Section {
                    ForEach(starters.indices, id: \.self) { index in
                        TextField("Something to ask", text: $starters[index])
                    }
                    .onDelete { starters.remove(atOffsets: $0) }
                    Button("Add a starter", systemImage: "plus") { starters.append("") }
                } header: {
                    Text("Starters")
                } footer: {
                    Text("Shown in a new chat with it, to tap and send.")
                }
                Section {
                    Toggle("Share with everyone on the server", isOn: $shared)
                }
            }
            .navigationTitle(isNew ? "New agent" : "Edit agent")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isNew ? "Create" : "Save", action: save)
                        .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty || saving)
                }
            }
            .errorAlert(errors)
            .task {
                load()
                if let api = app.api, let list: [ToolChoice] = try? await api.get("/agents/tools") { tools = list }
            }
        }
    }

    private var isNew: Bool { if case .new = target { true } else { false } }

    private func load() {
        guard case .edit(let agent) = target else { return }
        name = agent.name
        emoji = agent.emoji
        description = agent.description
        instructions = agent.instructions
        model = agent.model
        allTools = agent.toolsets == nil
        toolsets = Set(agent.toolsets ?? [])
        memories = agent.memories
        starters = agent.starters
        shared = agent.shared
    }

    private func save() {
        saving = true
        var fields: [String: JSONValue] = [
            "name": .string(name.trimmingCharacters(in: .whitespaces)),
            "emoji": .string(emoji.isEmpty ? "🤖" : emoji),
            "description": .string(description),
            "instructions": .string(instructions),
            "model": .optional(model),
            "memories": .bool(memories),
            "starters": .strings(starters.map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }),
            "shared": .bool(shared),
        ]
        fields["toolsets"] = allTools ? .null : .strings(toolsets.sorted())
        Task {
            await errors.run {
                guard let api = app.api else { return }
                switch target {
                case .new: let _: Agent = try await api.send("POST", "/agents", body: .object(fields))
                case .edit(let agent): let _: Agent = try await api.send("PATCH", "/agents/\(agent.id)", body: .object(fields))
                }
                await app.refreshAgents()
                dismiss()
            }
            saving = false
        }
    }
}
