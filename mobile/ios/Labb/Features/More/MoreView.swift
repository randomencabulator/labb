import Charts
import LabbDesign
import SwiftUI

/// The More tab: everything else, as a settings-style list
struct MoreView: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        NavigationStack {
            List {
                Section {
                    NavigationLink { PcsView() } label: {
                        LibraryRow(title: "PCs", detail: pcDetail, systemImage: "desktopcomputer")
                    }
                    NavigationLink { StudioView() } label: {
                        LibraryRow(title: "Studio", detail: "Workflows, their runs and approvals", systemImage: "point.3.connected.trianglepath.dotted")
                    }
                    NavigationLink { UsageView() } label: {
                        LibraryRow(title: "Usage", detail: "Replies, tokens and models", systemImage: "chart.bar")
                    }
                    NavigationLink { EnginesView() } label: {
                        LibraryRow(title: "Engines", detail: "How labb works through a reply", systemImage: "gearshape.2")
                    }
                }
                Section {
                    NavigationLink { SettingsView() } label: {
                        LibraryRow(title: "Settings", detail: app.user.map { "Signed in as \($0.username)" } ?? "", systemImage: "gearshape")
                    }
                    if app.user?.isAdmin == true {
                        NavigationLink { AdminView() } label: {
                            LibraryRow(title: "Server", detail: "People, invites, health and limits", systemImage: "server.rack")
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .labbList()
            .navigationTitle("More")
            .task { await app.refreshPcs() }
        }
    }

    private var pcDetail: String {
        let online = app.pcs.filter(\.online).count
        return app.pcs.isEmpty ? "Run labb on your computers from here" : "\(online) of \(app.pcs.count) online"
    }
}

// MARK: - PCs

struct PcsView: View {
    @Environment(AppModel.self) private var app
    @State private var forgetting: Pc?
    @State private var errors = ErrorAlert()

    var body: some View {
        List {
            ForEach(app.pcs) { pc in
                Section {
                    ForEach(pc.folders, id: \.self) { folder in
                        HStack {
                            VStack(alignment: .leading, spacing: LabbSpace.s1) {
                                Text((folder as NSString).lastPathComponent).labbText(.md, weight: LabbWeight.medium)
                                Text(folder).labbText(.xs, design: LabbFontDesign.mono).foregroundStyle(.labb.muted).lineLimit(1)
                            }
                            Spacer()
                            NavigationLink { PcNotesView(pc: pc, folder: folder) } label: { EmptyView() }.frame(width: 0).opacity(0)
                        }
                        .contentShape(Rectangle())
                        .swipeActions(edge: .leading) {
                            Button("New chat", systemImage: "bubble.left") { startChat(pc, folder) }.tint(.labb.accentFill)
                        }
                        .contextMenu {
                            Button("New chat here", systemImage: "bubble.left") { startChat(pc, folder) }
                        }
                        .onTapGesture { if pc.online { startChat(pc, folder) } }
                    }
                } header: {
                    HStack(spacing: LabbSpace.s3) {
                        Circle().fill(pc.online ? Color.labb.series3 : Color.labb.muted).frame(width: 8, height: 8)
                        Text(pc.name)
                        Spacer()
                        Menu {
                            Button("Forget this PC", systemImage: "trash", role: .destructive) { forgetting = pc }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                        }
                    }
                } footer: {
                    Text(pc.online ? "Online · labb \(pc.version ?? "")" + (pc.allowAll == true ? " · doesn't ask before acting" : "")
                         : "Offline · last seen \(relativeTime(pc.lastSeen))")
                }
            }
        }
        .labbList()
        .overlay {
            if app.pcs.isEmpty {
                ContentUnavailableView {
                    Label("No PCs", systemImage: "desktopcomputer")
                } description: {
                    Text("Run `labb connect` on a computer to work in its folders from here.")
                }
            }
        }
        .navigationTitle("PCs")
        .refreshable { await app.refreshPcs() }
        .confirmationDialog("Forget \(forgetting?.name ?? "this PC")?", isPresented: Binding(get: { forgetting != nil }, set: { if !$0 { forgetting = nil } }),
                            titleVisibility: .visible, presenting: forgetting) { pc in
            Button("Forget", role: .destructive) { forget(pc) }
        } message: { _ in
            Text("It can connect again with labb connect.")
        }
        .errorAlert(errors)
        .task { await app.refreshPcs() }
    }

    private func startChat(_ pc: Pc, _ folder: String) {
        guard pc.online else {
            errors.message = "\(pc.name) is offline. Start labb connect on it first."
            return
        }
        let session = app.chats.newSession(app: app, pc: PcTarget(pcId: pc.id, pcName: pc.name, folder: folder))
        app.pendingNewChat = session.id
    }

    private func forget(_ pc: Pc) {
        Task {
            await errors.run { try await app.api?.perform("DELETE", "/pcs/\(pc.id)") }
            await app.refreshPcs()
        }
    }
}

/// A PC folder's notes (its .labb/notes.md), asked of the PC
private struct PcNotesView: View {
    @Environment(AppModel.self) private var app
    let pc: Pc
    let folder: String
    @State private var notes: Notes?
    @State private var problem: String?

    var body: some View {
        ScrollView {
            Group {
                if let notes {
                    if notes.text.isEmpty { Text("No notes in this folder.").foregroundStyle(.labb.muted) } else { MarkdownText(text: notes.text) }
                } else if let problem {
                    Text(problem).foregroundStyle(.labb.muted)
                } else {
                    ProgressView()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(LabbSpace.s8)
        }
        .background(Color.labb.bg)
        .navigationTitle("Notes")
        .task {
            do {
                notes = try await app.api?.get("/pcs/\(pc.id)/notes", query: ["folder": folder])
            } catch {
                problem = error.localizedDescription
            }
        }
    }
}

// MARK: - Usage

struct UsageView: View {
    @Environment(AppModel.self) private var app
    @State private var range: Range = .month
    @State private var usage: Usage?
    @State private var errors = ErrorAlert()

    enum Range: String, CaseIterable, Identifiable {
        case week = "7 days", month = "30 days", all = "All time"
        var id: String { rawValue }

        var since: Date? {
            switch self {
            case .week: Calendar.current.date(byAdding: .day, value: -7, to: .now)
            case .month: Calendar.current.date(byAdding: .day, value: -30, to: .now)
            case .all: nil
            }
        }
    }

    var body: some View {
        List {
            Section {
                Picker("Range", selection: $range) {
                    ForEach(Range.allCases) { range in Text(range.rawValue).tag(range) }
                }
                .pickerStyle(.segmented)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())
            }
            if let usage {
                Section {
                    Grid(horizontalSpacing: LabbSpace.s6, verticalSpacing: LabbSpace.s6) {
                        GridRow {
                            Tile(label: "Replies", value: compactNumber(Double(usage.totals.replies)))
                            Tile(label: "Tokens", value: compactNumber(usage.totals.inputTokens + usage.totals.outputTokens))
                        }
                        GridRow {
                            Tile(label: "Tool calls", value: compactNumber(usage.totals.toolCalls))
                            Tile(label: "Chats", value: compactNumber(Double(usage.totals.conversations ?? 0)))
                        }
                    }
                    .listRowBackground(Color.clear)
                    .listRowInsets(EdgeInsets())
                }
                if !usage.byDay.isEmpty {
                    Section("Replies per day") {
                        Chart(usage.byDay, id: \.self) { day in
                            BarMark(x: .value("Day", day.day), y: .value("Replies", day.replies))
                                .foregroundStyle(by: .value("Where", day.source))
                        }
                        .chartForegroundStyleScale(range: [Color.labb.series1, Color.labb.series2, Color.labb.series3, Color.labb.series4, Color.labb.seriesOther])
                        .chartXAxis(.hidden)
                        .frame(height: 180)
                    }
                }
                if !usage.byModel.isEmpty {
                    Section("Models") {
                        ForEach(usage.byModel, id: \.self) { model in
                            LabeledContent {
                                Text("\(model.replies) · \(compactNumber(model.inputTokens + model.outputTokens)) tokens")
                            } label: {
                                VStack(alignment: .leading) {
                                    Text(app.modelName(model.model))
                                    if let speed = model.tokensPerSecond {
                                        Text("\(Int(speed)) tokens/s").labbText(.xs).foregroundStyle(.labb.muted)
                                    }
                                }
                            }
                        }
                    }
                }
                if !usage.tools.isEmpty {
                    Section("Tools") {
                        ForEach(usage.tools.prefix(10), id: \.self) { tool in
                            LabeledContent(tool.name, value: "\(tool.calls)")
                        }
                    }
                }
                if let top = usage.topConversations, !top.isEmpty {
                    Section("Busiest chats") {
                        ForEach(top, id: \.self) { conversation in
                            Button {
                                if conversation.exists { app.openChatRequest = conversation.threadId }
                            } label: {
                                LabeledContent(conversation.title.isEmpty ? "Chat" : conversation.title, value: "\(conversation.replies) replies")
                                    .foregroundStyle(.labb.text)
                            }
                            .disabled(!conversation.exists)
                        }
                    }
                }
            } else {
                ProgressView().frame(maxWidth: .infinity).listRowBackground(Color.clear)
            }
        }
        .labbList()
        .navigationTitle("Usage")
        .errorAlert(errors)
        .task(id: range) { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        guard let api = app.api else { return }
        // Minutes east of UTC, so days are local (the web's -getTimezoneOffset())
        var query = ["tz": String(TimeZone.current.secondsFromGMT() / 60)]
        if let since = range.since { query["since"] = since.formatted(.iso8601) }
        await errors.run { usage = try await api.get("/usage", query: query) }
    }
}

/// One number with a mono label
struct Tile: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: LabbSpace.s2) {
            Text(label).labbLabel()
            Text(value).labbText(.xxl, weight: LabbWeight.black, tracking: LabbTracking.heading).monospacedDigit()
        }
        .labbCard()
    }
}
