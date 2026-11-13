import SwiftUI

// The rest of labb's pieces for SwiftUI: a switch, cards, badges and text fields.

/// labb's switch: yellow when on, with the knob on a soft shadow.
///
///     Toggle("Memories", isOn: $on).toggleStyle(.labb)
public struct LabbSwitchStyle: ToggleStyle {
    public init() {}

    public func makeBody(configuration: Configuration) -> some View {
        LabbSwitch(configuration: configuration)
    }
}

public extension ToggleStyle where Self == LabbSwitchStyle {
    /// labb's switch: `.toggleStyle(.labb)`
    static var labb: LabbSwitchStyle { LabbSwitchStyle() }
}

private struct LabbSwitch: View {
    let configuration: ToggleStyle.Configuration
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let knob = LabbShadow.knob
        Button {
            configuration.isOn.toggle()
        } label: {
            HStack(spacing: LabbSpace.s6) {
                configuration.label
                Spacer(minLength: 0)
                Capsule()
                    .fill(configuration.isOn ? Color.labb.accentFill : Color.labb.border)
                    .frame(width: 44, height: 26)
                    .overlay(alignment: configuration.isOn ? .trailing : .leading) {
                        Circle()
                            .fill(Color.labb.knob)
                            .shadow(color: knob.color, radius: knob.blur / 2, x: knob.x, y: knob.y)
                            .padding(3)
                    }
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .opacity(isEnabled ? 1 : 0.5)
        .animation(reduceMotion ? nil : LabbMotion.base, value: configuration.isOn)
        .accessibilityRepresentation { Toggle(isOn: configuration.$isOn) { configuration.label } }
    }
}

public extension View {
    /// A card: the surface colour, a strong hairline and 6pt corners. An interactive one gets the
    /// yellow lift shadow while it's pressed or selected.
    func labbCard(lifted: Bool = false) -> some View {
        padding(.vertical, LabbSpace.s6)
            .padding(.horizontal, LabbSpace.s7)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.labb.surface, in: RoundedRectangle(cornerRadius: LabbRadius.lg))
            .overlay {
                RoundedRectangle(cornerRadius: LabbRadius.lg)
                    .strokeBorder(lifted ? Color.labb.accent : Color.labb.border, lineWidth: LabbBorder.strong)
            }
            .labbShadow(lifted ? .lift : LabbShadow(x: 0, y: 0, blur: 0, spread: 0, color: .clear, inset: false))
    }
}

/// A small label or count; the accent one for what needs looking at.
///
///     LabbBadge("3", tone: .accent)
public struct LabbBadge: View {
    public enum Tone: Sendable {
        case standard, accent, outline, error
    }

    let text: String
    let tone: Tone

    public init(_ text: String, tone: Tone = .standard) {
        self.text = text
        self.tone = tone
    }

    public var body: some View {
        Text(text)
            .labbText(.xs, weight: tone == .accent ? LabbWeight.semibold : LabbWeight.regular)
            .foregroundStyle(foreground)
            .padding(.horizontal, LabbSpace.s3)
            .padding(.vertical, LabbSpace.s1 / 2)
            .background(background, in: RoundedRectangle(cornerRadius: LabbRadius.sm))
            .overlay {
                if tone == .outline {
                    RoundedRectangle(cornerRadius: LabbRadius.sm).strokeBorder(Color.labb.accent, lineWidth: LabbBorder.thin)
                }
            }
    }

    private var foreground: Color {
        switch tone {
        case .accent: .labb.onAccent
        case .error: .labb.error
        case .standard, .outline: .labb.text
        }
    }

    private var background: Color {
        switch tone {
        case .standard: .labb.active
        case .accent: .labb.accentFill
        case .error: .labb.error.opacity(0.14)
        case .outline: .clear
        }
    }
}

/// labb's text fields: the page colour, a hairline, 4pt corners.
/// (A modifier rather than a TextFieldStyle: SwiftUI calls styles off the main actor.)
///
///     TextField("Name", text: $name).labbField()
public extension View {
    func labbField(invalid: Bool = false) -> some View {
        labbText(.base)
            .padding(.horizontal, LabbSpace.s5)
            .frame(minHeight: 44)
            .background(Color.labb.bg, in: RoundedRectangle(cornerRadius: LabbRadius.md))
            .overlay {
                RoundedRectangle(cornerRadius: LabbRadius.md)
                    .strokeBorder(invalid ? Color.labb.error : Color.labb.border, lineWidth: LabbBorder.thin)
            }
    }
}
