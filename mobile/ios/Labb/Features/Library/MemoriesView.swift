import LabbDesign
import SwiftUI
import UniformTypeIdentifiers

struct MemoriesView: View {
    @Environment(AppModel.self) private var app
    @State private var memories: [Memory] = []
    @State private var loaded = false
    @State private var editing: MemoryEdit?
    @State private var forgettingAll = false
    @State private var errors = ErrorAlert()

    struct MemoryEdit: Identifiable {
        var id: String { memory?.id ?? "new" }
        let memory: Memory?
    }

    var body: some View {
        List {
            let toReview = memories.filter { $0.reviewed == false }
            if !toReview.isEmpty {
                Section {
                    ForEach(toReview) { row($0) }
                } header: {
                    Text("To review")
                } footer: {
                    Text("Saved by labb on its own. Keep them, or delete what's wrong.")
                }
            }
            let pinned = memories.filter { $0.pinned && $0.reviewed != false }
            if !pinned.isEmpty {
                Section {
                    ForEach(pinned) { row($0) }
                } header: {
                    Text("Pinned")
                } footer: {
                    Text("Pinned memories are in every conversation.")
                }
            }
            let others = memories.filter { !$0.pinned && $0.reviewed != false }
            if !others.isEmpty {
                Section {
                    ForEach(others) { row($0) }
                } header: {
                    Text("Recalled when relevant")
                }
            }
        }
        .labbList()
        .overlay {
            if loaded && memories.isEmpty {
                ContentUnavailableView {
                    Label("No memories", systemImage: "brain")
                } description: {
                    Text("Tell labb to remember something, or add it here.")
                } actions: {
                    Button("Add a memory") { editing = MemoryEdit(memory: nil) }.buttonStyle(.labb(.primary))
                }
            }
        }
        .navigationTitle("Memories")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Menu {
                    Button("Add a memory", systemImage: "plus") { editing = MemoryEdit(memory: nil) }
                    if !memories.isEmpty {
                        Button("Forget everything", systemImage: "trash", role: .destructive) { forgettingAll = true }
                    }
                } label: {
                    Label("Add", systemImage: "plus")
                } primaryAction: {
                    editing = MemoryEdit(memory: nil)
                }
            }
        }
        .sheet(item: $editing, onDismiss: { Task { await load() } }) { edit in MemoryEditor(memory: edit.memory) }
        .confirmationDialog("Forget all \(memories.count) memories?", isPresented: $forgettingAll, titleVisibility: .visible) {
            Button("Forget everything", role: .destructive, action: forgetAll)
        } message: {
            Text("This can't be undone.")
        }
        .refreshable { await load() }
        .errorAlert(errors)
        .task { await load() }
    }

    private func row(_ memory: Memory) -> some View {
        Button { editing = MemoryEdit(memory: memory) } label: {
            HStack(alignment: .top, spacing: LabbSpace.s4) {
                if memory.pinned { Image(systemName: "pin.fill").foregroundStyle(.labb.accent).font(.caption) }
                Text(memory.text).foregroundStyle(.labb.text).multilineTextAlignment(.leading)
            }
        }
        .swipeActions(edge: .trailing) {
            Button("Delete", systemImage: "trash", role: .destructive) { change(memory, delete: true) }
        }
        .swipeActions(edge: .leading) {
            if memory.reviewed == false {
                Button("Keep", systemImage: "checkmark") { change(memory, fields: ["reviewed": true]) }.tint(.labb.series3)
            }
            Button(memory.pinned ? "Unpin" : "Pin", systemImage: memory.pinned ? "pin.slash" : "pin") {
                change(memory, fields: ["pinned": .bool(!memory.pinned)])
            }
            .tint(.labb.accentFill)
        }
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { memories = try await api.get("/memories") }
        loaded = true
    }

    private func change(_ memory: Memory, fields: [String: JSONValue] = [:], delete: Bool = false) {
        Task {
            await errors.run {
                if delete {
                    try await app.api?.perform("DELETE", "/memories/\(memory.id)")
                } else {
                    let _: Memory = try await app.api!.send("PATCH", "/memories/\(memory.id)", body: .object(fields))
                }
            }
            await load()
        }
    }

    private func forgetAll() {
        Task {
            await errors.run { try await app.api?.perform("DELETE", "/memories") }
            await load()
        }
    }
}

private struct MemoryEditor: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let memory: Memory?
    @State private var text = ""
    @State private var pinned = false
    @State private var errors = ErrorAlert()

    var body: some View {
        NavigationStack {
            Form {
                TextField("Something labb should remember", text: $text, axis: .vertical).lineLimit(3 ... 10)
                Toggle("Pinned: in every conversation", isOn: $pinned)
                if let memory, memory.reviewed == false {
                    Text("labb saved this on its own. Saving it here keeps it.").labbText(.sm).foregroundStyle(.labb.muted)
                }
            }
            .navigationTitle(memory == nil ? "New memory" : "Memory")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save", action: save).disabled(text.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .errorAlert(errors)
            .onAppear {
                text = memory?.text ?? ""
                pinned = memory?.pinned ?? false
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func save() {
        Task {
            await errors.run {
                guard let api = app.api else { return }
                let body: JSONValue = ["text": .string(text), "pinned": .bool(pinned)]
                if let memory {
                    let _: Memory = try await api.send("PATCH", "/memories/\(memory.id)", body: ["text": .string(text), "pinned": .bool(pinned), "reviewed": true])
                } else {
                    let _: Memory = try await api.send("POST", "/memories", body: body)
                }
                dismiss()
            }
        }
    }
}

