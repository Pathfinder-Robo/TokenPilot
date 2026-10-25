import AppKit
import SwiftUI
import Combine

// MARK: - Theme & Language Identifiers

enum ThemeID: String, CaseIterable, Codable, Identifiable {
    case cupertino = "cupertino"
    case graphite = "graphite"
    case instrument = "instrument"
    case frosted = "frosted"
    case liquid = "liquid"
    case vibrant = "vibrant"

    var id: String { rawValue }

    static let defaultTheme: ThemeID = .graphite

    static func resolve(_ raw: String?) -> ThemeID? {
        guard let cleaned = raw?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(), !cleaned.isEmpty else {
            return nil
        }
        switch cleaned {
        case "cupertino", "a", "light", "pearl":
            return .cupertino
        case "graphite", "quiet-graphite", "quiet_graphite", "b":
            return .graphite
        case "instrument", "d", "mono", "monochrome":
            return .instrument
        case "frosted", "frosted-glass", "frosted_glass", "apple-frosted-glass":
            return .frosted
        case "liquid", "liquid-glass", "liquid_glass", "apple-liquid-glass":
            return .liquid
        case "vibrant", "colorful":
            return .vibrant
        default:
            return nil
        }
    }

    var supportsCustomColors: Bool {
        self != .instrument
    }

    var usesGlassMaterial: Bool {
        self == .frosted || self == .liquid
    }

    var cornerRadius: CGFloat {
        switch self {
        case .cupertino, .frosted, .vibrant:
            return 16
        case .graphite:
            return 12
        case .instrument:
            return 8
        case .liquid:
            return 18
        }
    }

    enum TokenLayoutStyle {
        case twoColumns
        case ledgerRows
        case openGridMono
    }

    enum QuotaLayoutStyle {
        case stackedHeaderBar
        case ledgerBar
        case openGridQuotas
    }

    var tokenLayout: TokenLayoutStyle {
        switch self {
        case .cupertino, .frosted, .liquid, .vibrant:
            return .twoColumns
        case .graphite:
            return .ledgerRows
        case .instrument:
            return .openGridMono
        }
    }

    var quotaLayout: QuotaLayoutStyle {
        switch self {
        case .cupertino, .frosted, .liquid, .vibrant:
            return .stackedHeaderBar
        case .graphite:
            return .ledgerBar
        case .instrument:
            return .openGridQuotas
        }
    }

    var defaultColorPair: ThemeColorPair {
        switch self {
        case .cupertino:
            return ThemeColorPair(primaryHex: "#007AFF", secondaryHex: "#30B0C7")
        case .graphite:
            return ThemeColorPair(primaryHex: "#8E8CD8", secondaryHex: "#636182")
        case .instrument:
            return ThemeColorPair(primaryHex: "#EDEDED", secondaryHex: "#6E6E76")
        case .frosted:
            return ThemeColorPair(primaryHex: "#3898FF", secondaryHex: "#4FD1C5")
        case .liquid:
            return ThemeColorPair(primaryHex: "#0068E6", secondaryHex: "#6C63FF")
        case .vibrant:
            return ThemeColorPair(primaryHex: "#22D3EE", secondaryHex: "#F472B6")
        }
    }

    func displayName(in language: AppLanguage) -> String {
        switch self {
        case .cupertino:
            return language.L("Cupertino", "Cupertino 浅色")
        case .graphite:
            return language.L("Quiet Graphite", "静谧石墨")
        case .instrument:
            return language.L("Instrument", "精密仪表")
        case .frosted:
            return language.L("Frosted Glass", "磨砂玻璃")
        case .liquid:
            return language.L("Liquid Glass", "液态玻璃")
        case .vibrant:
            return language.L("Vibrant", "炫彩活力")
        }
    }

    func summary(in language: AppLanguage) -> String {
        switch self {
        case .cupertino:
            return language.L("Light pearl surface and classic blue actions", "浅色珠光面板与经典蓝青交互")
        case .graphite:
            return language.L("Warm graphite ledger with calm lilac accents", "温润石墨账本布局与柔和丁香紫")
        case .instrument:
            return language.L("High-contrast monochrome grid & mono counters", "高对比单色开放网格与等宽计数")
        case .frosted:
            return language.L("Translucent macOS HUD blur with cool highlights", "macOS 原生磨砂半透明材质与冷色高亮")
        case .liquid:
            return language.L("Refractive Liquid Glass optic with specular rim", "原生液态玻璃折射质感与高光边缘")
        case .vibrant:
            return language.L("Deep indigo-teal gradient with luminous accents", "深邃靛青渐变面板与鲜明活力强调色")
        }
    }
}

enum AppLanguage: String, CaseIterable, Codable, Identifiable {
    case english = "en"
    case chinese = "zh-Hans"

    var id: String { rawValue }

    static let defaultLanguage: AppLanguage = .english

    static func resolve(_ raw: String?) -> AppLanguage? {
        guard let cleaned = raw?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(), !cleaned.isEmpty else {
            return nil
        }
        if cleaned == "en" || cleaned.hasPrefix("en-") || cleaned.hasPrefix("en_") || cleaned == "english" {
            return .english
        }
        if cleaned == "zh" || cleaned.hasPrefix("zh-") || cleaned.hasPrefix("zh_") || cleaned == "chinese" || cleaned.contains("中文") {
            return .chinese
        }
        return nil
    }

    var displayName: String {
        switch self {
        case .english:
            return "English"
        case .chinese:
            return "简体中文"
        }
    }

    var locale: Locale {
        switch self {
        case .english:
            return Locale(identifier: "en_US")
        case .chinese:
            return Locale(identifier: "zh_CN")
        }
    }

    func L(_ en: String, _ zh: String) -> String {
        self == .chinese ? zh : en
    }
}

// MARK: - RGB Color & WCAG Contrast Math

struct RGBColor: Equatable, Sendable {
    var r: Double
    var g: Double
    var b: Double

    init(r: Double, g: Double, b: Double) {
        self.r = min(max(r, 0.0), 1.0)
        self.g = min(max(g, 0.0), 1.0)
        self.b = min(max(b, 0.0), 1.0)
    }

    static func normalizeHex(_ raw: String?) -> String? {
        guard var s = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else {
            return nil
        }
        if s.hasPrefix("#") {
            s.removeFirst()
        }
        guard s.count == 6 else {
            return nil
        }
        for scalar in s.unicodeScalars {
            let v = scalar.value
            let isDigit = (v >= 48 && v <= 57)
            let isUpper = (v >= 65 && v <= 70)
            let isLower = (v >= 97 && v <= 102)
            if !(isDigit || isUpper || isLower) {
                return nil
            }
        }
        return "#" + s.uppercased()
    }

    init?(hex: String?) {
        guard let normalized = Self.normalizeHex(hex) else { return nil }
        let body = String(normalized.dropFirst())
        guard let value = UInt32(body, radix: 16) else { return nil }
        let r = Double((value >> 16) & 0xFF) / 255.0
        let g = Double((value >> 8) & 0xFF) / 255.0
        let b = Double(value & 0xFF) / 255.0
        self.init(r: r, g: g, b: b)
    }

    init(nsColor: NSColor) {
        if let srgb = nsColor.usingColorSpace(.sRGB) {
            self.init(
                r: Double(srgb.redComponent),
                g: Double(srgb.greenComponent),
                b: Double(srgb.blueComponent)
            )
        } else {
            self.init(r: 0.5, g: 0.5, b: 0.5)
        }
    }

    init(color: Color) {
        self.init(nsColor: NSColor(color))
    }

    var hexString: String {
        let ri = Int((r * 255.0).rounded())
        let gi = Int((g * 255.0).rounded())
        let bi = Int((b * 255.0).rounded())
        return String(format: "#%02X%02X%02X", min(max(ri, 0), 255), min(max(gi, 0), 255), min(max(bi, 0), 255))
    }

    var color: Color {
        Color(.sRGB, red: r, green: g, blue: b, opacity: 1.0)
    }

    func color(opacity: Double) -> Color {
        Color(.sRGB, red: r, green: g, blue: b, opacity: opacity)
    }

    var nsColor: NSColor {
        NSColor(srgbRed: CGFloat(r), green: CGFloat(g), blue: CGFloat(b), alpha: 1.0)
    }

    func nsColor(alpha: CGFloat) -> NSColor {
        NSColor(srgbRed: CGFloat(r), green: CGFloat(g), blue: CGFloat(b), alpha: alpha)
    }

    private func channelLuminance(_ c: Double) -> Double {
        let v = min(max(c, 0.0), 1.0)
        return v <= 0.04045 ? (v / 12.92) : pow((v + 0.055) / 1.055, 2.4)
    }

    var relativeLuminance: Double {
        0.2126 * channelLuminance(r) + 0.7152 * channelLuminance(g) + 0.0722 * channelLuminance(b)
    }

    func mixed(with other: RGBColor, fraction: Double) -> RGBColor {
        let t = min(max(fraction, 0.0), 1.0)
        return RGBColor(
            r: r + (other.r - r) * t,
            g: g + (other.g - g) * t,
            b: b + (other.b - b) * t
        )
    }

    static func contrastRatio(_ a: RGBColor, _ b: RGBColor) -> Double {
        let l1 = max(a.relativeLuminance, b.relativeLuminance)
        let l2 = min(a.relativeLuminance, b.relativeLuminance)
        return (l1 + 0.05) / (l2 + 0.05)
    }

    static func minContrastRatio(_ candidate: RGBColor, against backgrounds: [RGBColor]) -> Double {
        guard !backgrounds.isEmpty else { return 21.0 }
        return backgrounds.map { contrastRatio(candidate, $0) }.min() ?? 21.0
    }

    static func readableAccentText(
        from accent: RGBColor,
        against backgrounds: [RGBColor],
        isDarkSurface: Bool,
        minimumContrast: Double = 4.5
    ) -> RGBColor {
        let safetyTarget = max(minimumContrast, 4.55)
        if minContrastRatio(accent, against: backgrounds) >= safetyTarget {
            return accent
        }
        let targetPole = isDarkSurface ? RGBColor(r: 1, g: 1, b: 1) : RGBColor(r: 0, g: 0, b: 0)
        var low = 0.0
        var high = 1.0
        var best = targetPole
        for _ in 0..<24 {
            let mid = (low + high) * 0.5
            let candidate = accent.mixed(with: targetPole, fraction: mid)
            if minContrastRatio(candidate, against: backgrounds) >= safetyTarget {
                best = candidate
                high = mid
            } else {
                low = mid
            }
        }
        return best
    }
}

struct ThemeColorPair: Codable, Equatable, Sendable {
    var primaryHex: String
    var secondaryHex: String
}

// MARK: - Resolved Material & Theme Palette

enum ResolvedMaterialKind: String, Sendable {
    case solid = "solid"
    case gradient = "gradient"
    case frostedVisualEffect = "frosted_visual_effect"
    case liquidGlassEffect = "liquid_glass_effect"
    case frostedFallbackForLiquid = "frosted_fallback_for_liquid"
    case solidAccessibilityFallback = "solid_accessibility_fallback"
}

struct ResolvedThemePalette: Sendable {
    let theme: ThemeID
    let isDark: Bool
    let materialKind: ResolvedMaterialKind

    let surfaceRGB: RGBColor
    let insetRGB: RGBColor
    let borderRGB: RGBColor
    let trackRGB: RGBColor

    let primaryTextRGB: RGBColor
    let secondaryTextRGB: RGBColor

    let rawPrimaryRGB: RGBColor
    let rawSecondaryRGB: RGBColor

    let readablePrimaryTextRGB: RGBColor
    let readableSecondaryTextRGB: RGBColor

    let gradientStops: [RGBColor]

    var surfaceColor: Color { surfaceRGB.color }
    var insetColor: Color { insetRGB.color }
    var borderColor: Color { borderRGB.color }
    var trackColor: Color { materialKind == .liquidGlassEffect ? Color(nsColor: .separatorColor) : trackRGB.color }
    var primaryTextColor: Color { materialKind == .liquidGlassEffect ? Color(nsColor: .textColor) : primaryTextRGB.color }
    var secondaryTextColor: Color { materialKind == .liquidGlassEffect ? Color(nsColor: .textColor).opacity(0.68) : secondaryTextRGB.color }
    var rawPrimaryColor: Color { rawPrimaryRGB.color }
    var rawSecondaryColor: Color { rawSecondaryRGB.color }
    var readablePrimaryTextColor: Color { materialKind == .liquidGlassEffect ? Color(nsColor: .textColor) : readablePrimaryTextRGB.color }
    var readableSecondaryTextColor: Color { materialKind == .liquidGlassEffect ? Color(nsColor: .textColor).opacity(0.68) : readableSecondaryTextRGB.color }

    var statusGreenColor: Color { materialKind == .liquidGlassEffect ? Color(nsColor: .systemGreen) : statusGreenRGB.color }

    var warningOrangeColor: Color { materialKind == .liquidGlassEffect ? Color(nsColor: .systemOrange) : warningOrangeRGB.color }

    var dangerRedColor: Color { materialKind == .liquidGlassEffect ? Color(nsColor: .systemRed) : dangerRedRGB.color }

    static let liveGreenRGB = RGBColor(hex: "#24AD62")!
    static let darkLiveGreenRGB = RGBColor(hex: "#28BC6D")!

    var statusGreenRGB: RGBColor {
        let base = isDark ? Self.darkLiveGreenRGB : Self.liveGreenRGB
        return RGBColor.readableAccentText(
            from: base,
            against: [surfaceRGB, insetRGB],
            isDarkSurface: isDark,
            minimumContrast: 4.5
        )
    }

    var warningOrangeRGB: RGBColor {
        let base = isDark ? RGBColor(hex: "#FFB042")! : RGBColor(hex: "#B85600")!
        return RGBColor.readableAccentText(
            from: base,
            against: [surfaceRGB, insetRGB],
            isDarkSurface: isDark,
            minimumContrast: 4.5
        )
    }

    var dangerRedRGB: RGBColor {
        let base = isDark ? RGBColor(hex: "#FF6B6B")! : RGBColor(hex: "#C92A2A")!
        return RGBColor.readableAccentText(
            from: base,
            against: [surfaceRGB, insetRGB],
            isDarkSurface: isDark,
            minimumContrast: 4.5
        )
    }

    static func resolve(
        theme: ThemeID,
        colorPair: ThemeColorPair,
        materialKind: ResolvedMaterialKind
    ) -> ResolvedThemePalette {
        let fallbackPair = theme.defaultColorPair
        let effectivePair = theme.supportsCustomColors ? colorPair : fallbackPair
        let rawPrimary = RGBColor(hex: effectivePair.primaryHex) ?? RGBColor(hex: fallbackPair.primaryHex)!
        let rawSecondary = RGBColor(hex: effectivePair.secondaryHex) ?? RGBColor(hex: fallbackPair.secondaryHex)!

        let isDark: Bool
        let surfaceRGB: RGBColor
        let insetRGB: RGBColor
        let borderRGB: RGBColor
        let trackRGB: RGBColor
        let primaryTextRGB: RGBColor
        let secondaryTextRGB: RGBColor
        var gradientStops: [RGBColor] = []

        switch theme {
        case .cupertino:
            isDark = false
            surfaceRGB = RGBColor(hex: "#FAFAFC")!
            insetRGB = RGBColor(hex: "#EFEFF4")!
            borderRGB = RGBColor(hex: "#DEDEE5")!
            trackRGB = RGBColor(hex: "#D6D6DF")!
            primaryTextRGB = RGBColor(hex: "#242428")!
            secondaryTextRGB = RGBColor(hex: "#595963")!

        case .graphite:
            isDark = true
            surfaceRGB = RGBColor(hex: "#242427")!
            insetRGB = RGBColor(hex: "#2E2E33")!
            borderRGB = RGBColor(hex: "#39393F")!
            trackRGB = RGBColor(hex: "#45454D")!
            primaryTextRGB = RGBColor(hex: "#EDEDF0")!
            secondaryTextRGB = RGBColor(hex: "#B7B7C0")!

        case .instrument:
            isDark = true
            surfaceRGB = RGBColor(hex: "#111113")!
            insetRGB = RGBColor(hex: "#1E1E21")!
            borderRGB = RGBColor(hex: "#333337")!
            trackRGB = RGBColor(hex: "#414144")!
            primaryTextRGB = RGBColor(hex: "#EDEDED")!
            secondaryTextRGB = RGBColor(hex: "#B3B3B8")!

        case .frosted:
            isDark = true
            surfaceRGB = RGBColor(hex: "#1D222B")!
            insetRGB = RGBColor(hex: "#262C37")!
            borderRGB = RGBColor(hex: "#465062")!
            trackRGB = RGBColor(hex: "#3B4454")!
            primaryTextRGB = RGBColor(hex: "#F2F5FA")!
            secondaryTextRGB = RGBColor(hex: "#B4BECC")!

        case .liquid:
            isDark = false
            surfaceRGB = RGBColor(hex: "#EEF3FA")!
            insetRGB = RGBColor(hex: "#E2E9F4")!
            borderRGB = RGBColor(hex: "#C5D1E3")!
            trackRGB = RGBColor(hex: "#C9D4E5")!
            primaryTextRGB = RGBColor(hex: "#151A23")!
            secondaryTextRGB = RGBColor(hex: "#495364")!

        case .vibrant:
            isDark = true
            let stop1 = RGBColor(hex: "#181B34")!.mixed(with: rawPrimary, fraction: 0.10)
            let stop2 = RGBColor(hex: "#17253B")!.mixed(with: rawSecondary, fraction: 0.10)
            let stop3 = RGBColor(hex: "#221A36")!.mixed(with: rawSecondary, fraction: 0.12)
            gradientStops = [stop1, stop2, stop3]
            surfaceRGB = RGBColor(hex: "#1E223A")!
            insetRGB = RGBColor(hex: "#272C48")!
            borderRGB = RGBColor(hex: "#424B74")!
            trackRGB = RGBColor(hex: "#3B4368")!
            primaryTextRGB = RGBColor(hex: "#F5F7FF")!
            secondaryTextRGB = RGBColor(hex: "#C0C7E3")!
        }

        let checkSurfaces = [surfaceRGB, insetRGB] + gradientStops
        let readablePrimary = RGBColor.readableAccentText(
            from: rawPrimary,
            against: checkSurfaces,
            isDarkSurface: isDark,
            minimumContrast: 4.5
        )
        let readableSecondary = RGBColor.readableAccentText(
            from: rawSecondary,
            against: checkSurfaces,
            isDarkSurface: isDark,
            minimumContrast: 4.5
        )

        return ResolvedThemePalette(
            theme: theme,
            isDark: isDark,
            materialKind: materialKind,
            surfaceRGB: surfaceRGB,
            insetRGB: insetRGB,
            borderRGB: borderRGB,
            trackRGB: trackRGB,
            primaryTextRGB: primaryTextRGB,
            secondaryTextRGB: secondaryTextRGB,
            rawPrimaryRGB: rawPrimary,
            rawSecondaryRGB: rawSecondary,
            readablePrimaryTextRGB: readablePrimary,
            readableSecondaryTextRGB: readableSecondary,
            gradientStops: gradientStops
        )
    }
}

// MARK: - Appearance Preference Store

struct PersistedAppearanceSettings: Codable {
    var theme: String
    var language: String
    var customColors: [String: ThemeColorPair]
}

final class AppearanceStore: ObservableObject {
    static let storageKey = "TokenPilot.AppearanceSettings.v2"

    @Published var theme: ThemeID {
        didSet { persistIfNeeded() }
    }
    @Published var language: AppLanguage {
        didSet { persistIfNeeded() }
    }
    @Published private(set) var customColorsByTheme: [ThemeID: ThemeColorPair]
    @Published var reduceTransparency: Bool
    @Published var increaseContrast: Bool

    private let userDefaults: UserDefaults?
    private let persistsChanges: Bool
    private var isRestoring = false
    private var workspaceObserver: NSObjectProtocol?

    static let isLiquidGlassAvailable: Bool = {
        guard let cls = NSClassFromString("NSGlassEffectView") as? NSView.Type else {
            return false
        }
        let instance = cls.init(frame: .zero)
        return instance.responds(to: NSSelectorFromString("setCornerRadius:"))
            && instance.responds(to: NSSelectorFromString("setTintColor:"))
    }()

    init(
        userDefaults: UserDefaults? = .standard,
        persistsChanges: Bool = true,
        initialReduceTransparency: Bool? = nil,
        initialIncreaseContrast: Bool? = nil
    ) {
        self.userDefaults = userDefaults
        self.persistsChanges = persistsChanges
        self.reduceTransparency = initialReduceTransparency ?? NSWorkspace.shared.accessibilityDisplayShouldReduceTransparency
        self.increaseContrast = initialIncreaseContrast ?? NSWorkspace.shared.accessibilityDisplayShouldIncreaseContrast

        var initialTheme: ThemeID = .defaultTheme
        var initialLanguage: AppLanguage = .defaultLanguage
        var initialColors: [ThemeID: ThemeColorPair] = [:]
        for item in ThemeID.allCases {
            initialColors[item] = item.defaultColorPair
        }

        if let defaults = userDefaults,
           let rawData = defaults.data(forKey: Self.storageKey),
           let decoded = try? JSONDecoder().decode(PersistedAppearanceSettings.self, from: rawData) {
            if let validTheme = ThemeID.resolve(decoded.theme) {
                initialTheme = validTheme
            }
            if let validLang = AppLanguage.resolve(decoded.language) {
                initialLanguage = validLang
            }
            for (rawKey, pair) in decoded.customColors {
                guard let keyTheme = ThemeID.resolve(rawKey) else { continue }
                if !keyTheme.supportsCustomColors {
                    initialColors[keyTheme] = keyTheme.defaultColorPair
                    continue
                }
                let def = keyTheme.defaultColorPair
                let p = RGBColor.normalizeHex(pair.primaryHex) ?? def.primaryHex
                let s = RGBColor.normalizeHex(pair.secondaryHex) ?? def.secondaryHex
                initialColors[keyTheme] = ThemeColorPair(primaryHex: p, secondaryHex: s)
            }
        }

        self.theme = initialTheme
        self.language = initialLanguage
        self.customColorsByTheme = initialColors

        if initialReduceTransparency == nil && initialIncreaseContrast == nil {
            workspaceObserver = NSWorkspace.shared.notificationCenter.addObserver(
                forName: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                guard let self else { return }
                self.reduceTransparency = NSWorkspace.shared.accessibilityDisplayShouldReduceTransparency
                self.increaseContrast = NSWorkspace.shared.accessibilityDisplayShouldIncreaseContrast
            }
        }
    }

    deinit {
        if let workspaceObserver {
            NSWorkspace.shared.notificationCenter.removeObserver(workspaceObserver)
        }
    }

    var shouldUseSolidGlassFallback: Bool {
        reduceTransparency || increaseContrast
    }

    var resolvedMaterialKind: ResolvedMaterialKind {
        switch theme {
        case .cupertino, .graphite, .instrument:
            return .solid
        case .vibrant:
            return .gradient
        case .frosted:
            return shouldUseSolidGlassFallback ? .solidAccessibilityFallback : .frostedVisualEffect
        case .liquid:
            if shouldUseSolidGlassFallback {
                return .solidAccessibilityFallback
            }
            return Self.isLiquidGlassAvailable ? .liquidGlassEffect : .frostedFallbackForLiquid
        }
    }

    func colorPair(for targetTheme: ThemeID? = nil) -> ThemeColorPair {
        let t = targetTheme ?? theme
        if !t.supportsCustomColors {
            return t.defaultColorPair
        }
        return customColorsByTheme[t] ?? t.defaultColorPair
    }

    var currentColorPair: ThemeColorPair {
        colorPair(for: theme)
    }

    var palette: ResolvedThemePalette {
        ResolvedThemePalette.resolve(
            theme: theme,
            colorPair: currentColorPair,
            materialKind: resolvedMaterialKind
        )
    }

    func palette(for targetTheme: ThemeID) -> ResolvedThemePalette {
        let kind: ResolvedMaterialKind = {
            switch targetTheme {
            case .cupertino, .graphite, .instrument: return .solid
            case .vibrant: return .gradient
            case .frosted: return shouldUseSolidGlassFallback ? .solidAccessibilityFallback : .frostedVisualEffect
            case .liquid:
                if shouldUseSolidGlassFallback { return .solidAccessibilityFallback }
                return Self.isLiquidGlassAvailable ? .liquidGlassEffect : .frostedFallbackForLiquid
            }
        }()
        return ResolvedThemePalette.resolve(
            theme: targetTheme,
            colorPair: colorPair(for: targetTheme),
            materialKind: kind
        )
    }

    @discardableResult
    func setPrimaryHex(_ rawHex: String, for targetTheme: ThemeID? = nil) -> Bool {
        let t = targetTheme ?? theme
        guard t.supportsCustomColors, let validHex = RGBColor.normalizeHex(rawHex) else {
            return false
        }
        var pair = colorPair(for: t)
        guard pair.primaryHex != validHex else { return true }
        pair.primaryHex = validHex
        customColorsByTheme[t] = pair
        persistIfNeeded()
        return true
    }

    @discardableResult
    func setSecondaryHex(_ rawHex: String, for targetTheme: ThemeID? = nil) -> Bool {
        let t = targetTheme ?? theme
        guard t.supportsCustomColors, let validHex = RGBColor.normalizeHex(rawHex) else {
            return false
        }
        var pair = colorPair(for: t)
        guard pair.secondaryHex != validHex else { return true }
        pair.secondaryHex = validHex
        customColorsByTheme[t] = pair
        persistIfNeeded()
        return true
    }

    var canResetCurrentThemeColors: Bool {
        guard theme.supportsCustomColors else { return false }
        return colorPair(for: theme) != theme.defaultColorPair
    }

    func resetColorsForCurrentTheme() {
        guard theme.supportsCustomColors else { return }
        customColorsByTheme[theme] = theme.defaultColorPair
        persistIfNeeded()
    }

    private func persistIfNeeded() {
        guard persistsChanges, !isRestoring, let defaults = userDefaults else { return }
        var map: [String: ThemeColorPair] = [:]
        for item in ThemeID.allCases {
            map[item.rawValue] = colorPair(for: item)
        }
        let payload = PersistedAppearanceSettings(
            theme: theme.rawValue,
            language: language.rawValue,
            customColors: map
        )
        if let data = try? JSONEncoder().encode(payload) {
            defaults.set(data, forKey: Self.storageKey)
        }
    }

    func L(_ en: String, _ zh: String) -> String {
        language.L(en, zh)
    }
}

// MARK: - Localization & Formatting Helper

struct TokenPilotLocalizer {
    let language: AppLanguage
    let timeZone: TimeZone

    init(language: AppLanguage, timeZone: TimeZone = .current) {
        self.language = language
        self.timeZone = timeZone
    }

    func L(_ en: String, _ zh: String) -> String {
        language.L(en, zh)
    }

    func formatTimeHM(_ date: Date) -> String {
        let f = DateFormatter()
        f.locale = language.locale
        f.timeZone = timeZone
        f.dateFormat = "HH:mm"
        return f.string(from: date)
    }

    func formatTimeHMS(_ date: Date) -> String {
        let f = DateFormatter()
        f.locale = language.locale
        f.timeZone = timeZone
        f.dateFormat = "HH:mm:ss"
        return f.string(from: date)
    }

    func formatWeekReset(_ date: Date) -> String {
        let f = DateFormatter()
        f.locale = language.locale
        f.timeZone = timeZone
        f.dateFormat = "EEE HH:mm"
        return f.string(from: date)
    }

    func formatReportDate(_ date: Date) -> String {
        let f = DateFormatter()
        f.locale = language.locale
        f.timeZone = timeZone
        f.dateFormat = "MM/dd HH:mm"
        return f.string(from: date)
    }

    func windowTitle(isWeekly: Bool) -> String {
        isWeekly ? L("Weekly", "周配额") : L("5-hour", "5 小时")
    }

    func windowShortPrefix(isWeekly: Bool) -> String {
        isWeekly ? L("Wk", "周") : L("5h", "5小时")
    }

    func localizeCollectorMessage(_ raw: String?) -> String? {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else {
            return nil
        }
        if language == .english { return trimmed }
        let lower = trimmed.lowercased()
        if lower == "collector stopped · click refresh to reconnect" {
            return "收集器已停止 · 点击刷新重新连接"
        }
        if lower == "collector data could not be read · refresh to reconnect" {
            return "无法读取收集器数据 · 请刷新重连"
        }
        if lower == "collector unavailable" {
            return "收集器不可用"
        }
        if lower.hasPrefix("cannot start collector:") {
            let suffix = trimmed.dropFirst("Cannot start collector:".count).trimmingCharacters(in: .whitespaces)
            return suffix.isEmpty ? "无法启动收集器" : "无法启动收集器：\(suffix)"
        }
        return trimmed
    }

    func localizeGeminiStatus(
        status: String?,
        message: String?,
        available: Bool
    ) -> (text: String, isAlert: Bool, tooltip: String)? {
        let rawStatus = (status ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let rawMessage = (message ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let combinedLower = (rawStatus + " " + rawMessage).lowercased()
        let isAlert = !available
            || combinedLower.contains("error")
            || combinedLower.contains("fail")
            || combinedLower.contains("unavail")

        let baseEnglish: String = {
            if !rawMessage.isEmpty {
                return rawMessage.lowercased().contains("gemini") ? rawMessage : "Gemini: \(rawMessage)"
            }
            if !rawStatus.isEmpty && (isAlert || rawStatus.lowercased() != "ok") {
                return "Gemini: \(rawStatus)"
            }
            if !available {
                return "Gemini: Unavailable"
            }
            return ""
        }()

        guard !baseEnglish.isEmpty else { return nil }
        if language == .english {
            return (baseEnglish, isAlert, baseEnglish)
        }

        let msgLower = rawMessage.lowercased()
        let statusLower = rawStatus.lowercased()
        var localized: String?

        if statusLower == "paused" {
            localized = "Gemini 已暂停 · 用量已记录"
        } else if statusLower == "error" {
            localized = "Gemini 请求失败 · 暂无更新用量"
        } else if msgLower.contains("worker completed") || statusLower == "completed" {
            localized = "Gemini 工作进程已完成"
        } else if msgLower.contains("worker running") || statusLower == "running" || statusLower == "in_progress" {
            localized = "Gemini 工作进程运行中"
        } else if msgLower.contains("no tracked") || msgLower.contains("no runs today") || statusLower == "idle" || statusLower == "none" {
            localized = "今日暂无已追踪的 Gemini 运行"
        } else if msgLower.contains("token ceiling") || msgLower.contains("bounded stop") {
            localized = "Gemini 已达单次 Token 上限并安全停止"
        } else if msgLower.contains("worker failed") || statusLower == "failed" {
            if rawMessage.isEmpty || msgLower == "gemini worker failed" || msgLower == "worker failed" {
                localized = "Gemini 工作进程失败"
            }
        } else if !available && rawMessage.isEmpty && (rawStatus.isEmpty || statusLower == "unavailable") {
            localized = "Gemini：不可用"
        }

        if let localized {
            let tip = localized == baseEnglish ? localized : "\(localized) (\(baseEnglish))"
            return (localized, isAlert, tip)
        }
        return (baseEnglish, isAlert, baseEnglish)
    }

    func localizeSelectionReason(_ raw: String?) -> String {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else {
            return L(
                "Forecast warming up; lower remaining quota is provisional",
                "预测预热中；暂按较低剩余配额显示"
            )
        }
        if language == .english { return trimmed }
        let lower = trimmed.lowercased()
        if lower == "showing the quota that has already reached its limit" { return "显示已达上限的配额窗口" }
        if lower == "quota data unavailable" { return "配额数据不可用" }
        if lower.contains("95%") && lower.contains("weekly") {
            return "5 小时配额剩余超过 95%，显示周配额"
        }
        if lower.contains("warming up") || lower.contains("provisional") {
            return "预测预热中；暂按较低剩余配额显示"
        }
        if lower.contains("earliest") && (lower.contains("depletion") || lower.contains("exhaustion") || lower.contains("projection")) {
            return "已选择预计最早耗尽的配额窗口"
        }
        if lower.contains("reset") && lower.contains("first") {
            return "下次配额重置早于预计耗尽时间"
        }
        if lower.contains("lower remaining") {
            return "显示剩余比例较低的配额窗口"
        }
        return trimmed
    }

    func localizeWindowMessage(_ raw: String?) -> String? {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else {
            return nil
        }
        if language == .english { return trimmed }
        let lower = trimmed.lowercased()
        let exact: [String:String] = [
            "quota unavailable":"配额不可用", "waiting for fresh quota data":"等待最新配额数据", "usage limit reached":"用量已达上限", "collecting 15m quota history":"正在收集 15 分钟配额历史", "reset detected · collecting a new 15m trend":"检测到重置 · 正在收集新的 15 分钟趋势", "no measurable quota burn in 15m":"过去 15 分钟无明显配额消耗", "scheduled reset comes before projected depletion":"计划重置早于预计耗尽"
        ]
        if let translation = exact[lower] { return translation }
        if lower.hasPrefix("estimate from the last 15m:") {
            let rate = trimmed.components(separatedBy: ":").dropFirst().joined(separator: ":").components(separatedBy: " ").first(where: {Double($0) != nil}) ?? "—"
            return "基于近 15 分钟：每分钟消耗 \(rate) 个配额百分点；假设速率不变"
        }
        if lower.contains("next reset comes first") || lower.contains("reset before projection") || lower.contains("resets before") {
            return "下次重置早于耗尽"
        }
        if lower.contains("warming up") || lower.contains("not enough history") || lower.contains("insufficient") {
            return "预测预热中（需 15 分钟趋势）"
        }
        if lower.contains("zero burn") || lower.contains("no recent") || lower.contains("steady") {
            return "近期无明显配额消耗"
        }
        return trimmed
    }

    func localizeForecastDetail(_ raw: String, nextResetWindow: String?) -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, language == .chinese else { return trimmed }
        let lower = trimmed.lowercased()
        if lower == "5-hour resets first · weekly quota remains"
            || (lower.contains("5-hour resets first") && lower.contains("weekly")) {
            return "5 小时窗口先重置 · 周配额继续计入"
        }
        if lower.contains("weekly resets first") && lower.contains("5-hour") {
            return "周配额窗口先重置 · 5 小时配额继续计入"
        }
        if lower == "15m trend · cache included · assumes unchanged pace" { return "近 15 分钟趋势 · 含缓存 · 假设速率不变" }
        if lower.contains("resets before projected depletion") {
            if lower.contains("5-hour") && lower.contains("weekly") { return "5 小时与周配额均先于预计耗尽重置" }
            return lower.contains("weekly") ? "周配额先于预计耗尽重置" : "5 小时配额先于预计耗尽重置"
        }
        if lower.contains("assuming unchanged") || lower.contains("unchanged pace") {
            return "按过去 15 分钟消耗速率维持不变估算"
        }
        if lower.contains("not enough") && lower.contains("history") {
            return "尚未积累完整的 15 分钟历史样本"
        }
        if lower.contains("zero burn") || lower.contains("no depletion") || lower.contains("no usage") {
            return "过去 15 分钟内配额消耗为 0"
        }
        if let nextResetWindow, lower.contains("resets first") {
            return nextResetWindow == "weekly"
                ? "周配额窗口先重置"
                : "5 小时窗口先重置 · 周配额继续计入"
        }
        return trimmed
    }

    func localizeForecastHeadline(
        forecast: UsageForecast,
        formattedETA: String?
    ) -> String {
        let raw = forecast.headline.trimmingCharacters(in: .whitespacesAndNewlines)
        let status = forecast.status.lowercased()
        let lower = raw.lowercased()

        if status == "reset_before_projection" {
            return L("Next reset comes first", "下次重置早于耗尽")
        }

        if status == "projected" {
            var base: String
            if language == .chinese {
                if lower.contains("5-hour") || lower.contains("5 hour") || forecast.display_window == "five_hour" {
                    base = "预计 5 小时配额耗尽"
                } else if lower.contains("weekly") || forecast.display_window == "weekly" {
                    base = "预计周配额耗尽"
                } else if raw.isEmpty || lower == "usage forecast" || lower.contains("projected") {
                    base = "预计配额耗尽时间"
                } else {
                    base = raw
                }
            } else {
                base = raw.isEmpty ? "Usage forecast" : raw
            }
            if let eta = formattedETA, !base.contains(eta) {
                base += " (~\(eta))"
            }
            return base
        }

        if language == .chinese {
            if status == "learning" { return "正在学习 15 分钟配额趋势" }
            if status == "unavailable" { return "等待配额数据" }
            if status == "warming_up" || status == "insufficient_history"
                || lower.contains("warming up") || lower.contains("not enough history") || lower.contains("waiting for 15m") {
                return "预测预热中（收集 15 分钟趋势）"
            }
            if status == "zero_burn" || status == "idle" || status == "no_recent_burn" || status == "steady"
                || lower.contains("zero burn") || lower.contains("no recent") || lower.contains("no depletion") {
                return "近期无明显配额消耗"
            }
            if status == "limit_reached" || status == "exhausted" || status == "depleted"
                || lower.contains("limit reached") || lower.contains("exhausted") {
                if lower.contains("5-hour") || forecast.display_window == "five_hour" {
                    return "5 小时配额已达上限"
                }
                if lower.contains("weekly") || forecast.display_window == "weekly" {
                    return "周配额已达上限"
                }
                return "配额已达上限"
            }
            if status == "stale" || status == "reset_crossed" || lower.contains("stale") {
                return "等待最新配额数据…"
            }
        }

        if raw.isEmpty {
            return L("Usage forecast", "用量预测")
        }
        return raw
    }

    func localizeNextResetHeadline(_ raw: String?, hasError: Bool) -> String {
        let clean = (raw ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if clean.isEmpty {
            if hasError {
                return L("Cached data stale", "缓存数据已过期")
            }
            return L("Loading…", "加载中…")
        }
        if language == .chinese {
            let lower = clean.lowercased()
            if lower == "no reset time announced" || lower == "no reset announced" {
                return "暂未公布重置时间"
            }
            if lower == "loading…" || lower == "loading..." || lower == "loading" {
                return "加载中…"
            }
            if lower == "cached data stale" {
                return "缓存数据已过期"
            }
        }
        return clean
    }
}

// MARK: - Native Glass & Surface Background Views

final class GlassHostContainerView: NSView {
    private var currentKind: ResolvedMaterialKind?
    private var effectView: NSView?

    override var isOpaque: Bool { false }
    override var mouseDownCanMoveWindow: Bool { true }
    // This material is decorative; controls and window-drag areas above it own all events.
    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    func update(kind: ResolvedMaterialKind, cornerRadius: CGFloat, tintColor: NSColor? = nil) {
        wantsLayer = true
        layer?.backgroundColor = NSColor.clear.cgColor
        layer?.cornerRadius = cornerRadius
        layer?.cornerCurve = .continuous
        layer?.masksToBounds = kind != .liquidGlassEffect

        if currentKind != kind || effectView == nil {
            effectView?.removeFromSuperview()
            effectView = makeEffectView(for: kind)
            currentKind = kind
            if let ev = effectView {
                ev.frame = bounds
                ev.autoresizingMask = [.width, .height]
                addSubview(ev)
            }
        }

        guard let ev = effectView else { return }
        ev.frame = bounds

        switch kind {
        case .liquidGlassEffect:
            ev.appearance = NSAppearance(named: .aqua)
            if ev.responds(to: NSSelectorFromString("setCornerRadius:")) {
                ev.setValue(Double(cornerRadius), forKey: "cornerRadius")
            }
            if ev.responds(to: NSSelectorFromString("setTintColor:")) {
                ev.setValue(tintColor, forKey: "tintColor")
            }
        case .frostedVisualEffect:
            if let vev = ev as? NSVisualEffectView {
                vev.material = .hudWindow
                vev.blendingMode = .behindWindow
                vev.state = .active
                vev.appearance = NSAppearance(named: .darkAqua)
                vev.wantsLayer = true
                vev.layer?.cornerRadius = cornerRadius
                vev.layer?.cornerCurve = .continuous
                vev.layer?.masksToBounds = true
            }
        case .frostedFallbackForLiquid:
            if let vev = ev as? NSVisualEffectView {
                vev.material = .popover
                vev.blendingMode = .behindWindow
                vev.state = .active
                vev.appearance = NSAppearance(named: .aqua)
                vev.wantsLayer = true
                vev.layer?.cornerRadius = cornerRadius
                vev.layer?.cornerCurve = .continuous
                vev.layer?.masksToBounds = true
            }
        default:
            break
        }
    }

    private func makeEffectView(for kind: ResolvedMaterialKind) -> NSView? {
        switch kind {
        case .liquidGlassEffect:
            if let cls = NSClassFromString("NSGlassEffectView") as? NSView.Type {
                return cls.init(frame: bounds)
            }
            return NSVisualEffectView(frame: bounds)
        case .frostedVisualEffect, .frostedFallbackForLiquid:
            return NSVisualEffectView(frame: bounds)
        default:
            return nil
        }
    }
}

struct GlassSurfaceRepresentable: NSViewRepresentable {
    let kind: ResolvedMaterialKind
    let cornerRadius: CGFloat
    var tintColor: NSColor? = nil

    func makeNSView(context: Context) -> GlassHostContainerView {
        let view = GlassHostContainerView(frame: .zero)
        view.update(kind: kind, cornerRadius: cornerRadius, tintColor: tintColor)
        return view
    }

    func updateNSView(_ nsView: GlassHostContainerView, context: Context) {
        nsView.update(kind: kind, cornerRadius: cornerRadius, tintColor: tintColor)
    }
}

struct WidgetSurfaceBackground: View {
    let palette: ResolvedThemePalette
    let cornerRadius: CGFloat

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        ZStack {
            switch palette.materialKind {
            case .liquidGlassEffect:
                GlassSurfaceRepresentable(
                    kind: .liquidGlassEffect,
                    cornerRadius: cornerRadius,
                    tintColor: nil
                )
            case .frostedFallbackForLiquid:
                GlassSurfaceRepresentable(
                    kind: .frostedFallbackForLiquid,
                    cornerRadius: cornerRadius,
                    tintColor: nil
                )
            case .frostedVisualEffect:
                GlassSurfaceRepresentable(
                    kind: .frostedVisualEffect,
                    cornerRadius: cornerRadius,
                    tintColor: palette.rawPrimaryRGB.nsColor(alpha: 0.12)
                )
                shape.fill(
                    LinearGradient(
                        colors: [
                            palette.surfaceRGB.color(opacity: 0.56),
                            palette.surfaceRGB.mixed(with: palette.rawPrimaryRGB, fraction: 0.08).color(opacity: 0.64)
                        ],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
            case .gradient:
                shape.fill(
                    LinearGradient(
                        colors: palette.gradientStops.isEmpty
                            ? [palette.surfaceColor]
                            : palette.gradientStops.map(\.color),
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
            case .solid, .solidAccessibilityFallback:
                shape.fill(palette.surfaceColor)
            }
        }
        .clipShape(shape)
        .overlay {
            if palette.materialKind != .liquidGlassEffect {
                shape.strokeBorder(palette.borderColor, lineWidth: 1)
            }
        }
    }
}


// MARK: - Customization Settings Window UI

struct CustomizationView: View {
    @ObservedObject var appearance: AppearanceStore
    private func colorBinding(primary: Bool) -> Binding<Color> {
        Binding(get: {
            let pair = appearance.currentColorPair
            return (RGBColor(hex: primary ? pair.primaryHex : pair.secondaryHex) ?? RGBColor(r:0,g:0,b:0)).color
        }, set: { color in
            let hex = RGBColor(color:color).hexString
            if primary { appearance.setPrimaryHex(hex) } else { appearance.setSecondaryHex(hex) }
        })
    }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment:.leading,spacing:6) {
                    Text(appearance.L("Customize TokenPilot", "自定义 TokenPilot")).font(.system(size:20,weight:.semibold))
                    Text(appearance.L("Changes appear immediately and save automatically.", "更改即时生效，并自动保存。"))
                        .font(.system(size:12)).foregroundStyle(.secondary)
                }
                HStack {
                    Text(appearance.L("Language", "界面语言")).font(.system(size:13,weight:.medium))
                    Spacer()
                    Picker(appearance.L("Language", "界面语言"),selection:$appearance.language) {
                        ForEach(AppLanguage.allCases) { language in Text(language.displayName).tag(language) }
                    }.pickerStyle(.segmented).labelsHidden().frame(width:210)
                }
                VStack(alignment:.leading,spacing:12) {
                    Text(appearance.L("Theme", "主题")).font(.system(size:13,weight:.medium))
                    LazyVGrid(columns:[GridItem(.flexible()),GridItem(.flexible()),GridItem(.flexible())],spacing:12) {
                        ForEach(ThemeID.allCases) { theme in
                            ThemeTileButton(theme:theme,palette:appearance.palette(for:theme),language:appearance.language,isSelected:appearance.theme==theme) { appearance.theme=theme }
                        }
                    }
                    if appearance.theme.usesGlassMaterial && appearance.shouldUseSolidGlassFallback {
                        Text(appearance.L("Using a solid surface to match your macOS accessibility settings.", "已根据 macOS 辅助功能设置，改用清晰的纯色背景。"))
                            .font(.system(size:11)).foregroundStyle(.secondary)
                    } else if appearance.theme == .liquid && !AppearanceStore.isLiquidGlassAvailable {
                        Text(appearance.L("Liquid Glass requires macOS 26 or later. Frosted Glass is active on this Mac.", "液态玻璃需要 macOS 26 或更新系统；此 Mac 当前使用磨砂玻璃。"))
                            .font(.system(size:11)).foregroundStyle(.secondary)
                    }
                }
                VStack(alignment:.leading,spacing:12) {
                    HStack {
                        Text(appearance.L("Accent colors", "强调色")).font(.system(size:13,weight:.medium))
                        Spacer()
                        Button(appearance.L("Reset colors", "重置颜色")) { appearance.resetColorsForCurrentTheme() }
                            .controlSize(.small).disabled(!appearance.canResetCurrentThemeColors)
                    }
                    HStack(spacing:20) {
                        accentPicker(primary:true)
                        accentPicker(primary:false)
                    }
                    if !appearance.theme.supportsCustomColors {
                        Label(appearance.L("Instrument keeps a fixed monochrome palette.", "精密仪表主题使用固定的单色方案。"),systemImage:"lock")
                            .font(.system(size:11)).foregroundStyle(.secondary)
                    } else {
                        Text(appearance.L("Colors are saved separately for each theme. Accent text adapts for readability.", "每个主题单独保存配色；强调色文本会自动调整以保持清晰。"))
                            .font(.system(size:11)).foregroundStyle(.secondary)
                    }
                }
                .padding(16)
                .background(RoundedRectangle(cornerRadius:12).fill(Color(nsColor:.controlBackgroundColor)))
            }.padding(24)
        }.frame(minWidth:500,idealWidth:540,maxWidth:.infinity,minHeight:570,idealHeight:600)
        .background(Color(nsColor:.windowBackgroundColor))
    }
    private func accentPicker(primary:Bool) -> some View {
        VStack(alignment:.leading,spacing:6) {
            ColorPicker(appearance.L(primary ? "Primary" : "Secondary", primary ? "主强调色" : "次强调色"),selection:colorBinding(primary:primary),supportsOpacity:false)
                .font(.system(size:12)).disabled(!appearance.theme.supportsCustomColors)
            Text(primary ? appearance.currentColorPair.primaryHex : appearance.currentColorPair.secondaryHex)
                .font(.system(size:11,design:.monospaced)).foregroundStyle(.secondary)
        }.frame(maxWidth:.infinity,alignment:.leading)
    }
}

private struct ThemeTileButton: View {
    let theme: ThemeID
    let palette: ResolvedThemePalette
    let language: AppLanguage
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 8) {
                swatchMiniature
                HStack(spacing: 6) {
                    Text(theme.displayName(in: language))
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.primary)
                        .lineLimit(1)
                    Spacer(minLength: 4)
                    Image(systemName: isSelected ? "checkmark.circle.fill" : "circle")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(isSelected ? Color.accentColor : Color.secondary.opacity(0.45))
                }

            }
            .padding(10)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(Color(nsColor: .controlBackgroundColor))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(
                        isSelected ? Color.accentColor : Color.primary.opacity(0.10),
                        lineWidth: isSelected ? 2 : 1
                    )
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(theme.summary(in:language))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(theme.displayName(in: language)). \(theme.summary(in: language))")
        .accessibilityAddTraits(isSelected ? [.isSelected] : [])
    }

    private var swatchMiniature: some View {
        let shape = RoundedRectangle(cornerRadius: 8, style: .continuous)
        return ZStack {
            if theme == .vibrant && !palette.gradientStops.isEmpty {
                shape.fill(
                    LinearGradient(
                        colors: palette.gradientStops.map(\.color),
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
            } else {
                shape.fill(palette.surfaceColor)
            }

            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 4) {
                    Circle()
                        .fill(palette.statusGreenRGB.color)
                        .frame(width: 5, height: 5)
                    RoundedRectangle(cornerRadius: 2)
                        .fill(palette.primaryTextColor.opacity(0.75))
                        .frame(width: 42, height: 4)
                    Spacer()
                    if theme.usesGlassMaterial {
                        Image(systemName:theme == .liquid ? "drop" : "sparkles")
                            .font(.system(size: 11))
                            .foregroundStyle(palette.secondaryTextColor)
                    }
                }

                switch theme.tokenLayout {
                case .ledgerRows:
                    VStack(spacing: 4) {
                        HStack {
                            RoundedRectangle(cornerRadius: 2)
                                .fill(palette.secondaryTextColor.opacity(0.7))
                                .frame(width: 34, height: 5)
                            Spacer()
                            RoundedRectangle(cornerRadius: 2)
                                .fill(palette.primaryTextColor)
                                .frame(width: 30, height: 7)
                        }
                        HStack {
                            RoundedRectangle(cornerRadius: 2)
                                .fill(palette.secondaryTextColor.opacity(0.7))
                                .frame(width: 34, height: 5)
                            Spacer()
                            RoundedRectangle(cornerRadius: 2)
                                .fill(palette.primaryTextColor)
                                .frame(width: 24, height: 7)
                        }
                    }
                case .openGridMono:
                    HStack(spacing: 8) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("51M")
                                .fixedSize()
                                .font(.system(size: 11, weight: .semibold, design: .monospaced))
                                .foregroundStyle(palette.primaryTextColor)
                            RoundedRectangle(cornerRadius: 1.5)
                                .fill(palette.secondaryTextColor.opacity(0.5))
                                .frame(width: 36, height: 3)
                        }
                        Rectangle()
                            .fill(palette.borderColor)
                            .frame(width: 1, height: 20)
                        VStack(alignment: .leading, spacing: 3) {
                            Text("148K")
                                .fixedSize()
                                .font(.system(size: 11, weight: .semibold, design: .monospaced))
                                .foregroundStyle(palette.primaryTextColor)
                            RoundedRectangle(cornerRadius: 1.5)
                                .fill(palette.secondaryTextColor.opacity(0.5))
                                .frame(width: 32, height: 3)
                        }
                        Spacer(minLength: 0)
                    }
                case .twoColumns:
                    HStack(spacing: 12) {
                        VStack(alignment: .leading, spacing: 3) {
                            RoundedRectangle(cornerRadius: 2)
                                .fill(palette.primaryTextColor)
                                .frame(width: 38, height: 9)
                            RoundedRectangle(cornerRadius: 1.5)
                                .fill(palette.secondaryTextColor.opacity(0.6))
                                .frame(width: 30, height: 3)
                        }
                        VStack(alignment: .leading, spacing: 3) {
                            RoundedRectangle(cornerRadius: 2)
                                .fill(palette.primaryTextColor)
                                .frame(width: 32, height: 9)
                            RoundedRectangle(cornerRadius: 1.5)
                                .fill(palette.secondaryTextColor.opacity(0.6))
                                .frame(width: 26, height: 3)
                        }
                        Spacer(minLength: 0)
                    }
                }

                GeometryReader { geo in
                    HStack(spacing: 0) {
                        Rectangle()
                            .fill(palette.rawPrimaryColor)
                            .frame(width: geo.size.width * 0.68)
                        Rectangle()
                            .fill(palette.rawSecondaryColor)
                    }
                    .clipShape(Capsule())
                }
                .frame(height: 4)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
        }
        .frame(height: 60)
        .overlay(
            shape.strokeBorder(palette.borderColor, lineWidth: 1)
        )
    }
}
