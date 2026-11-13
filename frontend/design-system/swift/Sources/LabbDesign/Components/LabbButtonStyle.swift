import SwiftUI

/// labb's buttons. `.primary` is the one yellow action per screen: it sits on a hard shadow and
/// presses down onto it. Every button is at least 44pt tall, so it's easy to tap.
///
///     Button("New chat") { … }.buttonStyle(.labb(.primary))
public struct LabbButtonStyle: ButtonStyle {
    public enum Variant: Sendable {
        /// Yellow, bold, on a hard shadow
        case primary
        /// A hairline outline
        case secondary
        /// A hairline outline, in the error colour
        case danger
        /// No outline, muted
        case ghost
    }

    let variant: Variant

    public init(_ variant: Variant = .secondary) {
        self.variant = variant
    }

    public func makeBody(configuration: Configuration) -> some View {
        LabbButton(configuration: configuration, variant: variant)
    }
}

public extension ButtonStyle where Self == LabbButtonStyle {
    /// labb's button style: `.buttonStyle(.labb(.primary))`
    static func labb(_ variant: LabbButtonStyle.Variant = .secondary) -> LabbButtonStyle {
        LabbButtonStyle(variant)
    }
}

private struct LabbButton: View {
    let configuration: ButtonStyle.Configuration
    let variant: LabbButtonStyle.Variant
    @Environment(\.isEnabled) private var isEnabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private let shape = RoundedRectangle(cornerRadius: LabbRadius.md)
    private var pressed: Bool { configuration.isPressed && isEnabled }

    var body: some View {
        let shadow = LabbShadow.press
        configuration.label
            .labbText(.base, weight: variant == .primary ? LabbWeight.bold : LabbWeight.medium)
            .foregroundStyle(foreground)
            .padding(.horizontal, LabbSpace.s8)
            .frame(minHeight: 44)
            .background(background, in: shape)
            .overlay { shape.strokeBorder(outline, lineWidth: LabbBorder.thin) }
            .contentShape(shape)
            // The yellow button presses down onto its shadow
            .offset(x: variant == .primary && pressed ? shadow.x : 0, y: variant == .primary && pressed ? shadow.y : 0)
            .background {
                if variant == .primary {
                    shape.fill(shadow.color).offset(x: shadow.x, y: shadow.y)
                }
            }
            .opacity(isEnabled ? 1 : 0.5)
            .animation(reduceMotion ? nil : LabbMotion.press, value: pressed)
    }

    private var foreground: Color {
        switch variant {
        case .primary: .labb.onAccent
        case .danger: .labb.error
        case .ghost: pressed ? .labb.text : .labb.muted
        case .secondary: .labb.text
        }
    }

    private var background: Color {
        switch variant {
        case .primary: .labb.accentFill
        case .ghost, .secondary, .danger: pressed ? .labb.active : .clear
        }
    }

    private var outline: Color {
        switch variant {
        case .primary: .labb.accentFill
        case .secondary: pressed ? .labb.muted : .labb.border
        case .danger: pressed ? .labb.error : .labb.border
        case .ghost: .clear
        }
    }
}
