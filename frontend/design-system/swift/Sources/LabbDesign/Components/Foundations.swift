import SwiftUI

// What every screen needs, and the type and shadows the rest is built from.

public extension View {
    /// The page: labb's background (edge to edge), text colour, accent and body type. Put it on the
    /// app's root view once.
    func labbRoot() -> some View {
        modifier(LabbRoot())
    }

    /// Text in one of labb's sizes, scaling with Dynamic Type from the size it's relative to.
    /// `tracking` is in em, like the tokens (`LabbTracking.heading`).
    func labbText(
        _ style: LabbTextStyle = .base,
        weight: Font.Weight = LabbWeight.regular,
        design: Font.Design = LabbFontDesign.sans,
        tracking: CGFloat = 0
    ) -> some View {
        modifier(LabbText(style: style, weight: weight, design: design, tracking: tracking))
    }

    /// A heading: black weight and tight tracking
    func labbHeading(_ style: LabbTextStyle = .xxl) -> some View {
        labbText(style, weight: LabbWeight.black, tracking: LabbTracking.heading)
    }

    /// A mono label over a section: small, bold, uppercase and spaced, in the muted colour
    func labbLabel() -> some View {
        labbText(.xxs, weight: LabbWeight.bold, design: LabbFontDesign.mono, tracking: LabbTracking.label)
            .textCase(.uppercase)
            .foregroundStyle(.labb.muted)
    }

    /// One of labb's shadows under (or, for inset ones, inside the leading edge of) a shape with
    /// these corners. The hard ones are solid and offset, never blurred.
    func labbShadow(_ shadow: LabbShadow, cornerRadius: CGFloat = LabbRadius.lg) -> some View {
        modifier(LabbShadowModifier(shadow: shadow, cornerRadius: cornerRadius))
    }
}

struct LabbRoot: ViewModifier {
    func body(content: Content) -> some View {
        content
            .labbText(.base)
            .foregroundStyle(.labb.text)
            .tint(.labb.accent)
            .background(Color.labb.bg.ignoresSafeArea())
    }
}

struct LabbText: ViewModifier {
    let weight: Font.Weight
    let design: Font.Design
    let tracking: CGFloat
    let lineHeight: CGFloat
    @ScaledMetric private var size: CGFloat

    init(style: LabbTextStyle, weight: Font.Weight, design: Font.Design, tracking: CGFloat) {
        self.weight = weight
        self.design = design
        self.tracking = tracking
        lineHeight = style.lineHeight
        _size = ScaledMetric(wrappedValue: style.size, relativeTo: style.relativeTo)
    }

    func body(content: Content) -> some View {
        content
            .font(.system(size: size, weight: weight, design: design))
            .tracking(tracking * size)
            // SwiftUI's own line height is about 1.2 times the size; add the rest of the token's
            .lineSpacing(max(0, (lineHeight - 1.2) * size))
    }
}

struct LabbShadowModifier: ViewModifier {
    let shadow: LabbShadow
    let cornerRadius: CGFloat

    func body(content: Content) -> some View {
        if shadow.inset {
            // A bar inside the leading edge, as wide as the shadow's offset
            content.overlay(alignment: .leading) {
                Rectangle().fill(shadow.color).frame(width: shadow.x)
            }
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
        } else if shadow.blur > 0 {
            content.shadow(color: shadow.color, radius: shadow.blur / 2, x: shadow.x, y: shadow.y)
        } else {
            // Solid: the same shape, filled with the shadow's colour, behind and offset
            content.background {
                RoundedRectangle(cornerRadius: cornerRadius)
                    .fill(shadow.color)
                    .offset(x: shadow.x, y: shadow.y)
            }
        }
    }
}
