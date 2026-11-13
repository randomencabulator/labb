import LabbDesign
import SwiftUI
import UniformTypeIdentifiers

struct DocumentsView: View {
    @Environment(AppModel.self) private var app
    @State private var documents: [DocumentInfo] = []
    @State private var loaded = false
    @State private var importing = false
    @State private var uploading = 0
    @State private var errors = ErrorAlert()

    var body: some View {
        List {
            if uploading > 0 {
                HStack(spacing: LabbSpace.s4) {
                    ProgressView()
                    Text("Uploading \(uploading) file\(uploading == 1 ? "" : "s")…").foregroundStyle(.labb.muted)
                }
            }
            ForEach(documents) { document in
                NavigationLink { DocumentTextView(document: document) } label: {
                    VStack(alignment: .leading, spacing: LabbSpace.s1) {
                        Text(document.name).labbText(.md, weight: LabbWeight.medium).lineLimit(2)
                        HStack(spacing: LabbSpace.s3) {
                            StatusBadge(status: document.status)
                            Text(details(document))
                        }
                        .labbText(.xs)
                        .foregroundStyle(.labb.muted)
                        if let error = document.error { Text(error).labbText(.xs).foregroundStyle(.labb.error) }
                    }
                }
                .disabled(document.status != "ready")
            }
            .onDelete(perform: delete)
        }
        .labbList()
        .overlay {
            if loaded && documents.isEmpty && uploading == 0 {
                ContentUnavailableView {
                    Label("No documents", systemImage: "doc.text.magnifyingglass")
                } description: {
                    Text("Add PDFs and text files, and labb searches them when you ask about them.")
                } actions: {
                    Button("Add documents") { importing = true }.buttonStyle(.labb(.primary))
                }
            }
        }
        .navigationTitle("Documents")
        .toolbar {
            ToolbarItem(placement: .primaryAction) { Button("Add", systemImage: "plus") { importing = true } }
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.pdf, .plainText, .text, .json, .commaSeparatedText, .html, .xml, .audio],
                      allowsMultipleSelection: true) { result in
            if case .success(let urls) = result { upload(urls) }
        }
        .refreshable { await load() }
        .errorAlert(errors)
        .task {
            await load()
            // While something is being indexed, look again now and then
            while !Task.isCancelled, documents.contains(where: { $0.status == "indexing" }) {
                try? await Task.sleep(for: .seconds(3))
                await load()
            }
        }
    }

    private func details(_ document: DocumentInfo) -> String {
        var parts = [byteCount(document.bytes)]
        if let pages = document.pages { parts.append("\(pages) page\(pages == 1 ? "" : "s")") }
        parts.append(relativeTime(document.createdAt))
        return parts.joined(separator: " · ")
    }

    private func load() async {
        guard let api = app.api else { return }
        await errors.run { documents = try await api.get("/documents") }
        loaded = true
    }

    private func upload(_ urls: [URL]) {
        uploading += urls.count
        Task {
            for url in urls {
                await errors.run {
                    let access = url.startAccessingSecurityScopedResource()
                    defer { if access { url.stopAccessingSecurityScopedResource() } }
                    let data = try Data(contentsOf: url)
                    let _: DocumentInfo = try await app.api!.send("POST", "/documents", body: [
                        "name": .string(url.lastPathComponent), "data": .string(data.base64EncodedString()),
                    ])
                }
                uploading -= 1
                await load()
            }
            while documents.contains(where: { $0.status == "indexing" }) {
                try? await Task.sleep(for: .seconds(3))
                await load()
            }
        }
    }

    private func delete(_ offsets: IndexSet) {
        let gone = offsets.map { documents[$0] }
        documents.remove(atOffsets: offsets)
        Task {
            for document in gone {
                await errors.run { try await app.api?.perform("DELETE", "/documents/\(document.id)") }
            }
            await load()
        }
    }
}

private struct DocumentTextView: View {
    @Environment(AppModel.self) private var app
    let document: DocumentInfo
    @State private var text: DocumentText?
    @State private var problem: String?

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: LabbSpace.s12) {
                if let text {
                    ForEach(Array(text.pages.enumerated()), id: \.offset) { index, page in
                        VStack(alignment: .leading, spacing: LabbSpace.s4) {
                            if text.paged { Text("Page \(index + 1)").labbLabel() }
                            Text(page).labbText(.sm).textSelection(.enabled)
                        }
                    }
                } else if let problem {
                    Text(problem).foregroundStyle(.labb.muted)
                } else {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
            .padding(LabbSpace.s8)
        }
        .background(Color.labb.bg)
        .navigationTitle(document.name)
        .navigationBarTitleDisplayMode(.inline)
        .task {
            do {
                text = try await app.api?.get("/documents/\(document.id)/text")
            } catch {
                problem = error.localizedDescription
            }
        }
    }
}

