import LabbDesign
import SwiftUI

/// A tool call to show the result of
struct ToolShown: Identifiable {
    let id: String?
    let name: String
    let args: [String: JSONValue]

    var identity: String { id ?? name }
}

extension ToolShown: Hashable {
    static func == (a: ToolShown, b: ToolShown) -> Bool { a.identity == b.identity }
    func hash(into hasher: inout Hasher) { hasher.combine(identity) }
}

/// One exchange: the message, and the reply as it's written
struct TurnView: View {
    @Environment(AppModel.self) private var app
    let turn: Turn
    let isLast: Bool
    let canEdit: Bool
    let onEdit: () -> Void
    let onRegenerate: () -> Void
    let onTool: (String?, String, [String: JSONValue]) -> Void
    let session: ChatSession

    var body: some View {
        VStack(alignment: .leading, spacing: LabbSpace.s8) {
            UserMessage(turn: turn, canEdit: canEdit, onEdit: onEdit)
            VStack(alignment: .leading, spacing: LabbSpace.s6) {
                ForEach(Array(turn.parts.enumerated()), id: \.offset) { _, part in
                    PartView(part: part, session: session, onTool: onTool)
                }
                if turn.status == .streaming && turn.parts.isEmpty {
                    HStack(spacing: LabbSpace.s4) {
                        ProgressView().controlSize(.small)
                        Text("Thinking…").foregroundStyle(.labb.muted)
                    }
                }
                if turn.status != .streaming { footer }
            }
        }
    }

    private var footer: some View {
        HStack(spacing: LabbSpace.s8) {
            if !turn.replyText.isEmpty {
                Button("Copy", systemImage: "doc.on.doc") { UIPasteboard.general.string = turn.replyText }
                ShareLink(item: turn.replyText) { Label("Share", systemImage: "square.and.arrow.up") }
            }
            if isLast && canEdit {
                Button("Regenerate", systemImage: "arrow.clockwise", action: onRegenerate)
            }
            Spacer(minLength: 0)
            if turn.status == .stopped {
                Text("Stopped").labbText(.xs).foregroundStyle(.labb.muted)
            }
        }
        .labelStyle(.iconOnly)
        .foregroundStyle(.labb.muted)
        .overlay(alignment: .bottomLeading) {
            if let stats = turn.stats {
                Text(statsLine(stats, modelName: app.modelName))
                    .labbText(.xs)
                    .foregroundStyle(.labb.muted)
                    .lineLimit(1)
                    .offset(y: 22)
            }
        }
        .padding(.bottom, turn.stats == nil ? 0 : LabbSpace.s8)
    }
}

/// What was asked: a bubble on the right, with its pictures and files
private struct UserMessage: View {
    let turn: Turn
    let canEdit: Bool
    let onEdit: () -> Void

    var body: some View {
        HStack {
            Spacer(minLength: 48)
            VStack(alignment: .trailing, spacing: LabbSpace.s3) {
                if !turn.images.isEmpty {
                    HStack(spacing: LabbSpace.s3) {
                        ForEach(Array(turn.images.enumerated()), id: \.offset) { _, image in
                            if let picture = Self.image(image) {
                                Image(uiImage: picture)
                                    .resizable()
                                    .scaledToFill()
                                    .frame(width: 88, height: 88)
                                    .clipShape(RoundedRectangle(cornerRadius: LabbRadius.lg))
                            }
                        }
                    }
                }
                if !turn.user.isEmpty || !turn.files.isEmpty {
                    VStack(alignment: .leading, spacing: LabbSpace.s3) {
                        if !turn.user.isEmpty { Text(turn.user).textSelection(.enabled) }
                        ForEach(turn.files, id: \.self) { file in
                            Label(file, systemImage: "doc.text").labbText(.sm).opacity(0.85)
                        }
                    }
                    .foregroundStyle(.labb.userText)
                    .padding(.horizontal, LabbSpace.s7)
                    .padding(.vertical, LabbSpace.s5)
                    .background(Color.labb.userBg, in: UnevenRoundedRectangle(
                        topLeadingRadius: LabbRadius.lg, bottomLeadingRadius: LabbRadius.lg,
                        bottomTrailingRadius: LabbRadius.xs, topTrailingRadius: LabbRadius.lg
                    ))
                    .contextMenu {
                        Button("Copy", systemImage: "doc.on.doc") { UIPasteboard.general.string = turn.user }
                        if canEdit { Button("Edit", systemImage: "pencil", action: onEdit) }
                    }
                }
            }
        }
    }

    /// A data URL's picture
    static func image(_ dataURL: String) -> UIImage? {
        guard let comma = dataURL.firstIndex(of: ","), let data = Data(base64Encoded: String(dataURL[dataURL.index(after: comma)...])) else { return nil }
        return UIImage(data: data)
    }
}

/// One piece of a reply
private struct PartView: View {
    let part: Part
    let session: ChatSession
    let onTool: (String?, String, [String: JSONValue]) -> Void

    var body: some View {
        switch part {
        case .text(let text):
            MarkdownText(text: text)
        case .thinking(let text, _, let seconds, let done):
            ThinkingView(text: text, seconds: seconds, done: done)
        case .tool(let id, let name, let args, let chars, let error, let firstLine):
            VStack(alignment: .leading, spacing: LabbSpace.s4) {
                Button { onTool(id, name, args) } label: {
                    ToolRow(name: name, args: args, chars: chars, error: error)
                }
                .buttonStyle(.plain)
                if name == "publish_artifact", !error, let url = ArtifactLink.published(firstLine) {
                    ArtifactLink(url: url, name: args["name"]?.stringValue ?? "")
                }
            }
        case .compact:
            Note(text: "Earlier messages were summarized to make room", systemImage: "rectangle.compress.vertical")
        case .note(let text):
            Note(text: text, systemImage: "arrow.triangle.2.circlepath")
        case .approval(let id, let title, let detail, let answer):
            ApprovalCard(id: id, title: title, detail: detail, answer: answer, session: session)
        case .question(let id, let question, let options, let answer, let expired):
            QuestionCard(id: id, question: question, options: options, answer: answer, expired: expired, session: session)
        case .error(let message):
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .labbText(.sm)
                .foregroundStyle(.labb.error)
                .padding(LabbSpace.s6)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.labb.error.opacity(0.08), in: RoundedRectangle(cornerRadius: LabbRadius.md))
        }
    }
}

private struct Note: View {
    let text: String
    let systemImage: String

    var body: some View {
        Label(text, systemImage: systemImage).labbText(.sm).foregroundStyle(.labb.muted)
    }
}

/// The model's reasoning, folded away
private struct ThinkingView: View {
    let text: String
    let seconds: Double?
    let done: Bool
    @State private var open = false

    var body: some View {
        DisclosureGroup(isExpanded: $open) {
            Text(text)
                .labbText(.sm)
                .foregroundStyle(.labb.muted)
                .textSelection(.enabled)
                .padding(.top, LabbSpace.s3)
        } label: {
            HStack(spacing: LabbSpace.s3) {
                if !done { ProgressView().controlSize(.mini) }
                Text(done ? (seconds.map { "Thought for \(Int($0.rounded())) s" } ?? "Thoughts") : "Thinking…")
            }
            .labbText(.sm, weight: LabbWeight.medium)
            .foregroundStyle(.labb.muted)
        }
        .tint(.labb.muted)
    }
}

/// A tool call: its name and what it was called with; tap for its result
private struct ToolRow: View {
    let name: String
    let args: [String: JSONValue]
    let chars: Int?
    let error: Bool

    var body: some View {
        HStack(spacing: LabbSpace.s4) {
            if chars == nil {
                ProgressView().controlSize(.mini)
            } else {
                Image(systemName: error ? "xmark.circle.fill" : "checkmark.circle.fill")
                    .foregroundStyle(error ? Color.labb.error : Color.labb.series3)
            }
            Text(name).labbText(.sm, weight: LabbWeight.semibold, design: LabbFontDesign.mono).foregroundStyle(.labb.text)
            Text(summary).labbText(.sm, design: LabbFontDesign.mono).foregroundStyle(.labb.muted).lineLimit(1)
            Spacer(minLength: 0)
            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.labb.muted)
        }
        .padding(.horizontal, LabbSpace.s5)
        .frame(minHeight: 36)
        .background(Color.labb.codeBg, in: RoundedRectangle(cornerRadius: LabbRadius.sm))
        .overlay { RoundedRectangle(cornerRadius: LabbRadius.sm).strokeBorder(Color.labb.border, lineWidth: LabbBorder.thin) }
    }

    private var summary: String {
        args.sorted { $0.key < $1.key }.map { $0.value.summary }.joined(separator: " ")
    }
}

/// A tool waiting for a yes
private struct ApprovalCard: View {
    let id: String
    let title: String
    let detail: String
    let answer: Part.ApprovalAnswer?
    let session: ChatSession
    @State private var errors = ErrorAlert()
    @State private var sending = false

    var body: some View {
        VStack(alignment: .leading, spacing: LabbSpace.s6) {
            Rectangle()
                .fill(.clear)
                .frame(height: 6)
                .background(HazardStripes())
                .padding(.horizontal, -LabbSpace.s7)
                .padding(.top, -LabbSpace.s6)
            Text(title).labbText(.md, weight: LabbWeight.bold)
            if !detail.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    Text(detail).labbText(.sm, design: LabbFontDesign.mono).textSelection(.enabled)
                }
                .padding(LabbSpace.s5)
                .background(Color.labb.codeBg, in: RoundedRectangle(cornerRadius: LabbRadius.sm))
            }
            switch answer {
            case nil:
                HStack(spacing: LabbSpace.s6) {
                    Button("Allow") { reply(true) }.buttonStyle(.labb(.primary))
                    Button("Don't allow") { reply(false) }.buttonStyle(.labb(.danger))
                }
                .disabled(sending)
            case .allowed:
                Label("Allowed", systemImage: "checkmark").foregroundStyle(.labb.series3)
            case .denied:
                Label("Not allowed", systemImage: "xmark").foregroundStyle(.labb.error)
            case .expired:
                Label("Not answered", systemImage: "clock").foregroundStyle(.labb.muted)
            }
        }
        .padding(.horizontal, LabbSpace.s7)
        .padding(.vertical, LabbSpace.s6)
        .background(Color.labb.surface, in: RoundedRectangle(cornerRadius: LabbRadius.lg))
        .overlay { RoundedRectangle(cornerRadius: LabbRadius.lg).strokeBorder(Color.labb.accentFill, lineWidth: LabbBorder.heavy) }
        .clipShape(RoundedRectangle(cornerRadius: LabbRadius.lg))
        .errorAlert(errors)
    }

    private func reply(_ allow: Bool) {
        sending = true
        Task {
            await errors.run { try await session.answerApproval(id, allow: allow) }
            sending = false
        }
    }
}

/// Yellow and black at 45°, along the top of what needs your approval
struct HazardStripes: View {
    var body: some View {
        Canvas { context, size in
            let stripe: CGFloat = 9
            var x: CGFloat = -size.height
            var yellow = true
            while x < size.width + size.height {
                var path = Path()
                path.move(to: CGPoint(x: x, y: size.height))
                path.addLine(to: CGPoint(x: x + size.height, y: 0))
                path.addLine(to: CGPoint(x: x + size.height + stripe, y: 0))
                path.addLine(to: CGPoint(x: x + stripe, y: size.height))
                context.fill(path, with: .color(yellow ? Color.labbBrand.yellow : Color.labbBrand.black))
                x += stripe
                yellow.toggle()
            }
        }
    }
}

/// An agent on a PC asks something
private struct QuestionCard: View {
    let id: String
    let question: String
    let options: [String]
    let answer: String?
    let expired: Bool
    let session: ChatSession
    @State private var text = ""
    @State private var errors = ErrorAlert()

    var body: some View {
        VStack(alignment: .leading, spacing: LabbSpace.s6) {
            Text(question).labbText(.md, weight: LabbWeight.semibold)
            if let answer {
                Label(answer.isEmpty ? "Answered" : answer, systemImage: "checkmark").foregroundStyle(.labb.muted)
            } else if expired {
                Label("Not answered", systemImage: "clock").foregroundStyle(.labb.muted)
            } else {
                ForEach(options, id: \.self) { option in
                    Button { reply(option) } label: { Text(option).frame(maxWidth: .infinity, alignment: .leading) }
                        .buttonStyle(.labb())
                }
                HStack {
                    TextField("Or write an answer", text: $text).labbField()
                    Button("Send") { reply(text) }.buttonStyle(.labb(.primary)).disabled(text.isEmpty)
                }
            }
        }
        .labbCard()
        .errorAlert(errors)
    }

    private func reply(_ text: String) {
        Task { await errors.run { try await session.answerQuestion(id, text: text) } }
    }
}

/// A web page the model published (tools/artifacts.py): its title, opened in Safari. It's on
/// labb-sites, another port of the server
struct ArtifactLink: View {
    let url: URL
    let name: String

    /// Where publish_artifact put its page, from its result's first line
    static func published(_ firstLine: String?) -> URL? {
        guard let line = firstLine, line.hasPrefix("Published at ") else { return nil }
        let address = line.dropFirst("Published at ".count).trimmingCharacters(in: .whitespaces)
        guard address.range(of: #"^https?://\S+/artifacts/[a-z0-9-]+/$"#, options: .regularExpression) != nil else { return nil }
        return URL(string: address)
    }

    /// "sales-chart-k3f9qa" is "Sales chart"
    private var title: String {
        let words = name.replacingOccurrences(of: #"-[a-z0-9]{6}$"#, with: "", options: .regularExpression)
            .replacingOccurrences(of: "-", with: " ")
        return words.prefix(1).uppercased() + words.dropFirst()
    }

    var body: some View {
        Link(destination: url) {
            HStack(spacing: LabbSpace.s5) {
                Image(systemName: "macwindow").foregroundStyle(.labb.muted)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title.isEmpty ? "Web page" : title).labbText(.md, weight: LabbWeight.bold)
                    Text(url.host() ?? url.absoluteString).labbText(.sm).foregroundStyle(.labb.muted).lineLimit(1)
                }
                Spacer(minLength: 0)
                Label("Open", systemImage: "safari").labbText(.sm, weight: LabbWeight.bold)
            }
            .padding(LabbSpace.s6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.labb.surface, in: RoundedRectangle(cornerRadius: LabbRadius.md))
            .overlay(RoundedRectangle(cornerRadius: LabbRadius.md).stroke(Color.labb.border))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Open the web page \(title)")
    }
}
