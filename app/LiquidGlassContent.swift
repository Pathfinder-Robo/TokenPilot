import AppKit
import SwiftUI

// Public NSGlassEffectView.Style values, verified in Apple's installed AppKit BridgeSupport metadata.
// Named shim keeps the public macOS26 enum usable with the older build SDK.
private enum NativePublicGlassStyle: Int { case regular = 0, clear = 1 }

final class LiquidGlassInnerHostingView: NSHostingView<AnyView> {
    var suppressIntrinsicInvalidation = false

    override var isOpaque: Bool { false }
    override var mouseDownCanMoveWindow: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func invalidateIntrinsicContentSize() {
        guard !suppressIntrinsicInvalidation else { return }
        super.invalidateIntrinsicContentSize()
    }
}

final class LiquidGlassHostView: NSView {
    let hostingView: LiquidGlassInnerHostingView
    private var effectView: NSView?
    private var lastMeasuredSize: CGSize = .zero
    private var isPerformingLayout = false

    override var isOpaque: Bool { false }
    override var mouseDownCanMoveWindow: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override init(frame frameRect: NSRect) {
        self.hostingView = LiquidGlassInnerHostingView(rootView: AnyView(EmptyView()))
        super.init(frame: frameRect)
        setupViews()
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    private func setupViews() {
        wantsLayer = true
        layer?.backgroundColor = NSColor.clear.cgColor

        hostingView.translatesAutoresizingMaskIntoConstraints = true
        hostingView.autoresizingMask = [.width, .height]
        hostingView.wantsLayer = true
        hostingView.layer?.backgroundColor = NSColor.clear.cgColor

        let glass = Self.makeNativeOrFallbackEffectView(frame: bounds)
        glass.autoresizingMask = [.width, .height]
        addSubview(glass)
        effectView = glass

        embedHostingView(in: glass)
    }

    private static func makeNativeOrFallbackEffectView(frame: NSRect) -> NSView {
        if let cls = NSClassFromString("NSGlassEffectView") as? NSView.Type {
            return cls.init(frame: frame)
        }
        let fallback = NSVisualEffectView(frame: frame)
        fallback.material = .popover
        fallback.blendingMode = .behindWindow
        fallback.state = .active
        fallback.appearance = NSAppearance(named: .aqua)
        fallback.wantsLayer = true
        fallback.layer?.cornerCurve = .continuous
        fallback.layer?.masksToBounds = true
        return fallback
    }

    private func embedHostingView(in glass: NSView) {
        hostingView.frame = glass.bounds
        if glass.responds(to: NSSelectorFromString("setContentView:")) {
            let current = glass.value(forKey: "contentView") as? NSView
            if current !== hostingView {
                glass.setValue(hostingView, forKey: "contentView")
            }
        } else if hostingView.superview !== glass {
            glass.addSubview(hostingView)
        }
    }

    func update(rootView: AnyView, cornerRadius: CGFloat, tintColor: NSColor?) {
        wantsLayer = true
        layer?.backgroundColor = NSColor.clear.cgColor

        hostingView.suppressIntrinsicInvalidation = true
        hostingView.rootView = rootView
        hostingView.suppressIntrinsicInvalidation = false

        if let ev = effectView {
            embedHostingView(in: ev)
            configureEffectView(ev, cornerRadius: cornerRadius, tintColor: tintColor)
        }

        let measured = measuredFittingSize(proposal: nil)
        if abs(measured.width - lastMeasuredSize.width) > 0.5 ||
            abs(measured.height - lastMeasuredSize.height) > 0.5 {
            lastMeasuredSize = measured
            if !isPerformingLayout {
                invalidateIntrinsicContentSize()
            }
            needsLayout = true
        }
    }

    private func configureEffectView(_ ev: NSView, cornerRadius: CGFloat, tintColor: NSColor?) {
        ev.appearance = NSAppearance(named: .aqua)
        // Keep custom glass independent of focus/hover interactions.
        if ev.responds(to: NSSelectorFromString("setEffectIsInteractive:")) {
            ev.setValue(false, forKey: "effectIsInteractive")
        }
        if ev.responds(to: NSSelectorFromString("setStyle:")) {
            ev.setValue(NSNumber(value: NativePublicGlassStyle.clear.rawValue), forKey: "style")
        }
        if ev.responds(to: NSSelectorFromString("setCornerRadius:")) {
            ev.setValue(Double(cornerRadius), forKey: "cornerRadius")
        }
        if ev.responds(to: NSSelectorFromString("setTintColor:")) {
            ev.setValue(tintColor, forKey: "tintColor")
        }
        if let vev = ev as? NSVisualEffectView {
            vev.material = .popover
            vev.blendingMode = .behindWindow
            vev.state = .active
            vev.wantsLayer = true
            vev.layer?.cornerRadius = cornerRadius
            vev.layer?.cornerCurve = .continuous
            vev.layer?.masksToBounds = true
        }
    }

    func measuredFittingSize(proposal: ProposedViewSize?) -> CGSize {
        hostingView.suppressIntrinsicInvalidation = true
        let raw = hostingView.fittingSize
        hostingView.suppressIntrinsicInvalidation = false

        let fallbackW: CGFloat = {
            if let pw = proposal?.width, pw.isFinite, pw > 1 { return pw }
            if bounds.width.isFinite, bounds.width > 1 { return bounds.width }
            return 300
        }()
        let fallbackH: CGFloat = {
            if let ph = proposal?.height, ph.isFinite, ph > 1 { return ph }
            if bounds.height.isFinite, bounds.height > 1 { return bounds.height }
            return 96
        }()

        let w = (raw.width.isFinite && raw.width > 1) ? raw.width : fallbackW
        let h = (raw.height.isFinite && raw.height > 1) ? raw.height : fallbackH
        return CGSize(width: ceil(w), height: ceil(h))
    }

    override var fittingSize: NSSize {
        measuredFittingSize(proposal: nil)
    }

    override var intrinsicContentSize: NSSize {
        measuredFittingSize(proposal: nil)
    }

    override func layout() {
        isPerformingLayout = true
        hostingView.suppressIntrinsicInvalidation = true
        defer {
            hostingView.suppressIntrinsicInvalidation = false
            isPerformingLayout = false
        }

        super.layout()

        if let ev = effectView {
            if ev.frame != bounds {
                ev.frame = bounds
            }
            ev.layoutSubtreeIfNeeded()
            if hostingView.translatesAutoresizingMaskIntoConstraints && hostingView.frame != ev.bounds {
                hostingView.frame = ev.bounds
            }
        } else if hostingView.frame != bounds {
            hostingView.frame = bounds
        }
    }
}

struct LiquidGlassContentRepresentable: NSViewRepresentable {
    let cornerRadius: CGFloat
    let tintColor: NSColor?
    let content: AnyView

    func makeNSView(context: Context) -> LiquidGlassHostView {
        let view = LiquidGlassHostView(frame: .zero)
        view.update(rootView: content, cornerRadius: cornerRadius, tintColor: tintColor)
        return view
    }

    func updateNSView(_ nsView: LiquidGlassHostView, context: Context) {
        nsView.update(rootView: content, cornerRadius: cornerRadius, tintColor: tintColor)
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView: LiquidGlassHostView, context: Context) -> CGSize? {
        nsView.update(rootView: content, cornerRadius: cornerRadius, tintColor: tintColor)
        return nsView.measuredFittingSize(proposal: proposal)
    }
}

struct LiquidGlassPanelSurfaceModifier: ViewModifier {
    let palette: ResolvedThemePalette
    let cornerRadius: CGFloat

    func body(content: Content) -> some View {
        if palette.materialKind == .liquidGlassEffect {
            LiquidGlassContentRepresentable(
                cornerRadius: cornerRadius,
                tintColor: nil,
                content: AnyView(
                    content.environment(\.colorScheme, palette.isDark ? .dark : .light)
                )
            )
            .environment(\.colorScheme, palette.isDark ? .dark : .light)
        } else {
            content
                .background(
                    WidgetSurfaceBackground(palette: palette, cornerRadius: cornerRadius)
                )
                .environment(\.colorScheme, palette.isDark ? .dark : .light)
        }
    }
}
