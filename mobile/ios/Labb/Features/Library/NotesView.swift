import LabbDesign
import SwiftUI
import UniformTypeIdentifiers

struct NotesView: View {
    @Environment(AppModel.self) private var app
    @State private var notes: Notes?
    @State private var adding = false
    @State private var editingAll = false
    @State private var errors = ErrorAlert()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: LabbSpace.s8) {
                if let notes {
                    if notes.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        ContentUnavailableView("No notes yet", systemImage: "note.text",
                                               description: Text("labb keeps notes here as it works: what it learned, what to do next."))
                    } else {
                        MarkdownText(text: notes.text)
                    }
                } else {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
            .padding(LabbSpace.s8)
        }
        .background(Color.labb.bg)
        .navigationTitle("Notes")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Menu {
                    Button("Add an entry", systemImage: "plus") { adding = true }
                    Button("Edit all", systemImage: "pencil") { editingAll = true }
                } label: {
                    Label("Edit", systemImage: "square.and.pencil")
                } primaryAction: {
                    adding = true
                }
                .disabled(notes == nil)
            }
        }
        .sheet(isPresented: $adding) { NoteEntryEditor(topics: notes?.topics ?? []) { change($0) } }
        .sheet(isPresented: $editingAll) { NotesTextEditor(text: notes?.text ?? "") { change($0) } }
        .refreshable { await load() }
        .errorAlert(errors)
        .task { await load() }
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { notes = try await api.get("/notes") }
    }

    /// Add an entry, or replace everything; the notes must still be what we read (`base`)
    private func change(_ change: [String: JSONValue]) {
        guard let notes else { return }
        var body = change
        body["base"] = .string(notes.digest)
        Task {
            await errors.run { self.notes = try await app.api!.send("POST", "/notes", body: .object(body)) }
        }
    }
}

private struct NoteEntryEditor: View {
    @Environment(\.dismiss) private var dismiss
    let topics: [String]
    let onSave: ([String: JSONValue]) -> Void
    @State private var topic = ""
    @State private var text = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Topic", text: $topic)
                    if !topics.isEmpty {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack {
                                ForEach(topics, id: \.self) { name in
                                    Button(name) { topic = name }.buttonStyle(.labb(.ghost))
                                }
                            }
                        }
                    }
                } footer: {
                    Text("Entries go under a heading for their topic.")
                }
                TextField("The note", text: $text, axis: .vertical).lineLimit(4 ... 12)
            }
            .navigationTitle("Add a note")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") {
                        var change: [String: JSONValue] = ["action": "add", "text": .string(text)]
                        if !topic.trimmingCharacters(in: .whitespaces).isEmpty { change["topic"] = .string(topic) }
                        onSave(change)
                        dismiss()
                    }
                    .disabled(text.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
    }
}

private struct NotesTextEditor: View {
    @Environment(\.dismiss) private var dismiss
    @State var text: String
    let onSave: ([String: JSONValue]) -> Void

    var body: some View {
        NavigationStack {
            TextEditor(text: $text)
                .labbText(.sm, design: LabbFontDesign.mono)
                .scrollContentBackground(.hidden)
                .padding(LabbSpace.s6)
                .background(Color.labb.bg)
                .navigationTitle("Notes")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Save") {
                            onSave(["action": "replace", "text": .string(text)])
                            dismiss()
                        }
                    }
                }
        }
    }
}

