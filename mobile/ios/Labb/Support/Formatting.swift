import Foundation
import LabbDesign
import SwiftUI

/// The server's times are ISO 8601, with or without fractions of a second
enum ServerDate {
    private nonisolated(unsafe) static let plain: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private nonisolated(unsafe) static let fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    static func parse(_ text: String?) -> Date? {
        guard var text, !text.isEmpty else { return nil }
        // Times without a zone are UTC
        if !text.hasSuffix("Z") && !text.contains("+") && text.range(of: #"-\d\d:\d\d$"#, options: .regularExpression) == nil {
            text += "Z"
        }
        return fractional.date(from: text) ?? plain.date(from: text)
    }
}

extension Date {
    /// "2 min ago", "yesterday"…
    var relative: String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter.localizedString(for: self, relativeTo: .now)
    }
}

/// "2 min ago" for a server time, or nothing
func relativeTime(_ text: String?) -> String {
    ServerDate.parse(text)?.relative ?? ""
}

/// "Mar 4, 14:05" for a server time
func shortTime(_ text: String?) -> String {
    guard let date = ServerDate.parse(text) else { return "" }
    return date.formatted(date: .abbreviated, time: .shortened)
}

/// 1234 → "1.2k", 3400000 → "3.4M"
func compactNumber(_ value: Double) -> String {
    value.formatted(.number.notation(.compactName).precision(.fractionLength(0 ... 1)))
}

func byteCount(_ bytes: Int) -> String {
    ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
}

/// "4.2 s" for milliseconds
func seconds(_ milliseconds: Double?) -> String {
    guard let milliseconds else { return "" }
    return milliseconds < 1000 ? "\(Int(milliseconds)) ms" : String(format: "%.1f s", milliseconds / 1000)
}

/// A reply's stats in one line: model · time · tokens
func statsLine(_ stats: Stats, modelName: (String?) -> String) -> String {
    var parts: [String] = []
    if let model = stats.model { parts.append(modelName(model)) }
    if let duration = stats.durationMs { parts.append(seconds(duration)) }
    if let output = stats.outputTokens { parts.append("\(compactNumber(output)) tokens") }
    if let tools = stats.toolCalls, tools > 0 { parts.append("\(Int(tools)) tool call\(tools == 1 ? "" : "s")") }
    return parts.joined(separator: " · ")
}

// MARK: - Errors

/// An error to show in an alert: every screen that calls the server uses one
@MainActor
@Observable
final class ErrorAlert {
    var message: String?

    /// Run something; if it throws, show its error
    func run(_ work: () async throws -> Void) async {
        do {
            try await work()
        } catch is CancellationError {
        } catch {
            message = error.localizedDescription
        }
    }
}

extension View {
    func errorAlert(_ alert: ErrorAlert) -> some View {
        self.alert("Something went wrong", isPresented: Binding(get: { alert.message != nil }, set: { if !$0 { alert.message = nil } })) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(alert.message ?? "")
        }
    }

    /// A list on labb's page colour, with its rows on the surface colour
    func labbList() -> some View {
        scrollContentBackground(.hidden)
            .background(Color.labb.bg)
    }
}

/// A status as a small coloured label
struct StatusBadge: View {
    let status: String

    var body: some View {
        Text(label)
            .labbText(.xs, weight: LabbWeight.semibold)
            .foregroundStyle(color)
    }

    private var label: String { status.prefix(1).uppercased() + status.dropFirst() }

    private var color: Color {
        switch status {
        case "done", "ready", "ok": .labb.series3
        case "failed", "error": .labb.error
        case "running", "queued", "indexing", "waiting": .labb.accent
        default: .labb.muted
        }
    }
}
