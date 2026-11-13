import LabbDesign
import SwiftUI
import UniformTypeIdentifiers

struct SkillsView: View {
    @Environment(AppModel.self) private var app
    @State private var skills: [Skill] = []
    @State private var loaded = false
    @State private var creating = false
    @State private var errors = ErrorAlert()

    var body: some View {
        List {
            let mine = skills.filter(\.mine)
            let shared = skills.filter { !$0.mine }
            if !mine.isEmpty { Section("Yours") { ForEach(mine) { row($0) } } }
            if !shared.isEmpty { Section("Shared with everyone") { ForEach(shared) { row($0) } } }
        }
        .labbList()
        .overlay {
            if loaded && skills.isEmpty {
                ContentUnavailableView {
                    Label("No skills", systemImage: "graduationcap")
                } description: {
                    Text("A skill teaches labb a kind of task. Type /name in a message to use one on purpose.")
                } actions: {
                    Button("New skill") { creating = true }.buttonStyle(.labb(.primary))
                }
            }
        }
        .navigationTitle("Skills")
        .toolbar {
            ToolbarItem(placement: .primaryAction) { Button("New skill", systemImage: "plus") { creating = true } }
        }
        .sheet(isPresented: $creating, onDismiss: { Task { await load() } }) { SkillEditor(skill: nil) }
        .refreshable { await load() }
        .errorAlert(errors)
        .task { await load() }
    }

    private func row(_ skill: Skill) -> some View {
        NavigationLink { SkillDetailView(skillId: skill.id, onChange: { Task { await load() } }) } label: {
            VStack(alignment: .leading, spacing: LabbSpace.s1) {
                Text("/\(skill.name)").labbText(.md, weight: LabbWeight.semibold, design: LabbFontDesign.mono)
                Text(skill.description).labbText(.sm).foregroundStyle(.labb.muted).lineLimit(2)
            }
        }
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { skills = try await api.get("/skills") }
        loaded = true
    }
}

private struct SkillDetailView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let skillId: String
    let onChange: () -> Void
    @State private var skill: Skill?
    @State private var editing = false
    @State private var deleting = false
    @State private var errors = ErrorAlert()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: LabbSpace.s8) {
                if let skill {
                    VStack(alignment: .leading, spacing: LabbSpace.s3) {
                        Text(skill.description).foregroundStyle(.labb.muted)
                        if skill.files.count > 1 {
                            Text("Files: " + skill.files.joined(separator: ", ")).labbText(.xs).foregroundStyle(.labb.muted)
                        }
                        if skill.mine {
                            Toggle("Share with everyone on the server", isOn: Binding(get: { skill.shared }, set: { setShared($0) }))
                        } else if let owner = skill.owner {
                            Text("By \(owner)").labbText(.sm).foregroundStyle(.labb.muted)
                        }
                    }
                    Divider()
                    MarkdownText(text: skill.skillMd ?? "")
                } else {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
            .padding(LabbSpace.s8)
        }
        .background(Color.labb.bg)
        .navigationTitle(skill.map { "/\($0.name)" } ?? "Skill")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if skill?.mine == true {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Button("Edit", systemImage: "pencil") { editing = true }
                        Button("Delete", systemImage: "trash", role: .destructive) { deleting = true }
                    } label: {
                        Label("Options", systemImage: "ellipsis.circle")
                    }
                }
            }
        }
        .sheet(isPresented: $editing, onDismiss: { Task { await load() } }) { SkillEditor(skill: skill) }
        .confirmationDialog("Delete this skill?", isPresented: $deleting, titleVisibility: .visible) {
            Button("Delete", role: .destructive, action: delete)
        }
        .errorAlert(errors)
        .task { await load() }
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { skill = try await api.get("/skills/\(skillId)") }
        onChange()
    }

    private func setShared(_ shared: Bool) {
        Task {
            await errors.run { let _: Skill = try await app.api!.send("PATCH", "/skills/\(skillId)", body: ["shared": .bool(shared)]) }
            await load()
        }
    }

    private func delete() {
        Task {
            await errors.run {
                try await app.api?.perform("DELETE", "/skills/\(skillId)")
                onChange()
                dismiss()
            }
        }
    }
}

private struct SkillEditor: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let skill: Skill?
    @State private var text = ""
    @State private var errors = ErrorAlert()

    private static let template = """
    ---
    name: my-skill
    description: What it does, and when to use it.
    ---

    # My skill

    The steps to follow.
    """

    var body: some View {
        NavigationStack {
            TextEditor(text: $text)
                .labbText(.sm, design: LabbFontDesign.mono)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .scrollContentBackground(.hidden)
                .padding(LabbSpace.s6)
                .background(Color.labb.bg)
                .navigationTitle(skill == nil ? "New skill" : "Edit SKILL.md")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                    ToolbarItem(placement: .confirmationAction) { Button("Save", action: save) }
                }
                .errorAlert(errors)
                .onAppear { text = skill?.skillMd ?? Self.template }
        }
    }

    private func save() {
        Task {
            await errors.run {
                guard let api = app.api else { return }
                if let skill {
                    let _: Skill = try await api.send("PATCH", "/skills/\(skill.id)", body: ["skill_md": .string(text)])
                } else {
                    let _: Skill = try await api.send("POST", "/skills", body: ["skill_md": .string(text)])
                }
                dismiss()
            }
        }
    }
}
