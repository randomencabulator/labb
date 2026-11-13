import LabbDesign
import SwiftUI

/// The Tasks tab: prompts that run on a schedule, each in a chat of its own
struct TasksView: View {
    @Environment(AppModel.self) private var app
    @State private var tasks: [LabbTask] = []
    @State private var loaded = false
    @State private var editing: TaskEditTarget?
    @State private var errors = ErrorAlert()

    var body: some View {
        NavigationStack {
            List {
                ForEach(tasks) { task in
                    NavigationLink(value: task) { TaskRow(task: task) }
                        .swipeActions(edge: .leading) {
                            Button("Run now", systemImage: "play.fill") { run(task) }.tint(.labb.accentFill)
                        }
                        .swipeActions(edge: .trailing) {
                            Button("Delete", systemImage: "trash", role: .destructive) { delete(task) }
                        }
                }
            }
            .listStyle(.insetGrouped)
            .labbList()
            .overlay {
                if loaded && tasks.isEmpty {
                    ContentUnavailableView {
                        Label("No tasks yet", systemImage: "clock")
                    } description: {
                        Text("A task sends a prompt on a schedule: a morning summary, a weekly check, a reminder.")
                    } actions: {
                        Button("New task") { editing = .new }.buttonStyle(.labb(.primary))
                    }
                }
            }
            .refreshable { await load() }
            .navigationTitle("Tasks")
            .toolbar {
                ToolbarItem(placement: .primaryAction) { Button("New task", systemImage: "plus") { editing = .new } }
            }
            .navigationDestination(for: LabbTask.self) { task in
                TaskDetailView(task: task, onChange: { Task { await load() } })
            }
            .sheet(item: $editing, onDismiss: { Task { await load() } }) { target in TaskEditor(target: target) }
            .errorAlert(errors)
            .task { await load() }
        }
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { tasks = try await api.get("/tasks") }
        loaded = true
    }

    private func run(_ task: LabbTask) {
        Task {
            await errors.run {
                let _: TaskRun = try await app.api!.send("POST", "/tasks/\(task.id)/run")
                await load()
            }
        }
    }

    private func delete(_ task: LabbTask) {
        Task {
            await errors.run {
                try await app.api?.perform("DELETE", "/tasks/\(task.id)")
                await load()
            }
        }
    }
}

private struct TaskRow: View {
    let task: LabbTask

    var body: some View {
        VStack(alignment: .leading, spacing: LabbSpace.s2) {
            HStack {
                Text(task.name).labbText(.md, weight: LabbWeight.semibold).opacity(task.enabled ? 1 : 0.55)
                Spacer()
                if let status = task.lastRun?.status { StatusBadge(status: status) }
            }
            Text(task.when).labbText(.sm).foregroundStyle(.labb.muted)
            if !task.enabled {
                Text(task.disabledReason ?? "Off").labbText(.xs).foregroundStyle(task.disabledReason == nil ? Color.labb.muted : Color.labb.error)
            } else if let next = task.nextRun {
                Text("Next: \(shortTime(next))").labbText(.xs).foregroundStyle(.labb.muted)
            }
        }
        .padding(.vertical, LabbSpace.s1)
    }
}

enum TaskEditTarget: Identifiable {
    case new
    case edit(LabbTask)

    var id: String {
        switch self {
        case .new: "new"
        case .edit(let task): task.id
        }
    }
}

/// A task: when it runs, and what its runs did
private struct TaskDetailView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @State var task: LabbTask
    let onChange: () -> Void
    @State private var runs: [TaskRun] = []
    @State private var editing: TaskEditTarget?
    @State private var errors = ErrorAlert()

    var body: some View {
        List {
            Section {
                Toggle("On", isOn: Binding(get: { task.enabled }, set: { setEnabled($0) }))
                LabeledContent("When", value: task.when)
                if let next = task.nextRun, task.enabled { LabeledContent("Next run", value: shortTime(next)) }
                LabeledContent("With", value: app.agent(task.agentId).map { "\($0.emoji) \($0.name)" } ?? "labb")
                LabeledContent("Runs in", value: task.mode == "same" ? "The same chat" : "A new chat each time")
                if let reason = task.disabledReason { Text(reason).foregroundStyle(.labb.error) }
            }
            Section("Prompt") { Text(task.prompt).textSelection(.enabled) }
            Section {
                Button {
                    runNow()
                } label: {
                    Label("Run now", systemImage: "play.fill").frame(maxWidth: .infinity)
                }
                .buttonStyle(.labb(.primary))
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets())
            }
            Section("Runs") {
                if runs.isEmpty { Text("It hasn't run yet.").foregroundStyle(.labb.muted) }
                ForEach(runs) { run in
                    Button {
                        if let thread = run.threadId { app.openChatRequest = thread }
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: LabbSpace.s1) {
                                Text(shortTime(run.startedAt ?? run.queuedAt)).foregroundStyle(.labb.text)
                                Text(run.trigger == "manual" ? "Run by hand" : "On schedule").labbText(.xs).foregroundStyle(.labb.muted)
                                if let error = run.error { Text(error).labbText(.xs).foregroundStyle(.labb.error) }
                            }
                            Spacer()
                            StatusBadge(status: run.status)
                            if run.status == "running" {
                                Button("Stop") { stop(run) }.buttonStyle(.labb(.danger))
                            }
                        }
                    }
                    .disabled(run.threadId == nil)
                }
            }
        }
        .labbList()
        .navigationTitle(task.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) { Button("Edit") { editing = .edit(task) } }
        }
        .sheet(item: $editing, onDismiss: { Task { await reload() } }) { target in TaskEditor(target: target) }
        .refreshable { await reload() }
        .errorAlert(errors)
        .task { await reload() }
    }

    private func reload() async {
        guard let api = app.api else { return }
        if let tasks: [LabbTask] = try? await api.get("/tasks"), let fresh = tasks.first(where: { $0.id == task.id }) { task = fresh }
        if let list: [TaskRun] = try? await api.get("/tasks/\(task.id)/runs") { runs = list }
        onChange()
    }

    private func setEnabled(_ on: Bool) {
        Task {
            await errors.run {
                task = try await app.api!.send("PATCH", "/tasks/\(task.id)", body: ["enabled": .bool(on)])
                onChange()
            }
        }
    }

    private func runNow() {
        Task {
            await errors.run {
                let _: TaskRun = try await app.api!.send("POST", "/tasks/\(task.id)/run")
                await reload()
            }
        }
    }

    private func stop(_ run: TaskRun) {
        Task {
            await errors.run {
                try await app.api?.perform("POST", "/tasks/runs/\(run.id)/stop")
                await reload()
            }
        }
    }
}

/// Making or changing a task, with its schedule built from pickers
private struct TaskEditor: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let target: TaskEditTarget

    @State private var name = ""
    @State private var prompt = ""
    @State private var agentId: String?
    @State private var kind: Schedule.Kind = .daily
    @State private var at = Date.now.addingTimeInterval(3600)
    @State private var time = Calendar.current.date(bySettingHour: 8, minute: 0, second: 0, of: .now) ?? .now
    @State private var every = 1
    @State private var weekdays: Set<Int> = [0]
    @State private var day = 1
    @State private var sameChat = false
    @State private var enabled = true
    @State private var preview: SchedulePreview?
    @State private var saving = false
    @State private var errors = ErrorAlert()

    private static let dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    /// A "once" task's time: wall-clock time in the task's time zone, without a zone ("2026-10-25T08:00")
    private static let localMinute: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm"
        return formatter
    }()

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name", text: $name)
                    TextField("What to ask, each time it runs", text: $prompt, axis: .vertical).lineLimit(4 ... 12)
                    Picker("With", selection: $agentId) {
                        Text("labb").tag(String?.none)
                        ForEach(app.agents) { agent in Text("\(agent.emoji) \(agent.name)").tag(Optional(agent.id)) }
                    }
                }
                Section {
                    Picker("Repeat", selection: $kind) {
                        ForEach(Schedule.Kind.allCases) { kind in Text(kind.label).tag(kind) }
                    }
                    switch kind {
                    case .once:
                        DatePicker("At", selection: $at, in: Date.now...)
                    case .minutes, .hours:
                        Stepper("Every \(every) \(kind == .minutes ? "minute" : "hour")\(every == 1 ? "" : "s")", value: $every,
                                in: kind == .minutes ? 5 ... 1440 : 1 ... 168, step: kind == .minutes ? 5 : 1)
                    case .daily, .weekdays:
                        DatePicker("At", selection: $time, displayedComponents: .hourAndMinute)
                    case .weekly:
                        HStack(spacing: LabbSpace.s2) {
                            ForEach(0 ..< 7, id: \.self) { index in
                                let on = weekdays.contains(index)
                                Button(Self.dayNames[index]) {
                                    if on { weekdays.remove(index) } else { weekdays.insert(index) }
                                }
                                .labbText(.sm, weight: LabbWeight.semibold)
                                .frame(maxWidth: .infinity, minHeight: 36)
                                .foregroundStyle(on ? Color.labb.onAccent : Color.labb.text)
                                .background(on ? Color.labb.accentFill : Color.labb.active, in: RoundedRectangle(cornerRadius: LabbRadius.md))
                                .buttonStyle(.plain)
                            }
                        }
                        DatePicker("At", selection: $time, displayedComponents: .hourAndMinute)
                    case .days:
                        Stepper("Every \(every) day\(every == 1 ? "" : "s")", value: $every, in: 1 ... 365)
                        DatePicker("At", selection: $time, displayedComponents: .hourAndMinute)
                    case .monthly:
                        Stepper("On day \(day)", value: $day, in: 1 ... 31)
                        DatePicker("At", selection: $time, displayedComponents: .hourAndMinute)
                    }
                } header: {
                    Text("When")
                } footer: {
                    if let preview {
                        VStack(alignment: .leading, spacing: LabbSpace.s1) {
                            Text(preview.when)
                            if !preview.next.isEmpty {
                                Text("Next: " + preview.next.prefix(3).map(shortTime).joined(separator: ", "))
                            }
                        }
                    } else {
                        Text("In \(TimeZone.current.identifier).")
                    }
                }
                Section {
                    Toggle("Run in the same chat each time", isOn: $sameChat)
                    Toggle("On", isOn: $enabled)
                } footer: {
                    Text(sameChat ? "Each run adds to one chat, so it can see what earlier runs said." : "Each run starts a chat of its own.")
                }
            }
            .navigationTitle(isNew ? "New task" : "Edit task")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isNew ? "Create" : "Save", action: save).disabled(!valid || saving)
                }
            }
            .errorAlert(errors)
            .onAppear(perform: load)
            .task(id: scheduleKey) { await updatePreview() }
        }
    }

    private var isNew: Bool { if case .new = target { true } else { false } }

    private var valid: Bool {
        !name.trimmingCharacters(in: .whitespaces).isEmpty && !prompt.trimmingCharacters(in: .whitespaces).isEmpty
            && (kind != .weekly || !weekdays.isEmpty)
    }

    private var timeText: String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: time)
    }

    private var schedule: Schedule {
        Schedule(kind: kind, at: Self.localMinute.string(from: at), every: every, time: timeText, days: weekdays.sorted(), day: day)
    }

    private var scheduleKey: String { schedule.json.summary }

    private func load() {
        guard case .edit(let task) = target else { return }
        name = task.name
        prompt = task.prompt
        agentId = task.agentId
        kind = task.schedule.kind
        every = task.schedule.every ?? 1
        weekdays = Set(task.schedule.days ?? [0])
        day = task.schedule.day ?? 1
        sameChat = task.mode == "same"
        enabled = task.enabled
        if let text = task.schedule.time {
            let parts = text.split(separator: ":").compactMap { Int($0) }
            if parts.count >= 2 { time = Calendar.current.date(bySettingHour: parts[0], minute: parts[1], second: 0, of: .now) ?? time }
        }
        if let text = task.schedule.at, let date = Self.localMinute.date(from: String(text.prefix(16))) { at = date }
    }

    private func updatePreview() async {
        guard let api = app.api else { return }
        try? await Task.sleep(for: .milliseconds(300))
        preview = try? await api.send("POST", "/tasks/preview", body: ["schedule": schedule.json, "tz": .string(TimeZone.current.identifier)])
    }

    private func save() {
        saving = true
        let fields: JSONValue = [
            "name": .string(name.trimmingCharacters(in: .whitespaces)),
            "prompt": .string(prompt),
            "agent_id": .optional(agentId),
            "schedule": schedule.json,
            "tz": .string(TimeZone.current.identifier),
            "mode": .string(sameChat ? "same" : "new"),
            "enabled": .bool(enabled),
        ]
        Task {
            await errors.run {
                guard let api = app.api else { return }
                switch target {
                case .new: let _: LabbTask = try await api.send("POST", "/tasks", body: fields)
                case .edit(let task): let _: LabbTask = try await api.send("PATCH", "/tasks/\(task.id)", body: fields)
                }
                dismiss()
            }
            saving = false
        }
    }
}
