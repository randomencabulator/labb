import LabbDesign
import SwiftUI

/// Markdown as replies use it: headings, lists, quotes, code blocks and tables, with inline
/// styles and links through AttributedString. Lines break where the text breaks, as on the web.
struct MarkdownText: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: LabbSpace.s5) {
            ForEach(Array(Self.blocks(text).enumerated()), id: \.offset) { _, block in
                view(for: block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    enum Block {
        case paragraph(String)
        case heading(level: Int, text: String)
        case list(items: [String], ordered: Bool)
        case quote(String)
        case code(language: String, text: String)
        case table(rows: [[String]])
        case rule
    }

    @ViewBuilder
    private func view(for block: Block) -> some View {
        switch block {
        case .paragraph(let text):
            Text(Self.inline(text)).textSelection(.enabled)
        case .heading(let level, let text):
            Text(Self.inline(text))
                .labbText(level <= 1 ? .xl : level == 2 ? .lg : .md, weight: level <= 2 ? LabbWeight.black : LabbWeight.bold,
                          tracking: level <= 2 ? LabbTracking.heading : 0)
                .padding(.top, LabbSpace.s2)
        case .list(let items, let ordered):
            VStack(alignment: .leading, spacing: LabbSpace.s2) {
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    HStack(alignment: .firstTextBaseline, spacing: LabbSpace.s4) {
                        Text(ordered ? "\(index + 1)." : "•").foregroundStyle(.labb.muted).monospacedDigit()
                        Text(Self.inline(item)).textSelection(.enabled)
                    }
                }
            }
        case .quote(let text):
            Text(Self.inline(text))
                .foregroundStyle(.labb.muted)
                .padding(.leading, LabbSpace.s6)
                .overlay(alignment: .leading) { Rectangle().fill(Color.labb.accentFill).frame(width: 3) }
        case .code(let language, let text):
            CodeBlockView(language: language, code: text)
        case .table(let rows):
            ScrollView(.horizontal, showsIndicators: false) {
                Grid(alignment: .leading, horizontalSpacing: LabbSpace.s8, verticalSpacing: LabbSpace.s3) {
                    ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                        GridRow {
                            ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                                Text(Self.inline(cell))
                                    .labbText(.sm, weight: index == 0 ? LabbWeight.semibold : LabbWeight.regular)
                            }
                        }
                        if index == 0 { Divider().gridCellUnsizedAxes(.horizontal) }
                    }
                }
                .padding(LabbSpace.s5)
            }
            .background(Color.labb.surface, in: RoundedRectangle(cornerRadius: LabbRadius.md))
            .overlay { RoundedRectangle(cornerRadius: LabbRadius.md).strokeBorder(Color.labb.border, lineWidth: LabbBorder.thin) }
        case .rule:
            Divider()
        }
    }

    /// Inline Markdown (bold, italics, code, links), keeping line breaks
    static func inline(_ text: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace, failurePolicy: .returnPartiallyParsedIfPossible)
        var attributed = (try? AttributedString(markdown: text, options: options)) ?? AttributedString(text)
        for run in attributed.runs where run.inlinePresentationIntent?.contains(.code) == true {
            attributed[run.range].font = .system(.callout, design: .monospaced)
            attributed[run.range].backgroundColor = Color.labb.codeBg
        }
        for run in attributed.runs where run.link != nil {
            attributed[run.range].foregroundColor = Color.labb.accent
            attributed[run.range].underlineStyle = .single
        }
        return attributed
    }

    /// Splits Markdown into blocks
    static func blocks(_ text: String) -> [Block] {
        var blocks: [Block] = []
        var paragraph: [String] = []
        let lines = text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        var index = 0

        func flush() {
            if !paragraph.isEmpty { blocks.append(.paragraph(paragraph.joined(separator: "\n"))) }
            paragraph = []
        }

        func listItem(_ line: String) -> (String, Bool)? {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            for marker in ["- ", "* ", "+ "] where trimmed.hasPrefix(marker) { return (String(trimmed.dropFirst(2)), false) }
            if let match = trimmed.range(of: #"^\d+[.)] "#, options: .regularExpression) {
                return (String(trimmed[match.upperBound...]), true)
            }
            return nil
        }

        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") {
                flush()
                let language = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                var code: [String] = []
                index += 1
                while index < lines.count, !lines[index].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    code.append(lines[index])
                    index += 1
                }
                blocks.append(.code(language: language, text: code.joined(separator: "\n")))
                index += 1
                continue
            }
            if trimmed.isEmpty {
                flush()
            } else if let match = trimmed.range(of: #"^#{1,6} "#, options: .regularExpression) {
                flush()
                blocks.append(.heading(level: trimmed.distance(from: match.lowerBound, to: match.upperBound) - 1, text: String(trimmed[match.upperBound...])))
            } else if trimmed == "---" || trimmed == "***" || trimmed == "___" {
                flush()
                blocks.append(.rule)
            } else if trimmed.hasPrefix(">") {
                flush()
                var quoted: [String] = []
                while index < lines.count, lines[index].trimmingCharacters(in: .whitespaces).hasPrefix(">") {
                    quoted.append(String(lines[index].trimmingCharacters(in: .whitespaces).dropFirst()).trimmingCharacters(in: .whitespaces))
                    index += 1
                }
                blocks.append(.quote(quoted.joined(separator: "\n")))
                continue
            } else if trimmed.hasPrefix("|") {
                flush()
                var rows: [[String]] = []
                while index < lines.count, lines[index].trimmingCharacters(in: .whitespaces).hasPrefix("|") {
                    let row = lines[index].trimmingCharacters(in: .whitespaces)
                    let cells = row.trimmingCharacters(in: CharacterSet(charactersIn: "|")).components(separatedBy: "|")
                        .map { $0.trimmingCharacters(in: .whitespaces) }
                    // The line under the header is only dashes
                    if !cells.allSatisfy({ $0.allSatisfy { "-: ".contains($0) } }) { rows.append(cells) }
                    index += 1
                }
                blocks.append(.table(rows: rows))
                continue
            } else if let (first, ordered) = listItem(line) {
                flush()
                var items = [first]
                index += 1
                while index < lines.count {
                    if let (item, sameKind) = listItem(lines[index]), sameKind == ordered {
                        items.append(item)
                    } else if !lines[index].trimmingCharacters(in: .whitespaces).isEmpty, lines[index].hasPrefix("  ") {
                        // A line that goes on the item before
                        items[items.count - 1] += "\n" + lines[index].trimmingCharacters(in: .whitespaces)
                    } else {
                        break
                    }
                    index += 1
                }
                blocks.append(.list(items: items, ordered: ordered))
                continue
            } else {
                paragraph.append(line)
            }
            index += 1
        }
        flush()
        return blocks
    }
}

/// A code block: mono, scrolling sideways, with its language and a copy button
struct CodeBlockView: View {
    let language: String
    let code: String
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(language.isEmpty ? "code" : language).labbLabel()
                Spacer()
                Button(copied ? "Copied" : "Copy") {
                    UIPasteboard.general.string = code
                    copied = true
                    Task {
                        try? await Task.sleep(for: .seconds(1.5))
                        copied = false
                    }
                }
                .labbText(.xs, weight: LabbWeight.semibold)
                .foregroundStyle(.labb.muted)
            }
            .padding(.horizontal, LabbSpace.s6)
            .padding(.vertical, LabbSpace.s3)
            Divider()
            ScrollView(.horizontal, showsIndicators: false) {
                Text(code)
                    .labbText(.sm, design: LabbFontDesign.mono)
                    .textSelection(.enabled)
                    .padding(LabbSpace.s6)
            }
        }
        .background(Color.labb.codeBg, in: RoundedRectangle(cornerRadius: LabbRadius.lg))
        .overlay { RoundedRectangle(cornerRadius: LabbRadius.lg).strokeBorder(Color.labb.border, lineWidth: LabbBorder.thin) }
    }
}
