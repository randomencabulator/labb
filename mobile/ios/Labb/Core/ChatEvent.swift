import Foundation

/// One event of a reply: from /chat as it's written, from /chat/live when joining one, or from
/// /history when a chat is opened. Lines of newline-delimited JSON, told apart by `type`.
enum ChatEvent: Decodable, Sendable {
    case thread(String)
    case user(content: String, files: [String], images: [String], turn: Int?)
    case thinking(String)
    case token(String)
    case toolCall(id: String?, name: String, args: [String: JSONValue])
    case toolResult(id: String?, chars: Int, error: Bool, firstLine: String?)
    case compact
    /// The reply ended too early and goes on
    case continued(reason: String)
    /// A model call failed for a moment and is tried again
    case retry(wait: Double, message: String)
    case ping
    /// A tool waits for a yes
    case approval(id: String, title: String, detail: String)
    /// An agent on a PC asks something
    case question(id: String, question: String, options: [String])
    case answered(id: String, allow: Bool?, text: String?)
    case title(String)
    case error(String)
    case stats(Stats)
    case done(Stats?)
    case stopped(Stats?)
    case unknown

    private enum Keys: String, CodingKey {
        case type, content, files, images, turn, id, name, args, chars, error, reason, wait, message
        case title, detail, question, options, allow, text, stats
        case firstLine = "first_line"
        case threadId = "thread_id"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        let type = try c.decode(String.self, forKey: .type)
        func string(_ key: Keys) -> String { (try? c.decode(String.self, forKey: key)) ?? "" }
        switch type {
        case "thread": self = .thread(string(.threadId))
        case "user":
            self = .user(
                content: string(.content),
                files: (try? c.decode([String].self, forKey: .files)) ?? [],
                images: (try? c.decode([String].self, forKey: .images)) ?? [],
                turn: try? c.decode(Int.self, forKey: .turn)
            )
        case "thinking": self = .thinking(string(.content))
        case "token": self = .token(string(.content))
        case "tool_call":
            self = .toolCall(id: try? c.decode(String.self, forKey: .id), name: string(.name),
                             args: (try? c.decode([String: JSONValue].self, forKey: .args)) ?? [:])
        case "tool_result":
            self = .toolResult(id: try? c.decode(String.self, forKey: .id), chars: (try? c.decode(Int.self, forKey: .chars)) ?? 0,
                               error: (try? c.decode(Bool.self, forKey: .error)) ?? false,
                               firstLine: try? c.decode(String.self, forKey: .firstLine))
        case "compact": self = .compact
        case "continue": self = .continued(reason: string(.reason))
        case "retry": self = .retry(wait: (try? c.decode(Double.self, forKey: .wait)) ?? 0, message: string(.message))
        case "ping": self = .ping
        case "approval": self = .approval(id: string(.id), title: string(.title), detail: string(.detail))
        case "question":
            self = .question(id: string(.id), question: string(.question), options: (try? c.decode([String].self, forKey: .options)) ?? [])
        case "answered":
            self = .answered(id: string(.id), allow: try? c.decode(Bool.self, forKey: .allow), text: try? c.decode(String.self, forKey: .text))
        case "title": self = .title(string(.title))
        case "error": self = .error(string(.message))
        case "stats": self = .stats(try Stats(from: decoder))
        case "done": self = .done(try? c.decode(Stats.self, forKey: .stats))
        case "stopped": self = .stopped(try? c.decode(Stats.self, forKey: .stats))
        default: self = .unknown
        }
    }
}

/// A piece of a reply
enum Part: Identifiable, Sendable {
    case text(String)
    case thinking(text: String, startedAt: Date?, seconds: Double?, done: Bool)
    /// `firstLine`: the start of its result (publish_artifact's says where the page is)
    case tool(id: String?, name: String, args: [String: JSONValue], chars: Int?, error: Bool, firstLine: String? = nil)
    case compact
    case note(String)
    case approval(id: String, title: String, detail: String, answer: ApprovalAnswer?)
    case question(id: String, question: String, options: [String], answer: String?, expired: Bool)
    case error(String)

    enum ApprovalAnswer: Sendable {
        case allowed, denied, expired
    }

    // Parts are only ever appended or changed in place, so their place is their identity
    var id: String { "" }
}

/// One exchange: what was asked, and the reply
struct Turn: Identifiable, Sendable {
    enum Status: Sendable {
        case streaming, done, stopped, error
    }

    let id = UUID()
    var user: String
    var files: [String]
    var images: [String]
    var parts: [Part] = []
    var status: Status = .streaming
    var stats: Stats?
    var startedAt: Date?

    /// The reply's text, for copying and reading aloud
    var replyText: String {
        parts.compactMap { if case .text(let text) = $0 { text } else { nil } }.joined(separator: "\n\n")
    }
}

private let continueReasons = [
    "empty": "The reply was empty: asking again",
    "announced": "It said what it'd do next and stopped: telling it to go on",
    "open_steps": "Its todo list still has open steps: back to work",
    "loop_unscheduled": "It didn't say when to run again: asking",
    "goal": "The goal isn't done yet: going on",
    "goal_budget": "The goal used its budget: wrapping up",
    "hook": "A check said the work isn't done: going on",
    "audit": "Checking the evidence before the report",
]

/// Turns events into turns, as the web's turnsReducer does
extension [Turn] {
    private mutating func updateLast(_ change: (inout Turn) -> Void) {
        guard !isEmpty else { return }
        change(&self[count - 1])
    }

    private static func endThinking(_ parts: inout [Part], now: Date?) {
        guard case .thinking(let text, let startedAt, _, false) = parts.last else { return }
        let seconds = startedAt.flatMap { start in now.map { $0.timeIntervalSince(start) } }
        parts[parts.count - 1] = .thinking(text: text, startedAt: startedAt, seconds: seconds, done: true)
    }

    private static func expireQuestions(_ parts: inout [Part]) {
        for index in parts.indices {
            switch parts[index] {
            case .approval(let id, let title, let detail, nil):
                parts[index] = .approval(id: id, title: title, detail: detail, answer: .expired)
            case .question(let id, let question, let options, nil, false):
                parts[index] = .question(id: id, question: question, options: options, answer: nil, expired: true)
            default: break
            }
        }
    }

    /// The reply in progress stopped (or its stream ended)
    mutating func abort() {
        updateLast { turn in
            guard turn.status == .streaming else { return }
            Self.endThinking(&turn.parts, now: .now)
            Self.expireQuestions(&turn.parts)
            turn.status = .stopped
        }
    }

    /// Saved turns without stats are finished too
    mutating func replayed() {
        for index in indices where self[index].status == .streaming {
            Self.endThinking(&self[index].parts, now: nil)
            self[index].status = .done
        }
    }

    mutating func apply(_ event: ChatEvent, now: Date? = nil) {
        switch event {
        case .user(let content, let files, let images, _):
            append(Turn(user: content, files: files, images: images, startedAt: now))
        case .thinking(let text):
            updateLast { turn in
                if case .thinking(let previous, let startedAt, let seconds, false) = turn.parts.last {
                    turn.parts[turn.parts.count - 1] = .thinking(text: previous + text, startedAt: startedAt, seconds: seconds, done: false)
                } else {
                    turn.parts.append(.thinking(text: text, startedAt: now, seconds: nil, done: false))
                }
            }
        case .token(let text):
            updateLast { turn in
                Self.endThinking(&turn.parts, now: now)
                if case .text(let previous) = turn.parts.last {
                    turn.parts[turn.parts.count - 1] = .text(previous + text)
                } else {
                    turn.parts.append(.text(text))
                }
            }
        case .toolCall(let id, let name, let args):
            updateLast { turn in
                Self.endThinking(&turn.parts, now: now)
                turn.parts.append(.tool(id: id, name: name, args: args, chars: nil, error: false, firstLine: nil))
            }
        case .toolResult(let id, let chars, let error, let firstLine):
            updateLast { turn in
                // Its call (by id), or the first tool line still waiting for its result
                guard let index = turn.parts.firstIndex(where: { part in
                    if case .tool(let partId, _, _, nil, _, _) = part { return id == nil || partId == id }
                    return false
                }), case .tool(let partId, let name, let args, _, _, _) = turn.parts[index] else { return }
                turn.parts[index] = .tool(id: partId, name: name, args: args, chars: chars, error: error, firstLine: firstLine)
            }
        case .compact:
            updateLast { turn in
                Self.endThinking(&turn.parts, now: now)
                turn.parts.append(.compact)
            }
        case .continued(let reason):
            updateLast { turn in
                Self.endThinking(&turn.parts, now: now)
                // A cut-off reply goes on in the same text
                if reason != "truncated" { turn.parts.append(.note(continueReasons[reason] ?? "Going on")) }
            }
        case .retry(let wait, let message):
            updateLast { turn in
                Self.endThinking(&turn.parts, now: now)
                // What the failed call wrote comes again
                if case .text = turn.parts.last { turn.parts.removeLast() }
                turn.parts.append(.note("The model call failed, trying again in \(Int(wait))s: \(message)"))
            }
        case .approval(let id, let title, let detail):
            updateLast { turn in
                Self.endThinking(&turn.parts, now: now)
                turn.parts.append(.approval(id: id, title: title, detail: detail, answer: nil))
            }
        case .question(let id, let question, let options):
            updateLast { turn in
                Self.endThinking(&turn.parts, now: now)
                turn.parts.append(.question(id: id, question: question, options: options, answer: nil, expired: false))
            }
        case .answered(let id, let allow, let text):
            updateLast { turn in
                for index in turn.parts.indices {
                    switch turn.parts[index] {
                    case .approval(id, let title, let detail, _):
                        turn.parts[index] = .approval(id: id, title: title, detail: detail, answer: allow == true ? .allowed : .denied)
                    case .question(id, let question, let options, _, _):
                        turn.parts[index] = .question(id: id, question: question, options: options, answer: text ?? "", expired: false)
                    default: break
                    }
                }
            }
        case .error(let message):
            updateLast { turn in
                Self.endThinking(&turn.parts, now: now)
                turn.parts.append(.error(message))
                turn.status = .error
            }
        case .stats(let stats):
            updateLast { turn in
                turn.stats = stats
                turn.status = stats.stopped == true ? .stopped : .done
            }
        case .done(let stats), .stopped(let stats):
            let stoppedEvent: Bool = if case .stopped = event { true } else { false }
            updateLast { turn in
                Self.endThinking(&turn.parts, now: now)
                Self.expireQuestions(&turn.parts)
                if let stats { turn.stats = stats }
                if turn.status != .error { turn.status = stoppedEvent ? .stopped : .done }
            }
        case .thread, .title, .ping, .unknown:
            break
        }
    }

    /// Mark an approval answered here
    mutating func answer(approval id: String, allowed: Bool) {
        updateLast { turn in
            for index in turn.parts.indices {
                if case .approval(id, let title, let detail, _) = turn.parts[index] {
                    turn.parts[index] = .approval(id: id, title: title, detail: detail, answer: allowed ? .allowed : .denied)
                }
            }
        }
    }

    mutating func answer(question id: String, text: String) {
        updateLast { turn in
            for index in turn.parts.indices {
                if case .question(id, let question, let options, _, _) = turn.parts[index] {
                    turn.parts[index] = .question(id: id, question: question, options: options, answer: text, expired: false)
                }
            }
        }
    }
}
