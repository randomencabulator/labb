import LabbDesign
import SwiftUI
import UniformTypeIdentifiers

/// The Library tab: what labb knows about you and can use
struct LibraryView: View {
    var body: some View {
        NavigationStack {
            List {
                NavigationLink { DocumentsView() } label: {
                    LibraryRow(title: "Documents", detail: "Files labb can search", systemImage: "doc.text.magnifyingglass")
                }
                NavigationLink { MemoriesView() } label: {
                    LibraryRow(title: "Memories", detail: "What labb remembers about you", systemImage: "brain")
                }
                NavigationLink { NotesView() } label: {
                    LibraryRow(title: "Notes", detail: "labb's notes to itself", systemImage: "note.text")
                }
                NavigationLink { SkillsView() } label: {
                    LibraryRow(title: "Skills", detail: "Instructions for kinds of tasks", systemImage: "graduationcap")
                }
            }
            .listStyle(.insetGrouped)
            .labbList()
            .navigationTitle("Library")
        }
    }
}

struct LibraryRow: View {
    let title: String
    let detail: String
    let systemImage: String

    var body: some View {
        HStack(spacing: LabbSpace.s6) {
            Image(systemName: systemImage)
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(.labb.onAccent)
                .frame(width: 32, height: 32)
                .background(Color.labb.accentFill, in: RoundedRectangle(cornerRadius: LabbRadius.md))
            VStack(alignment: .leading, spacing: LabbSpace.s1) {
                Text(title).labbText(.md, weight: LabbWeight.semibold)
                Text(detail).labbText(.sm).foregroundStyle(.labb.muted)
            }
        }
        .padding(.vertical, LabbSpace.s1)
    }
}
