import LabbDesign
import SwiftUI

/// Studio on the phone: run workflows, follow their runs, and answer what waits for approval.
/// Building and editing workflows is on the web, where there's room for the canvas.
struct StudioView: View {
    @Environment(AppModel.self) private var app
    @State private var workflows: [WorkflowSummary] = []
    @State private var runs: [WorkflowRun] = []
    @State private var waiting: [WaitingApproval] = []
    @State private var loaded = false
    @State private var errors = ErrorAlert()

    var body: some View {
        List {
            if !waiting.isEmpty {
                Section("Waiting for you") {
                    ForEach(waiting) { approval in ApprovalRow(approval: approval) { await load() } }
                }
            }
            Section("Workflows") {
                if loaded && workflows.isEmpty {
                    Text("No workflows yet. Build them in Studio on the web.").foregroundStyle(.labb.muted)
                }
                ForEach(workflows) { workflow in
                    NavigationLink { WorkflowView(workflow: workflow) } label: {
                        HStack(spacing: LabbSpace.s6) {
                            Text(workflow.emoji).font(.title2)
                            VStack(alignment: .leading, spacing: LabbSpace.s1) {
                                Text(workflow.name).labbText(.md, weight: LabbWeight.semibold)
                                Text(workflow.description.isEmpty ? "\(workflow.steps) steps" : workflow.description)
                                    .labbText(.sm).foregroundStyle(.labb.muted).lineLimit(2)
                            }
                        }
                    }
                }
            }
            if !runs.isEmpty {
                Section("Recent runs") {
                    ForEach(runs.prefix(20)) { run in
                        NavigationLink { RunView(runId: run.id) } label: { RunRow(run: run) }
                    }
                }
            }
        }
        .labbList()
        .navigationTitle("Studio")
        .refreshable { await load() }
        .errorAlert(errors)
        .task { await load() }
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run {
            async let workflows: [WorkflowSummary] = api.get("/studio/workflows")
            async let runs: [WorkflowRun] = api.get("/studio/runs")
            async let waiting: [WaitingApproval] = api.get("/studio/waiting")
            (self.workflows, self.runs, self.waiting) = try await (workflows, runs, waiting)
        }
        loaded = true
    }
}

private struct RunRow: View {
    let run: WorkflowRun

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: LabbSpace.s1) {
                Text("\(run.emoji ?? "") \(run.workflow ?? "Run")").labbText(.md, weight: LabbWeight.medium)
                Text("\(run.trigger) · \(relativeTime(run.startedAt ?? run.queuedAt))").labbText(.xs).foregroundStyle(.labb.muted)
            }
            Spacer()
            StatusBadge(status: run.status)
        }
    }
}

private struct ApprovalRow: View {
    @Environment(AppModel.self) private var app
    let approval: WaitingApproval
    let onAnswered: () async -> Void
    @State private var errors = ErrorAlert()

    var body: some View {
        VStack(alignment: .leading, spacing: LabbSpace.s4) {
            Text(approval.workflow).labbLabel()
            Text(approval.label).labbText(.md, weight: LabbWeight.semibold)
            if !approval.message.isEmpty { Text(approval.message).labbText(.sm) }
            HStack(spacing: LabbSpace.s6) {
                Button("Approve") { answer(true) }.buttonStyle(.labb(.primary))
                Button("Reject") { answer(false) }.buttonStyle(.labb(.danger))
            }
        }
        .padding(.vertical, LabbSpace.s3)
        .errorAlert(errors)
    }

    private func answer(_ approved: Bool) {
        Task {
            await errors.run {
                let _: WorkflowRun = try await app.api!.send("POST", "/studio/runs/\(approval.runId)/approve",
                                                             body: ["node_id": .string(approval.nodeId), "approved": .bool(approved), "comment": ""])
            }
            await onAnswered()
        }
    }
}

private struct WorkflowView: View {
    @Environment(AppModel.self) private var app
    let workflow: WorkflowSummary
    @State private var runs: [WorkflowRun] = []
    @State private var started: WorkflowRun?
    @State private var errors = ErrorAlert()

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: LabbSpace.s4) {
                    Text(workflow.emoji).font(.system(size: 44))
                    Text(workflow.name).labbHeading(.xxl)
                    if !workflow.description.isEmpty { Text(workflow.description).foregroundStyle(.labb.muted) }
                    Text("\(workflow.steps) steps" + (workflow.publishedVersion.map { " · version \($0)" } ?? " · not published"))
                        .labbText(.sm).foregroundStyle(.labb.muted)
                    if let next = workflow.nextRun { Text("Next run \(shortTime(next))").labbText(.sm).foregroundStyle(.labb.muted) }
                    Button {
                        run()
                    } label: {
                        Label("Run", systemImage: "play.fill").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.labb(.primary))
                    .padding(.top, LabbSpace.s4)
                }
                .listRowBackground(Color.clear)
            }
            Section("Runs") {
                if runs.isEmpty { Text("It hasn't run yet.").foregroundStyle(.labb.muted) }
                ForEach(runs) { run in
                    NavigationLink { RunView(runId: run.id) } label: { RunRow(run: run) }
                }
            }
        }
        .labbList()
        .navigationTitle(workflow.name)
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(item: $started) { run in RunView(runId: run.id) }
        .refreshable { await load() }
        .errorAlert(errors)
        .task { await load() }
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { runs = try await api.get("/studio/workflows/\(workflow.id)/runs") }
    }

    private func run() {
        Task {
            await errors.run {
                // Draft runs only when it was never published
                started = try await app.api!.send("POST", "/studio/workflows/\(workflow.id)/runs",
                                                  body: ["inputs": [:], "draft": .bool(workflow.publishedVersion == nil)])
            }
            await load()
        }
    }
}

/// A run: each step with what it did, updating while it runs
private struct RunView: View {
    @Environment(AppModel.self) private var app
    let runId: String
    @State private var run: WorkflowRun?
    @State private var errors = ErrorAlert()

    var body: some View {
        List {
            if let run {
                Section {
                    LabeledContent("Status") { StatusBadge(status: run.status) }
                    LabeledContent("Started", value: shortTime(run.startedAt ?? run.queuedAt))
                    if let finished = run.finishedAt { LabeledContent("Finished", value: shortTime(finished)) }
                    if let error = run.error { Text(error).foregroundStyle(.labb.error) }
                    if ["running", "queued", "waiting"].contains(run.status) {
                        Button("Stop the run", role: .destructive, action: stop)
                    }
                }
                Section("Steps") {
                    ForEach(run.steps ?? []) { step in
                        DisclosureGroup {
                            if let error = step.error { Text(error).foregroundStyle(.labb.error) }
                            if let output = step.output, output != .null {
                                Text(output.stringValue ?? output.pretty)
                                    .labbText(.xs, design: LabbFontDesign.mono)
                                    .textSelection(.enabled)
                            }
                        } label: {
                            HStack {
                                Text(step.key).labbText(.sm, design: LabbFontDesign.mono)
                                Spacer()
                                StatusBadge(status: step.status)
                            }
                        }
                    }
                }
                if let result = run.result, result != .null {
                    Section("Result") {
                        Text(result.stringValue ?? result.pretty).labbText(.sm, design: LabbFontDesign.mono).textSelection(.enabled)
                    }
                }
            } else {
                ProgressView().frame(maxWidth: .infinity)
            }
        }
        .labbList()
        .navigationTitle(run?.workflow ?? "Run")
        .navigationBarTitleDisplayMode(.inline)
        .errorAlert(errors)
        .task {
            await load()
            while !Task.isCancelled, let status = run?.status, ["running", "queued"].contains(status) {
                try? await Task.sleep(for: .seconds(2))
                await load()
            }
        }
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { run = try await api.get("/studio/runs/\(runId)") }
    }

    private func stop() {
        Task {
            await errors.run { try await app.api?.perform("POST", "/studio/runs/\(runId)/stop") }
            await load()
        }
    }
}

// MARK: - Engines

struct EnginesView: View {
    @Environment(AppModel.self) private var app
    @State private var list: EngineList?
    @State private var errors = ErrorAlert()

    var body: some View {
        List {
            if let list {
                Section {
                    ForEach(list.engines.filter { $0.webProblems.isEmpty }) { engine in
                        Button {
                            setDefault(engine.id)
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: LabbSpace.s1) {
                                    Text(engine.name).labbText(.md, weight: LabbWeight.medium).foregroundStyle(.labb.text)
                                    if !engine.description.isEmpty {
                                        Text(engine.description).labbText(.sm).foregroundStyle(.labb.muted).lineLimit(3)
                                    }
                                    Text(engine.builtIn ? "Comes with labb" : engine.mine ? "Yours · version \(engine.version)" : "By \(engine.owner ?? "someone")")
                                        .labbText(.xs).foregroundStyle(.labb.muted)
                                }
                                Spacer()
                                if isDefault(engine.id, list) { Image(systemName: "checkmark").foregroundStyle(.labb.accent) }
                            }
                        }
                    }
                } header: {
                    Text("Your default")
                } footer: {
                    Text("The engine your new chats use. Making and changing engines is on the web.")
                }
            } else {
                ProgressView().frame(maxWidth: .infinity)
            }
        }
        .labbList()
        .navigationTitle("Engines")
        .errorAlert(errors)
        .task { await load() }
    }

    private func isDefault(_ id: String, _ list: EngineList) -> Bool {
        (list.myDefault ?? list.serverDefault ?? "default") == id
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { list = try await api.get("/engines") }
    }

    private func setDefault(_ id: String) {
        Task {
            await errors.run { try await app.api?.perform("PUT", "/engines/my-default", body: ["engine_id": .string(id)]) }
            await load()
        }
    }
}
