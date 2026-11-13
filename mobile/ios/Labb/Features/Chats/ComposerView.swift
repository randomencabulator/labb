import LabbDesign
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

/// The message box at the bottom of a chat: what to send, what's attached, and Send (or Stop)
struct ComposerView: View {
    @Environment(AppModel.self) private var app
    let session: ChatSession
    @Binding var text: String
    @Binding var attachments: [Attachment]
    @Binding var editing: Int?
    let onSend: () -> Void

    @State private var photos: [PhotosPickerItem] = []
    @State private var pickingPhotos = false
    @State private var importing = false
    @State private var problem: String?
    @FocusState private var focused: Bool

    /// The server's limits on attachments, as the web's composer has them
    private static let maxAttachments = 10
    private static let maxImageBytes = 5_000_000
    private static let maxTextChars = 200_000

    var body: some View {
        VStack(spacing: LabbSpace.s4) {
            if editing != nil {
                HStack {
                    Label("Editing your message: the reply after it is written again", systemImage: "pencil")
                        .labbText(.sm)
                        .foregroundStyle(.labb.muted)
                    Spacer()
                    Button("Cancel") {
                        editing = nil
                        text = ""
                    }
                    .labbText(.sm, weight: LabbWeight.semibold)
                }
                .padding(.horizontal, LabbSpace.s7)
                .padding(.vertical, LabbSpace.s4)
                .floatingGlass(in: Capsule())
            }
            if let problem {
                Label(problem, systemImage: "exclamationmark.triangle").labbText(.sm).foregroundStyle(.labb.error)
                    .padding(.horizontal, LabbSpace.s7)
                    .padding(.vertical, LabbSpace.s4)
                    .floatingGlass(in: Capsule())
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            if !attachments.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: LabbSpace.s4) {
                        ForEach(attachments) { attachment in
                            AttachmentChip(attachment: attachment) {
                                attachments.removeAll { $0.id == attachment.id }
                            }
                        }
                    }
                }
            }
            HStack(alignment: .bottom, spacing: LabbSpace.s4) {
                Menu {
                    Button("Photos", systemImage: "photo.on.rectangle") { pickingPhotos = true }
                    Button("Files", systemImage: "doc") { importing = true }
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 18, weight: .semibold))
                        .frame(width: 44, height: 44)
                        .foregroundStyle(.labb.text)
                        .floatingGlass(in: Circle())
                }
                .disabled(session.isTerminalSession || editing != nil)
                .accessibilityLabel("Attach")

                HStack(alignment: .bottom, spacing: LabbSpace.s2) {
                    TextField(placeholder, text: $text, axis: .vertical)
                        .lineLimit(1 ... 6)
                        .focused($focused)
                        .padding(.leading, LabbSpace.s7)
                        .padding(.vertical, LabbSpace.s5)
                        .frame(minHeight: 44)
                    // Inside the field, as in messaging apps: Stop while a reply comes in, Send once there's something to send
                    if session.streaming {
                        Button(action: session.stop) {
                            Image(systemName: session.stopping ? "hourglass" : "stop.fill")
                                .font(.system(size: 13, weight: .bold))
                                .frame(width: 34, height: 34)
                                .foregroundStyle(.labb.userText)
                                .background(Color.labb.userBg, in: Circle())
                        }
                        .disabled(session.stopping)
                        .accessibilityLabel("Stop")
                        .padding(5)
                        .transition(.scale.combined(with: .opacity))
                    } else if canSend {
                        Button {
                            // Put the keyboard away, so the reply has the screen to be read
                            focused = false
                            onSend()
                        } label: {
                            Image(systemName: "arrow.up")
                                .font(.system(size: 15, weight: .bold))
                                .frame(width: 34, height: 34)
                                .foregroundStyle(.labb.onAccent)
                                .background(Color.labb.accentFill, in: Circle())
                        }
                        .accessibilityLabel("Send")
                        .padding(5)
                        .transition(.scale.combined(with: .opacity))
                    }
                }
                .padding(.trailing, session.streaming || canSend ? 0 : LabbSpace.s7)
                .floatingGlass(in: RoundedRectangle(cornerRadius: 22), outline: focused ? Color.labb.accent : nil)
                .animation(.snappy(duration: 0.2), value: canSend)
                .animation(.snappy(duration: 0.2), value: session.streaming)
            }
        }
        .padding(.horizontal, LabbSpace.s6)
        .padding(.bottom, LabbSpace.s3)
        .photosPicker(isPresented: $pickingPhotos, selection: $photos, maxSelectionCount: Self.maxAttachments, matching: .images)
        .onChange(of: photos) { _, items in
            guard !items.isEmpty else { return }
            Task { await addPhotos(items) }
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.plainText, .text, .sourceCode, .json, .commaSeparatedText, .xml, .html, .image],
                      allowsMultipleSelection: true) { result in
            if case .success(let urls) = result { addFiles(urls) }
        }
        .onChange(of: editing) { _, index in if index != nil { focused = true } }
    }

    private var placeholder: String {
        if session.isTerminalSession { return "Message the session" }
        if let agent = app.agent(session.agentId) { return "Message \(agent.name)" }
        return "Message labb"
    }

    private var canSend: Bool {
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty
    }

    // MARK: - Attaching

    private func room() -> Bool {
        if attachments.count >= Self.maxAttachments {
            problem = "At most \(Self.maxAttachments) attachments"
            return false
        }
        return true
    }

    private func addPhotos(_ items: [PhotosPickerItem]) async {
        problem = nil
        for item in items {
            guard room() else { break }
            guard let data = try? await item.loadTransferable(type: Data.self), let image = UIImage(data: data),
                  let jpeg = Self.jpeg(image) else {
                problem = "Couldn't read that picture"
                continue
            }
            attachments.append(Attachment(name: "photo-\(attachments.count + 1).jpg", kind: .image, data: jpeg.base64EncodedString(), mimeType: "image/jpeg"))
        }
        photos = []
    }

    /// A JPEG under the server's limit: smaller and smaller until it fits
    private static func jpeg(_ image: UIImage) -> Data? {
        var image = image
        for _ in 0 ..< 6 {
            if let data = image.jpegData(compressionQuality: 0.8), data.count <= maxImageBytes { return data }
            let size = CGSize(width: image.size.width * 0.7, height: image.size.height * 0.7)
            image = UIGraphicsImageRenderer(size: size).image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
        }
        return nil
    }

    private func addFiles(_ urls: [URL]) {
        problem = nil
        for url in urls {
            guard room() else { break }
            let access = url.startAccessingSecurityScopedResource()
            defer { if access { url.stopAccessingSecurityScopedResource() } }
            guard let data = try? Data(contentsOf: url) else {
                problem = "Couldn't read \(url.lastPathComponent)"
                continue
            }
            if let type = UTType(filenameExtension: url.pathExtension), type.conforms(to: .image) {
                guard let image = UIImage(data: data), let jpeg = Self.jpeg(image) else {
                    problem = "\(url.lastPathComponent) is over 5 MB"
                    continue
                }
                attachments.append(Attachment(name: url.lastPathComponent, kind: .image, data: jpeg.base64EncodedString(), mimeType: "image/jpeg"))
            } else {
                guard let text = String(data: data, encoding: .utf8) else {
                    problem = "\(url.lastPathComponent) isn't text"
                    continue
                }
                guard text.count <= Self.maxTextChars else {
                    problem = "\(url.lastPathComponent) is over 200,000 characters"
                    continue
                }
                attachments.append(Attachment(name: url.lastPathComponent, kind: .text, data: text))
            }
        }
    }
}

private struct AttachmentChip: View {
    let attachment: Attachment
    let onRemove: () -> Void

    var body: some View {
        HStack(spacing: LabbSpace.s3) {
            if attachment.kind == .image, let data = Data(base64Encoded: attachment.data), let image = UIImage(data: data) {
                Image(uiImage: image).resizable().scaledToFill().frame(width: 28, height: 28)
                    .clipShape(RoundedRectangle(cornerRadius: LabbRadius.sm))
            } else {
                Image(systemName: "doc.text")
            }
            Text(attachment.name).labbText(.sm).lineLimit(1)
            Button(action: onRemove) { Image(systemName: "xmark.circle.fill") }
                .foregroundStyle(.labb.muted)
                .accessibilityLabel("Remove \(attachment.name)")
        }
        .padding(.horizontal, LabbSpace.s5)
        .padding(.vertical, LabbSpace.s2)
        .floatingGlass(in: Capsule())
    }
}

extension View {
    /// A control floating over the chat: Liquid Glass on iOS 26, a blurred material before it
    func floatingGlass<S: InsettableShape>(in shape: S, outline: Color? = nil) -> some View {
        modifier(FloatingGlass(shape: shape, outline: outline))
    }
}

private struct FloatingGlass<S: InsettableShape>: ViewModifier {
    let shape: S
    let outline: Color?

    func body(content: Content) -> some View {
        if #available(iOS 26, *) {
            content
                .glassEffect(.regular.interactive(), in: shape)
                .overlay { if let outline { shape.strokeBorder(outline, lineWidth: LabbBorder.heavy) } }
        } else {
            content
                .background(.regularMaterial, in: shape)
                .overlay { shape.strokeBorder(outline ?? Color.labb.border, lineWidth: outline == nil ? LabbBorder.thin : LabbBorder.heavy) }
                .shadow(color: .black.opacity(0.12), radius: 8, y: 2)
        }
    }
}

/// A tool call's arguments and result
struct ToolResultView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let threadId: String?
    let tool: ToolShown
    @State private var result: ToolResult?
    @State private var problem: String?

    var body: some View {
        NavigationStack {
            List {
                if !tool.args.isEmpty {
                    Section("Called with") {
                        ForEach(tool.args.sorted { $0.key < $1.key }, id: \.key) { key, value in
                            VStack(alignment: .leading, spacing: LabbSpace.s2) {
                                Text(key).labbLabel()
                                Text(value.stringValue ?? value.pretty)
                                    .labbText(.sm, design: LabbFontDesign.mono)
                                    .textSelection(.enabled)
                            }
                        }
                    }
                }
                Section("Result") {
                    if let content = result?.content {
                        Text(content)
                            .labbText(.sm, design: LabbFontDesign.mono)
                            .textSelection(.enabled)
                    } else if let problem {
                        Text(problem).foregroundStyle(.labb.muted)
                    } else if tool.id == nil || threadId == nil {
                        Text("The result isn't saved for this call.").foregroundStyle(.labb.muted)
                    } else {
                        ProgressView()
                    }
                }
            }
            .labbList()
            .navigationTitle(tool.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
                if let content = result?.content {
                    ToolbarItem(placement: .topBarLeading) {
                        Button("Copy", systemImage: "doc.on.doc") { UIPasteboard.general.string = content }
                    }
                }
            }
            .task {
                guard let api = app.api, let threadId, let id = tool.id else { return }
                do {
                    result = try await api.get("/tool_result", query: ["thread_id": threadId, "tool_call_id": id])
                } catch {
                    problem = error.localizedDescription
                }
            }
        }
    }
}
