import AppKit
import SwiftUI
import Combine

struct Usage: Decodable {
    var total_tokens: Double = 0
    var cached_tokens: Double = 0
    var available: Bool = false
    var status: String?
    var status_message: String?
    var last_usage_at: String?
}
struct Routing: Decodable { var gemini_percent: Int }
struct WindowLimit: Decodable { var used_percent: Double; var resets_at: Double? }
struct Limits: Decodable {
    var five_hour: WindowLimit?
    var weekly: WindowLimit?
    var bucket: String?
    var fetched_at: String?
    var stale: Bool?
    var error: String?
}
struct ForecastWindow: Decodable { var status: String; var message: String; var exhaustion_at: Double? }
struct UsageForecast: Decodable {
    var status: String; var headline: String; var detail: String
    var exhaustion_at: Double?; var tokens_per_minute: Double?
    var five_hour: ForecastWindow?; var weekly: ForecastWindow?
    var display_window: String?; var selection_reason: String?
    var next_reset_window: String?
}
struct NextResetInfo: Decodable {
    var headline: String; var url: String
    var latest_report_at: String?; var fetched_at: String?; var error: String?
    var source_delayed: Bool; var stale: Bool
    var probability_24h: Double?
    var probability_48h: Double?
    var probability_72h: Double?
    var expected_by: String?
    var signal_url: String?
    var source_as_of: String?
    var expires_at: String?
    var announcement_state: String?
}
struct Snapshot: Decodable {
    var timestamp: String
    var codex: Usage
    var gemini: Usage
    var routing: Routing
    var limits: Limits
    var warnings: [String]
    var forecast: UsageForecast?
    var nextreset: NextResetInfo?
}
struct Runtime: Decodable { var python: String; var codex_home: String; var codex_binary: String; var bridge_path: String? }

func compact(_ value: Double) -> String {
    if value >= 1_000_000 { return String(format: "%.1fM", value / 1_000_000) }
    if value >= 1_000 { return String(format: "%.1fK", value / 1_000).replacingOccurrences(of: ".0K", with: "K") }
    return String(format: "%.0f", value)
}

final class FloatModel: ObservableObject {
    @Published var snapshot: Snapshot?
    @Published var preference: Double = 75
    @Published var connected = false
    @Published var message: String?
    @Published var editing = false
    @Published var pending: Int?
    @Published var collapsed = UserDefaults.standard.bool(forKey: "TokenPilotCollapsed")
    private var process: Process?
    private var input: FileHandle?
    private var buffer = Data()
    private let queue = DispatchQueue(label: "routing-float.backend")

    func launch() {
        guard process == nil else { return }
        do {
            let resources = Bundle.main.resourceURL!
            let runtime = try JSONDecoder().decode(Runtime.self, from: Data(contentsOf: resources.appendingPathComponent("runtime.json")))
            let task = Process()
            let out = Pipe(), into = Pipe()
            task.executableURL = URL(fileURLWithPath: runtime.python)
            task.arguments = [runtime.bridge_path ?? resources.appendingPathComponent("bridge.py").path, "--serve", "--codex-home", runtime.codex_home, "--codex-binary", runtime.codex_binary, "--timezone", TimeZone.current.identifier]
            task.standardInput = into
            task.standardOutput = out
            task.standardError = FileHandle.nullDevice
            var env = ProcessInfo.processInfo.environment
            env["PYTHONUNBUFFERED"] = "1"
            env.removeValue(forKey: "GEMINI_API_KEY")
            task.environment = env
            task.terminationHandler = { [weak self] _ in
                DispatchQueue.main.async {
                    self?.connected = false
                    self?.message = "Collector stopped · click refresh to reconnect"
                    self?.process = nil
                    self?.input = nil
                }
            }
            try task.run()
            input = into.fileHandleForWriting
            process = task
            let reader = out.fileHandleForReading
            queue.async { [weak self] in
                while true {
                    let data = reader.availableData
                    if data.isEmpty { break }
                    self?.receive(data)
                }
            }
        } catch {
            message = "Cannot start collector: \(error.localizedDescription)"
            connected = false
        }
    }

    private func receive(_ data: Data) {
        buffer.append(data)
        while let newline = buffer.firstIndex(of: 10) {
            let line = buffer.prefix(upTo: newline)
            buffer.removeSubrange(...newline)
            if let value = try? JSONDecoder().decode(Snapshot.self, from: line) {
                DispatchQueue.main.async { [weak self] in
                    guard let self else { return }
                    self.snapshot = value
                    self.connected = true
                    if !self.editing && self.pending == nil { self.preference = Double(value.routing.gemini_percent) }
                    if self.pending == value.routing.gemini_percent {
                        self.pending = nil
                        self.preference = Double(value.routing.gemini_percent)
                    }
                    self.message = value.warnings.first
                }
            } else if let value = try? JSONSerialization.jsonObject(with: line) as? [String: String], let error = value["error"] {
                DispatchQueue.main.async { [weak self] in
                    self?.message = error
                    self?.pending = nil
                    if let percent = self?.snapshot?.routing.gemini_percent { self?.preference = Double(percent) }
                }
            } else {
                DispatchQueue.main.async { [weak self] in
                    self?.message = "Collector data could not be read · refresh to reconnect"
                }
            }
        }
        if buffer.count > 2_000_000 { buffer.removeAll() }
    }

    func send(_ value: [String: Any]) {
        do {
            guard let input else { throw NSError(domain: "RoutingFloat", code: 1, userInfo: [NSLocalizedDescriptionKey: "Collector unavailable"]) }
            var data = try JSONSerialization.data(withJSONObject: value)
            data.append(10)
            try input.write(contentsOf: data)
        } catch { message = error.localizedDescription; pending = nil }
    }
    func save(_ value: Double) {
        preference = value.rounded()
        pending = Int(preference)
        send(["command": "set_routing", "gemini_percent": Int(preference)])
    }
    func refresh() {
        if process == nil { launch() }
        else { send(["command": "refresh"]) }
    }
    func toggleCollapsed() {
        collapsed.toggle()
        UserDefaults.standard.set(collapsed, forKey: "TokenPilotCollapsed")
    }
    func stop() {
        try? input?.close()
        process?.terminate()
    }
}

struct RoutingView: View {
    @ObservedObject var model: FloatModel
    @ObservedObject var appearance: AppearanceStore
    var hide: () -> Void = {}
    var resize: () -> Void = {}
    var customize: () -> Void = {}

    init(
        model: FloatModel,
        appearance: AppearanceStore = AppearanceStore(userDefaults: nil, persistsChanges: false),
        hide: @escaping () -> Void = {},
        resize: @escaping () -> Void = {},
        customize: @escaping () -> Void = {}
    ) {
        self.model = model
        self.appearance = appearance
        self.hide = hide
        self.resize = resize
        self.customize = customize
    }

    private var palette: ResolvedThemePalette { appearance.palette }
    private var loc: TokenPilotLocalizer { TokenPilotLocalizer(language: appearance.language) }
    private var resetComesFirst: Bool { model.snapshot?.forecast?.status == "reset_before_projection" }

    private static let isoFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let isoPlain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    var body: some View {
        let corner: CGFloat = palette.materialKind == .liquidGlassEffect
            ? (model.collapsed ? 24 : 26)
            : appearance.theme.cornerRadius

        VStack(alignment: .leading, spacing: model.collapsed ? 1 : 12) {
            if !model.collapsed {
                headerRow
            }
            if model.collapsed {
                collapsedBody
            } else {
                expandedBody
            }
        }
        .padding(.horizontal, model.collapsed ? 12 : 20)
        .padding(.top, model.collapsed ? 10 : 12)
        .padding(.bottom, model.collapsed ? 10 : 12)
        .frame(width: model.collapsed ? 300 : 360)
        .environment(\.colorScheme, palette.isDark ? .dark : .light)
        .gaussianFrostField(enabled: palette.materialKind == .liquidGlassEffect)
        .modifier(
            LiquidGlassPanelSurfaceModifier(palette: palette, cornerRadius: corner)
        )
        .onChange(of: model.collapsed) { _, _ in
            resize()
        }
        .onChange(of: model.snapshot?.timestamp) { _, _ in
            resize()
        }
        .onChange(of: appearance.theme) { _, _ in
            resize()
        }
        .onChange(of: appearance.language) { _, _ in
            resize()
        }
    }

    // MARK: - Header (32pt minimal toolbar with >=32x32 hit targets)

    private var headerRow: some View {
        HStack(spacing: 8) {
            HStack(spacing: 8) {
            if let url = Bundle.main.resourceURL?.appendingPathComponent("TokenPilot.icns"), let icon = NSImage(contentsOf: url) {
                Image(nsImage: icon).resizable().frame(width: 15, height: 15)
            } else {
                Image(systemName: "location.north.circle")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(palette.readablePrimaryTextColor)
            }

            FrostText("TokenPilot")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(palette.primaryTextColor)
                .lineLimit(1)

            Circle()
                .fill(model.connected ? palette.statusGreenRGB.color : palette.dangerRedRGB.color)
                .frame(width: 6, height: 6)
                .help(model.connected ? loc.L("Connected", "已连接") : loc.L("Disconnected", "未连接"))
                .accessibilityLabel(model.connected ? loc.L("Connected", "已连接") : loc.L("Disconnected", "未连接"))

            if let statusNote = headerStatusNote {
                FrostText(statusNote)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(palette.secondaryTextColor)
                    .lineLimit(1)
            }

            Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, minHeight: 32, alignment: .leading)
            .overlay(WindowDragArea(label: loc.L("Drag to move TokenPilot", "拖动以移动 TokenPilot")))

            HStack(spacing: 2) {
                Button(action: customize) {
                    Image(systemName: "paintpalette")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(palette.secondaryTextColor)
                        .frame(width: 32, height: 32)
                        .gaussianFrostAnchor(weight: 0.45)
                        .background(Color.white.opacity(0.001))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help(loc.L("Customize appearance and language", "自定义外观与语言"))
                .accessibilityLabel(loc.L("Customize TokenPilot", "自定义 TokenPilot"))

                Button(action: toggleCollapse) {
                    Image(systemName: "line.3.horizontal")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(palette.secondaryTextColor)
                        .frame(width: 32, height: 32)
                        .gaussianFrostAnchor(weight: 0.45)
                        .background(Color.white.opacity(0.001))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help(model.collapsed ? loc.L("Expand TokenPilot", "展开 TokenPilot") : loc.L("Collapse TokenPilot", "收起 TokenPilot"))
                .accessibilityLabel(model.collapsed ? loc.L("Expand TokenPilot", "展开 TokenPilot") : loc.L("Collapse TokenPilot", "收起 TokenPilot"))

                Button(action: hide) {
                    Image(systemName: "minus")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(palette.secondaryTextColor)
                        .frame(width: 32, height: 32)
                        .gaussianFrostAnchor(weight: 0.45)
                        .background(Color.white.opacity(0.001))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help(loc.L("Hide TokenPilot", "隐藏 TokenPilot"))
                .accessibilityLabel(loc.L("Hide TokenPilot", "隐藏 TokenPilot"))
            }
            .padding(.trailing, -4)
        }
        .frame(minHeight: 32)
    }

    // MARK: - Collapsed Body (3 flat rows: tokens, quota, routing; 300x96 compact layout)

    private var collapsedBody: some View {
        VStack(alignment: .leading, spacing: 1) {
            VStack(alignment: .leading, spacing: 1) {
                collapsedTokensRow
                collapsedQuotaRow
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .contextMenu {
                collapsedContextMenuItems
            }
            .overlay(
                WindowDragArea(label: loc.L("Drag to move TokenPilot", "拖动以移动 TokenPilot"))
            )

            collapsedRoutingRow
        }
    }

    @ViewBuilder
    private var collapsedContextMenuItems: some View {
        Button(loc.L("Expand TokenPilot", "展开 TokenPilot"), action: toggleCollapse)
        Button(loc.L("Customize TokenPilot", "自定义 TokenPilot"), action: customize)
        Button(loc.L("Refresh", "刷新")) {
            model.refresh()
        }
        Divider()
        Button(loc.L("Hide TokenPilot", "隐藏 TokenPilot"), action: hide)
    }

    private var collapsedTokensRow: some View {
        let codex = model.snapshot?.codex
        let gemini = model.snapshot?.gemini
        let codexStr = (codex?.available == true) ? compact(codex?.total_tokens ?? 0) : "—"
        let geminiStr = (gemini?.available == true) ? compact(gemini?.total_tokens ?? 0) : "—"
        let summary = loc.L(
            "Observed tokens: Codex \(codexStr), Gemini \(geminiStr)",
            "已记录 Token：Codex \(codexStr)，Gemini \(geminiStr)"
        )

        return HStack(spacing: 6) {
            FrostText("Codex \(codexStr)")
                .font(.system(size: 11, weight: .medium))
                .monospacedDigit()
                .foregroundStyle(codex?.available == true ? palette.primaryTextColor : palette.secondaryTextColor)
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)

            tokenSplitBar(height: 4)
                .gaussianFrostAnchor(weight: 0.08)
                .frame(minWidth: 24, maxWidth: .infinity)

            FrostText("\(geminiStr) Gemini")
                .font(.system(size: 11, weight: .medium))
                .monospacedDigit()
                .foregroundStyle(gemini?.available == true ? palette.primaryTextColor : palette.secondaryTextColor)
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)
        }
        .frame(minHeight: 20, maxHeight: 20)
        .help(summary)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(summary)
    }

    private var collapsedQuotaRow: some View {
        let selection = selectedCollapsedQuota

        return HStack(spacing: 8) {
            HStack(spacing: 5) {
                if resetComesFirst {
                    Circle()
                        .fill(palette.statusGreenRGB.color)
                        .frame(width: 6, height: 6)
                }
                FrostText(selection.label)
                    .font(.system(size: 11, weight: .medium))
                    .monospacedDigit()
                    .foregroundStyle(resetComesFirst ? palette.statusGreenRGB.color : palette.primaryTextColor)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
            }

            quotaProgressBar(remainingFraction: selection.remainingFraction, available: selection.available)
                .gaussianFrostAnchor(weight: 0.08)
                .frame(minWidth: 48, maxWidth: .infinity)

            FrostText(selection.percentText)
                .font(.system(size: 11, weight: .semibold))
                .monospacedDigit()
                .foregroundStyle(selection.available ? palette.primaryTextColor : palette.secondaryTextColor)
                .lineLimit(1)
                .frame(minWidth: 36, alignment: .trailing)
        }
        .frame(minHeight: 20, maxHeight: 20)
        .help(selection.tooltip)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(loc.L("\(selection.label) remaining quota \(selection.percentText)", "\(selection.label) 剩余配额 \(selection.percentText)"))
    }

    private var collapsedRoutingRow: some View {
        HStack(spacing: 6) {
            routingPresetMenu(prefix: loc.L("Routing: ", "路由："))
            routingSlider
            collapsedExpandButton
        }
        .frame(minHeight: 32, maxHeight: 32)
    }

    private var collapsedExpandButton: some View {
        Button(action: toggleCollapse) {
            Image(systemName: "line.3.horizontal")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(palette.secondaryTextColor)
                .frame(width: 32, height: 32)
                        .gaussianFrostAnchor(weight: 0.45)
                .background(Color.white.opacity(0.001))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .contextMenu {
            collapsedContextMenuItems
        }
        .help(loc.L("Expand TokenPilot", "展开 TokenPilot"))
        .accessibilityLabel(loc.L("Expand TokenPilot", "展开 TokenPilot"))
    }


    // MARK: - Expanded Body (calm hierarchy + single NextReset card before Routing)

    private var expandedBody: some View {
        VStack(alignment: .leading, spacing: 16) {
            expandedTokenSection
            VStack(alignment: .leading, spacing: 12) {
                expandedQuotasSection
                expandedForecastRow
            }
            nextResetCard
            VStack(alignment: .leading, spacing: 8) {
                expandedRoutingSection
                expandedFooterRow
            }
        }
    }

    private var expandedTokenSection: some View {
        let codex = model.snapshot?.codex
        let gemini = model.snapshot?.gemini
        let codexVal = (codex?.available == true) ? compact(codex?.total_tokens ?? 0) : "—"
        let geminiVal = (gemini?.available == true) ? compact(gemini?.total_tokens ?? 0) : "—"
        let codexCache: String = {
            if codex?.available == true {
                return loc.L("cached \(compact(codex?.cached_tokens ?? 0))", "缓存 \(compact(codex?.cached_tokens ?? 0))")
            }
            return loc.localizeCollectorMessage(codex?.status_message) ?? loc.L("Unavailable", "不可用")
        }()
        let geminiCache: String = {
            if gemini?.available == true {
                return loc.L("cached \(compact(gemini?.cached_tokens ?? 0))", "缓存 \(compact(gemini?.cached_tokens ?? 0))")
            }
            return loc.L("cached —", "缓存 —")
        }()

        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                FrostText(loc.L("Today", "今日用量"))
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(palette.secondaryTextColor)

                Spacer(minLength: 8)

                if let geminiStatus = geminiStatusLine {
                    HStack(spacing: 5) {
                        Circle()
                            .fill(geminiStatus.isAlert ? palette.warningOrangeRGB.color : palette.statusGreenRGB.color)
                            .frame(width: 6, height: 6)
                        FrostText(geminiStatus.text)
                            .font(.system(size: 11, weight: geminiStatus.isAlert ? .medium : .regular))
                            .foregroundStyle(geminiStatus.isAlert ? palette.warningOrangeRGB.color : palette.secondaryTextColor)
                            .lineLimit(2)
                            .multilineTextAlignment(.trailing)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .help(geminiStatus.tooltip)
                }
            }

            switch appearance.theme.tokenLayout {
            case .twoColumns:
                HStack(alignment: .top, spacing: 24) {
                    VStack(alignment: .leading, spacing: 4) {
                        FrostText("Codex")
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(palette.secondaryTextColor)
                        FrostText(codexVal)
                            .font(.system(size: 26, weight: .semibold))
                            .monospacedDigit()
                            .foregroundStyle(codex?.available == true ? palette.primaryTextColor : palette.secondaryTextColor.opacity(0.6))
                            .lineLimit(1)
                        FrostText(codexCache)
                            .font(.system(size: 11, weight: .regular))
                            .foregroundStyle(palette.secondaryTextColor)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)

                    VStack(alignment: .leading, spacing: 4) {
                        FrostText("Gemini")
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(palette.secondaryTextColor)
                        FrostText(geminiVal)
                            .font(.system(size: 26, weight: .semibold))
                            .monospacedDigit()
                            .foregroundStyle(gemini?.available == true ? palette.primaryTextColor : palette.secondaryTextColor.opacity(0.6))
                            .lineLimit(1)
                        FrostText(geminiCache)
                            .font(.system(size: 11, weight: .regular))
                            .foregroundStyle(palette.secondaryTextColor)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

            case .ledgerRows:
                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .center, spacing: 16) {
                        VStack(alignment: .leading, spacing: 2) {
                            FrostText("Codex")
                                .font(.system(size: 13, weight: .medium))
                                .foregroundStyle(palette.primaryTextColor)
                            FrostText(codexCache)
                                .font(.system(size: 11, weight: .regular))
                                .foregroundStyle(palette.secondaryTextColor)
                                .lineLimit(2)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 8)
                        FrostText(codexVal)
                            .font(.system(size: 24, weight: .medium))
                            .monospacedDigit()
                            .foregroundStyle(codex?.available == true ? palette.primaryTextColor : palette.secondaryTextColor.opacity(0.6))
                            .lineLimit(1)
                    }

                    HStack(alignment: .center, spacing: 16) {
                        VStack(alignment: .leading, spacing: 2) {
                            FrostText("Gemini")
                                .font(.system(size: 13, weight: .medium))
                                .foregroundStyle(palette.primaryTextColor)
                            FrostText(geminiCache)
                                .font(.system(size: 11, weight: .regular))
                                .foregroundStyle(palette.secondaryTextColor)
                                .lineLimit(2)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 8)
                        FrostText(geminiVal)
                            .font(.system(size: 24, weight: .medium))
                            .monospacedDigit()
                            .foregroundStyle(gemini?.available == true ? palette.primaryTextColor : palette.secondaryTextColor.opacity(0.6))
                            .lineLimit(1)
                    }
                }

            case .openGridMono:
                HStack(alignment: .top, spacing: 16) {
                    VStack(alignment: .leading, spacing: 4) {
                        FrostText("Codex")
                            .font(.system(size: 11, weight: .regular))
                            .foregroundStyle(palette.secondaryTextColor)
                        FrostText(codexVal)
                            .font(.system(size: 24, weight: .medium, design: .monospaced))
                            .foregroundStyle(codex?.available == true ? palette.primaryTextColor : palette.secondaryTextColor.opacity(0.6))
                            .lineLimit(1)
                        FrostText(codexCache)
                            .font(.system(size: 11, weight: .regular))
                            .foregroundStyle(palette.secondaryTextColor)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)

                    Rectangle()
                        .fill(palette.borderColor)
                        .frame(width: 1)

                    VStack(alignment: .leading, spacing: 4) {
                        FrostText("Gemini")
                            .font(.system(size: 11, weight: .regular))
                            .foregroundStyle(palette.secondaryTextColor)
                        FrostText(geminiVal)
                            .font(.system(size: 24, weight: .medium, design: .monospaced))
                            .foregroundStyle(gemini?.available == true ? palette.primaryTextColor : palette.secondaryTextColor.opacity(0.6))
                            .lineLimit(1)
                        FrostText(geminiCache)
                            .font(.system(size: 11, weight: .regular))
                            .foregroundStyle(palette.secondaryTextColor)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }

            tokenSplitBar(height: 4)
                .gaussianFrostAnchor(weight: 0.08)
                .padding(.top, 4)
        }
    }

    // MARK: - Expanded Quotas Section

    @ViewBuilder
    private var expandedQuotasSection: some View {
        switch appearance.theme.quotaLayout {
        case .stackedHeaderBar:
            VStack(alignment: .leading, spacing: 12) {
                stackedQuotaRow(
                    limit: model.snapshot?.limits.five_hour,
                    forecastWindow: model.snapshot?.forecast?.five_hour,
                    isWeekly: false
                )
                stackedQuotaRow(
                    limit: model.snapshot?.limits.weekly,
                    forecastWindow: model.snapshot?.forecast?.weekly,
                    isWeekly: true
                )
            }

        case .ledgerBar:
            VStack(alignment: .leading, spacing: 12) {
                ledgerQuotaRow(
                    limit: model.snapshot?.limits.five_hour,
                    forecastWindow: model.snapshot?.forecast?.five_hour,
                    isWeekly: false
                )
                ledgerQuotaRow(
                    limit: model.snapshot?.limits.weekly,
                    forecastWindow: model.snapshot?.forecast?.weekly,
                    isWeekly: true
                )
            }

        case .openGridQuotas:
            HStack(alignment: .top, spacing: 16) {
                openGridQuotaCell(
                    limit: model.snapshot?.limits.five_hour,
                    forecastWindow: model.snapshot?.forecast?.five_hour,
                    isWeekly: false
                )
                .frame(maxWidth: .infinity, alignment: .leading)

                Rectangle()
                    .fill(palette.borderColor)
                    .frame(width: 1)

                openGridQuotaCell(
                    limit: model.snapshot?.limits.weekly,
                    forecastWindow: model.snapshot?.forecast?.weekly,
                    isWeekly: true
                )
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func stackedQuotaRow(
        limit: WindowLimit?,
        forecastWindow: ForecastWindow?,
        isWeekly: Bool
    ) -> some View {
        let title = loc.windowTitle(isWeekly: isWeekly)
        let available = limit != nil
        let remaining = clampPercent(100.0 - (limit?.used_percent ?? 0))
        let fraction = available ? clamp01(remaining / 100.0) : 0.0
        let leftStr = available
            ? loc.L("\(Int(remaining.rounded()))% left", "剩余 \(Int(remaining.rounded()))%")
            : loc.L("Unavailable", "不可用")
        let resetText = formatResetLabel(resetsAt: limit?.resets_at, isWeekly: isWeekly, includeVerb: true)
        let tip = quotaTooltip(title: title, limit: limit, forecastWindow: forecastWindow, isWeekly: isWeekly)

        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                FrostText("\(title) · \(leftStr)")
                    .font(.system(size: 12, weight: .medium))
                    .monospacedDigit()
                    .foregroundStyle(palette.primaryTextColor)
                Spacer(minLength: 4)
                if !resetText.isEmpty {
                    FrostText(resetText)
                        .font(.system(size: 11, weight: .regular))
                        .monospacedDigit()
                        .foregroundStyle(palette.secondaryTextColor)
                }
            }
            quotaProgressBar(remainingFraction: fraction, available: available)
        }
        .help(tip)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(title) \(leftStr) \(resetText)")
    }

    private func ledgerQuotaRow(
        limit: WindowLimit?,
        forecastWindow: ForecastWindow?,
        isWeekly: Bool
    ) -> some View {
        let title = loc.windowTitle(isWeekly: isWeekly)
        let available = limit != nil
        let remaining = clampPercent(100.0 - (limit?.used_percent ?? 0))
        let fraction = available ? clamp01(remaining / 100.0) : 0.0
        let leftStr = available
            ? loc.L("\(Int(remaining.rounded()))% left", "剩余 \(Int(remaining.rounded()))%")
            : loc.L("Unavailable", "不可用")
        let resetRaw = formatResetLabel(resetsAt: limit?.resets_at, isWeekly: isWeekly, includeVerb: false)
        let metaText = resetRaw.isEmpty ? leftStr : "\(leftStr) · \(resetRaw)"
        let tip = quotaTooltip(title: title, limit: limit, forecastWindow: forecastWindow, isWeekly: isWeekly)

        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                FrostText(title)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(palette.primaryTextColor)
                Spacer(minLength: 4)
                FrostText(metaText)
                    .font(.system(size: 11, weight: .regular))
                    .monospacedDigit()
                    .foregroundStyle(palette.secondaryTextColor)
            }
            quotaProgressBar(remainingFraction: fraction, available: available)
        }
        .help(tip)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(title) \(metaText)")
    }

    private func openGridQuotaCell(
        limit: WindowLimit?,
        forecastWindow: ForecastWindow?,
        isWeekly: Bool
    ) -> some View {
        let title = loc.windowTitle(isWeekly: isWeekly)
        let available = limit != nil
        let remaining = clampPercent(100.0 - (limit?.used_percent ?? 0))
        let fraction = available ? clamp01(remaining / 100.0) : 0.0
        let leftStr = available
            ? loc.L("\(Int(remaining.rounded()))% left", "剩余 \(Int(remaining.rounded()))%")
            : loc.L("Unavailable", "不可用")
        let resetText = formatResetLabel(resetsAt: limit?.resets_at, isWeekly: isWeekly, includeVerb: true)
        let tip = quotaTooltip(title: title, limit: limit, forecastWindow: forecastWindow, isWeekly: isWeekly)

        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                FrostText(title)
                    .font(.system(size: 12, weight: .regular))
                    .foregroundStyle(palette.primaryTextColor)
                Spacer(minLength: 4)
                FrostText(leftStr)
                    .font(.system(size: 11, weight: .medium))
                    .monospacedDigit()
                    .foregroundStyle(palette.primaryTextColor)
            }
            quotaProgressBar(remainingFraction: fraction, available: available)
            FrostText(resetText.isEmpty ? "—" : resetText)
                .font(.system(size: 11, weight: .regular))
                .monospacedDigit()
                .foregroundStyle(palette.secondaryTextColor)
        }
        .help(tip)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(title) \(leftStr) \(resetText)")
    }

    // MARK: - Forecast Row (Expanded, natural line wrapping for localized strings)

    private var expandedForecastRow: some View {
        let info = forecastDisplay
        return VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                if resetComesFirst {
                    Circle()
                        .fill(palette.statusGreenRGB.color)
                        .frame(width: 6, height: 6)
                } else {
                    Image(systemName: "chart.line.uptrend.xyaxis")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(info.isProjected ? palette.readablePrimaryTextColor : palette.secondaryTextColor)
                }

                FrostText(info.primary)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(resetComesFirst ? palette.statusGreenRGB.color : palette.primaryTextColor)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)

                Spacer(minLength: 6)

                if let burn = info.burnBadge {
                    FrostText(burn)
                        .font(.system(size: 11, weight: .regular))
                        .monospacedDigit()
                        .foregroundStyle(palette.secondaryTextColor)
                        .lineLimit(1)
                        .fixedSize(horizontal: true, vertical: false)
                }
            }

            if let secondary = info.secondary, !secondary.isEmpty {
                FrostText(secondary)
                    .font(.system(size: 11, weight: .regular))
                    .foregroundStyle(palette.secondaryTextColor)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .help(info.tooltip)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(loc.L("Forecast: \(info.primary). \(info.secondary ?? "")", "用量预测：\(info.primary)。\(info.secondary ?? "")"))
    }

    // MARK: - Single NextReset Card (placed after Forecast and before Routing)

    private var nextResetCard: some View {
        NextResetCardView(info: model.snapshot?.nextreset, language: appearance.language,
                          palette: palette, openHome: openNextResetURL)
    }

    private func openNextResetURL() {
        let raw = model.snapshot?.nextreset?.url ?? "https://nextreset.ai"
        guard
            let url = URL(string: raw),
            url.scheme?.lowercased() == "https",
            let host = url.host?.lowercased(),
            host == "nextreset.ai" || host.hasSuffix(".nextreset.ai")
        else {
            return
        }
        NSWorkspace.shared.open(url)
    }

    // MARK: - Expanded Routing Section (placed after NextReset and before Footer)

    private var expandedRoutingSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                FrostText(loc.L("Routing", "任务路由"))
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(palette.primaryTextColor)

                if let pending = model.pending {
                    FrostText(loc.L("Saving \(pending)%…", "正在保存 \(pending)%…"))
                        .font(.system(size: 11, weight: .regular))
                        .foregroundStyle(palette.secondaryTextColor)
                        .lineLimit(1)
                } else if let rawMsg = model.message, !rawMsg.isEmpty {
                    let displayMsg = loc.localizeCollectorMessage(rawMsg) ?? rawMsg
                    FrostText(displayMsg)
                        .font(.system(size: 11, weight: .regular))
                        .foregroundStyle(palette.secondaryTextColor)
                        .lineLimit(1)
                        .help(rawMsg)
                }

                Spacer(minLength: 4)

                routingPresetMenu(prefix: "")
            }
            .frame(minHeight: 28)

            routingSlider
                .frame(maxWidth: .infinity, minHeight: 28)
        }
    }

    private func routingPresetMenu(prefix: String) -> some View {
        let pct = Int(model.preference.rounded())
        let title = "\(prefix)\(pct)% Gemini"

        return Menu {
            Button(loc.L("GPT (0% Gemini)", "仅 GPT (0% Gemini)")) {
                applyPreset(0)
            }
            Button(loc.L("Balanced (50% Gemini)", "均衡 (50% Gemini)")) {
                applyPreset(50)
            }
            Button(loc.L("Gemini (100% Gemini)", "优先 Gemini (100% Gemini)")) {
                applyPreset(100)
            }
        } label: {
            HStack(spacing: 4) {
                FrostText(title)
                    .font(.system(size: model.collapsed ? 11 : 12, weight: .medium))
                    .monospacedDigit()
                    .foregroundStyle(model.connected ? palette.readablePrimaryTextColor : palette.secondaryTextColor.opacity(0.6))
                    .lineLimit(1)
                    .frame(width: model.collapsed ? 112 : nil, alignment: .leading)

                Image(systemName: "chevron.down")
                    .font(.system(size: model.collapsed ? 8 : 9, weight: .bold))
                    .foregroundStyle(model.connected ? palette.readablePrimaryTextColor.opacity(0.85) : palette.secondaryTextColor.opacity(0.5))
            }
            .frame(width: model.collapsed ? 126 : nil, alignment: .leading)
            .frame(minHeight: 32)
            .contentShape(Rectangle())
        }
        .menuStyle(.borderlessButton)
        .font(.system(size: model.collapsed ? 11 : 12, weight: .medium))
        .controlSize(model.collapsed ? .small : .regular)
        .menuIndicator(.hidden)
        .buttonStyle(.plain)
        .fixedSize(horizontal: true, vertical: false)
        .frame(width: model.collapsed ? 140 : nil, alignment: .leading)
        .disabled(!model.connected)
        .help(routingTooltip)
        .accessibilityLabel(loc.L("Routing preset menu, current value \(pct) percent Gemini", "路由预设菜单，当前为 \(pct)% Gemini"))
    }

    private var routingSlider: some View {
        let pct = Int(model.preference.rounded())
        return Slider(
            value: $model.preference,
            in: 0...100,
            onEditingChanged: { isEditing in
                model.editing = isEditing
                if !isEditing {
                    model.save(model.preference)
                }
            }
        )
        .frame(minWidth: 72, maxWidth: .infinity)
        .tint(palette.rawPrimaryColor)
        .disabled(!model.connected)
        .help(routingTooltip)
        .accessibilityLabel(loc.L("Gemini routing split slider", "Gemini 路由比例滑块"))
        .accessibilityValue("\(pct)% Gemini")
    }


    private func applyPreset(_ value: Double) {
        model.editing = false
        model.preference = value
        model.save(value)
    }

    // MARK: - Expanded Footer Row

    private var expandedFooterRow: some View {
        HStack(spacing: 6) {
            FrostText(formattedSnapshotTimestamp)
                .font(.system(size: 11, weight: .regular))
                .monospacedDigit()
                .foregroundStyle(palette.secondaryTextColor)
                .lineLimit(1)

            if model.snapshot?.limits.stale == true {
                FrostText(loc.L("· Stale", "· 缓存过期"))
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(palette.warningOrangeRGB.color)
                    .lineLimit(1)
            } else if let limitErr = model.snapshot?.limits.error, !limitErr.isEmpty {
                FrostText(loc.L("· Quota error", "· 配额异常"))
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(palette.warningOrangeRGB.color)
                    .lineLimit(1)
                    .help(limitErr)
            }

            Spacer(minLength: 4)

            HStack(spacing: 2) {
                Button(action: { model.refresh() }) {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(palette.secondaryTextColor)
                        .frame(width: 32, height: 32)
                        .gaussianFrostAnchor(weight: 0.45)
                        .background(Color.white.opacity(0.001))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help(loc.L("Refresh usage and quota", "刷新用量与配额"))
                .accessibilityLabel(loc.L("Refresh", "刷新"))

                Menu {
                    Button(loc.L("Customize…", "自定义外观…")) {
                        customize()
                    }
                    Divider()
                    Button(loc.L("Refresh", "刷新")) {
                        model.refresh()
                    }
                    Button(model.collapsed ? loc.L("Expand", "展开") : loc.L("Collapse", "收起")) {
                        toggleCollapse()
                    }
                    Button(loc.L("Hide", "隐藏")) {
                        hide()
                    }
                    Divider()
                    Button(loc.L("Quit", "退出")) {
                        NSApplication.shared.terminate(nil)
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(palette.secondaryTextColor)
                        .frame(width: 32, height: 32)
                        .gaussianFrostAnchor(weight: 0.45)
                        .background(Color.white.opacity(0.001))
                        .contentShape(Rectangle())
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .buttonStyle(.plain)
                .fixedSize()
                .help(loc.L("TokenPilot actions", "TokenPilot 操作菜单"))
                .accessibilityLabel(loc.L("Actions menu", "操作菜单"))
            }
            .padding(.trailing, -4)
        }
        .frame(minHeight: 32)
    }

    // MARK: - Shared Progress & Split Bars

    private func tokenSplitBar(height: CGFloat) -> some View {
        let ratio = observedCodexRatio
        return GeometryReader { geo in
            let w = max(geo.size.width, 0)
            ZStack(alignment: .leading) {
                Capsule(style: .continuous)
                    .fill(ratio == nil ? palette.trackColor : palette.rawSecondaryColor)
                if let r = ratio {
                    Capsule(style: .continuous)
                        .fill(palette.rawPrimaryColor)
                        .frame(width: w * clamp01(r))
                }
            }
        }
        .frame(height: height)
    }

    private func quotaProgressBar(remainingFraction: Double, available: Bool) -> some View {
        let clamped = clamp01(remainingFraction)
        let fillColor: Color = !available
            ? palette.trackColor
            : (clamped <= 0.10
                ? palette.dangerRedRGB.color
                : (clamped <= 0.25 ? palette.warningOrangeRGB.color : palette.rawPrimaryColor))

        return GeometryReader { geo in
            let w = max(geo.size.width, 0)
            ZStack(alignment: .leading) {
                Capsule(style: .continuous)
                    .fill(palette.trackColor)
                if available {
                    Capsule(style: .continuous)
                        .fill(fillColor)
                        .frame(width: w * clamped)
                }
            }
        }
        .frame(height: 4)
    }

    // MARK: - Helpers & Computed Display Data

    private func toggleCollapse() {
        model.toggleCollapsed()
        resize()
    }

    private func clamp01(_ value: Double) -> Double {
        min(max(value, 0.0), 1.0)
    }

    private func clampPercent(_ value: Double) -> Double {
        min(max(value, 0.0), 100.0)
    }

    private var headerStatusNote: String? {
        if !model.connected {
            return loc.L("Offline", "离线")
        }
        if model.collapsed, let pending = model.pending {
            return loc.L("Saving \(pending)%…", "正在保存 \(pending)%…")
        }
        return nil
    }

    private var routingTooltip: String {
        let pct = Int(model.preference.rounded())
        if let pending = model.pending {
            return loc.L(
                "Routing \(pct)% Gemini (saving \(pending)%…)",
                "路由 \(pct)% Gemini（正在保存 \(pending)%…）"
            )
        }
        if let message = model.message, !message.isEmpty {
            let localizedMsg = loc.localizeCollectorMessage(message) ?? message
            let suffix = localizedMsg == message ? message : "\(localizedMsg) (\(message))"
            return loc.L(
                "Routing \(pct)% Gemini · \(suffix)",
                "路由 \(pct)% Gemini · \(suffix)"
            )
        }
        return model.connected
            ? loc.L(
                "Gemini delegation preference: \(pct)% for new coding tasks; not a fixed token or request split",
                "新编码任务的 Gemini 委派偏好：\(pct)%（非固定 Token 或请求比例）"
            )
            : loc.L("Routing disabled while disconnected", "离线状态下无法修改路由偏好")
    }

    private var observedCodexRatio: Double? {
        let codexTokens = (model.snapshot?.codex.available == true) ? max(model.snapshot?.codex.total_tokens ?? 0, 0) : 0
        let geminiTokens = (model.snapshot?.gemini.available == true) ? max(model.snapshot?.gemini.total_tokens ?? 0, 0) : 0
        let total = codexTokens + geminiTokens
        guard total > 0 else { return nil }
        return clamp01(codexTokens / total)
    }

    private var geminiStatusLine: (text: String, isAlert: Bool, tooltip: String)? {
        guard let gemini = model.snapshot?.gemini else { return nil }
        return loc.localizeGeminiStatus(
            status: gemini.status,
            message: gemini.status_message,
            available: gemini.available
        )
    }

    struct CollapsedQuotaSelection {
        let label: String
        let hasCompactETA: Bool
        let remainingFraction: Double
        let percentText: String
        let available: Bool
        let tooltip: String
    }

    var selectedCollapsedQuota: CollapsedQuotaSelection {
        let five = model.snapshot?.limits.five_hour
        let weekly = model.snapshot?.limits.weekly

        let chosenIsFive: Bool? = {
            switch (five, weekly) {
            case let (f?, w?):
                if 100 - f.used_percent > 95 { return false }
                if let selected = model.snapshot?.forecast?.display_window {
                    return selected == "five_hour"
                }
                let a = model.snapshot?.forecast?.five_hour?.exhaustion_at
                let b = model.snapshot?.forecast?.weekly?.exhaustion_at
                if let a, let b { return a <= b }
                if a != nil { return true }
                if b != nil { return false }
                return f.used_percent >= w.used_percent
            case (_?, nil):
                return true
            case (nil, _?):
                return false
            case (nil, nil):
                return nil
            }
        }()

        guard let isFive = chosenIsFive, let limit = isFive ? five : weekly else {
            let errNote = model.snapshot?.limits.error ?? loc.L("Quota data unavailable", "配额数据不可用")
            return CollapsedQuotaSelection(
                label: loc.L("Quota", "配额"),
                hasCompactETA: false,
                remainingFraction: 0.0,
                percentText: "—",
                available: false,
                tooltip: errNote
            )
        }

        let remaining = clampPercent(100.0 - limit.used_percent)
        let fraction = clamp01(remaining / 100.0)
        let percentText = "\(Int(remaining.rounded()))%"
        let matchingForecast = isFive ? model.snapshot?.forecast?.five_hour : model.snapshot?.forecast?.weekly
        let baseTitle = loc.windowTitle(isWeekly: !isFive)
        let shortPrefix = loc.windowShortPrefix(isWeekly: !isFive)

        var label = baseTitle
        var hasCompactETA = false
        if resetComesFirst {
            let nextIsWeekly = model.snapshot?.forecast?.next_reset_window == "weekly"
            let nextPrefix = loc.windowShortPrefix(isWeekly: nextIsWeekly)
            if appearance.language == .chinese {
                label = nextPrefix == shortPrefix ? "\(shortPrefix)先重置" : "\(shortPrefix) · \(nextPrefix)先重置"
            } else {
                label = nextPrefix == shortPrefix ? "\(shortPrefix) reset first" : "\(shortPrefix) · \(nextPrefix) reset first"
            }
        } else if matchingForecast?.status == "projected",
           let eta = matchingForecast?.exhaustion_at,
           let etaStr = formatEpochHM(eta) {
            label = "\(shortPrefix) ~\(etaStr)"
            hasCompactETA = true
        }

        let reason = loc.localizeSelectionReason(model.snapshot?.forecast?.selection_reason)
        let tip = reason + " · " + quotaTooltip(
            title: baseTitle,
            limit: limit,
            forecastWindow: matchingForecast,
            isWeekly: !isFive
        )

        return CollapsedQuotaSelection(
            label: label,
            hasCompactETA: hasCompactETA,
            remainingFraction: fraction,
            percentText: percentText,
            available: true,
            tooltip: tip
        )
    }

    private func quotaTooltip(
        title: String,
        limit: WindowLimit?,
        forecastWindow: ForecastWindow?,
        isWeekly: Bool
    ) -> String {
        guard let limit else {
            if let err = model.snapshot?.limits.error, !err.isEmpty {
                return loc.L("\(title) quota unavailable · \(err)", "\(title)配额不可用 · \(err)")
            }
            return loc.L("\(title) quota unavailable", "\(title)配额不可用")
        }
        let used = clampPercent(limit.used_percent)
        let remaining = clampPercent(100.0 - used)
        var parts: [String] = [
            loc.L(
                "\(title): \(Int(remaining.rounded()))% remaining (\(Int(used.rounded()))% used)",
                "\(title)：剩余 \(Int(remaining.rounded()))%（已用 \(Int(used.rounded()))%）"
            )
        ]
        if let resetsAt = limit.resets_at {
            let resetText = isWeekly ? formatEpochWeek(resetsAt) : (formatEpochHM(resetsAt) ?? "")
            if !resetText.isEmpty {
                parts.append(loc.L("Resets \(resetText)", "\(resetText) 重置"))
            }
        }
        if let msg = loc.localizeWindowMessage(forecastWindow?.message), !msg.isEmpty {
            parts.append(msg)
        }
        if model.snapshot?.limits.stale == true {
            parts.append(loc.L("Cached quota (stale)", "缓存配额（已过期）"))
        }
        if let err = model.snapshot?.limits.error, !err.isEmpty {
            parts.append(err)
        }
        return parts.joined(separator: " · ")
    }

    private var forecastDisplay: (primary: String, secondary: String?, burnBadge: String?, isProjected: Bool, tooltip: String) {
        guard let f = model.snapshot?.forecast else {
            return (
                primary: loc.L("Waiting for 15m usage trend…", "正在收集 15 分钟用量趋势…"),
                secondary: nil,
                burnBadge: nil,
                isProjected: false,
                tooltip: loc.L(
                    "Forecast unavailable until usage trend is collected.",
                    "需收集完整的 15 分钟趋势后才可生成配额预测。"
                )
            )
        }

        let isProjected = f.status == "projected"
        let etaHM = isProjected ? formatEpochHM(f.exhaustion_at) : nil
        let headline = loc.localizeForecastHeadline(forecast: f, formattedETA: etaHM)

        var burnBadge: String?
        if let tpm = f.tokens_per_minute, tpm > 0 {
            burnBadge = loc.L("\(compact(tpm)) tok/min", "\(compact(tpm)) tok/分钟")
        }

        let detail = loc.localizeForecastDetail(f.detail, nextResetWindow: f.next_reset_window)
        var tipParts: [String] = [
            headline,
            loc.L(
                "Quota trend determines depletion; token burn includes cached context and is not a fixed quota unit.",
                "耗尽预测基于配额消耗速率；Token 速率包含缓存上下文，非固定配额单位。"
            )
        ]
        if let tpm = f.tokens_per_minute, tpm > 0 {
            tipParts.append(loc.L("15m burn: \(compact(tpm)) tokens/min", "15 分钟速率：\(compact(tpm)) tokens/分钟"))
        }
        if !detail.isEmpty {
            tipParts.append(detail)
        }
        let rawHeadline = f.headline.trimmingCharacters(in: .whitespacesAndNewlines)
        if appearance.language == .chinese && !rawHeadline.isEmpty && rawHeadline != headline {
            tipParts.append(rawHeadline)
        }

        return (
            primary: headline,
            secondary: detail.isEmpty ? nil : detail,
            burnBadge: burnBadge,
            isProjected: isProjected,
            tooltip: tipParts.joined(separator: " · ")
        )
    }

    private var nextResetDisplay: (headline: String, reportFooter: String, statusBadge: String?, isWarning: Bool, tooltip: String) {
        guard let nr = model.snapshot?.nextreset else {
            return (
                headline: loc.L("Loading…", "加载中…"),
                reportFooter: loc.L("Report —", "暂无报告"),
                statusBadge: nil,
                isWarning: false,
                tooltip: loc.L(
                    "Community tracker (nextreset.ai), not personal quota. Loading latest report…",
                    "社区重置追踪（nextreset.ai），非个人账户配额。正在加载最新报告…"
                )
            )
        }

        let baseHeadline: String
        if nr.announcement_state == "announced" {
            baseHeadline = loc.L("Reset announced · awaiting rollout", "重置已宣布 · 等待生效")
        } else if nr.headline == "Community reset forecast" {
            baseHeadline = loc.L("Community reset forecast", "社区重置预测")
        } else {
            baseHeadline = loc.localizeNextResetHeadline(nr.headline, hasError: nr.error != nil)
        }
        let headline: String
        if let chance = nr.probability_24h, chance.isFinite, (0...1).contains(chance) {
            let percent = Int((chance * 100).rounded())
            headline = baseHeadline + loc.L(" · 24h \(percent)% est.", " · 24小时 \(percent)%（估计）")
        } else { headline = baseHeadline }
        let reportTime = formatISOReport(nr.latest_report_at)
        let reportFooter = reportTime.isEmpty
            ? loc.L("Report —", "暂无报告")
            : loc.L("Latest \(reportTime)", "最新 \(reportTime)")

        var badge: String?
        var isWarning = false
        if let err = nr.error, !err.isEmpty {
            badge = loc.L("Stale", "已过期")
            isWarning = true
        } else if nr.stale {
            badge = loc.L("Stale", "已过期")
            isWarning = true
        } else if nr.source_delayed {
            badge = loc.L("Source delayed", "数据源延迟")
            isWarning = true
        }

        var tipParts: [String] = [
            loc.L(
                "Community tracker (nextreset.ai), not personal quota.",
                "公共社区追踪（nextreset.ai），不代表您的个人账户重置。"
            ),
            headline
        ]
        if let asOf = nr.source_as_of {
            let updateTime = formatISOReport(asOf)
            if !updateTime.isEmpty { tipParts.append(loc.L("Feed updated: \(updateTime)", "数据更新：\(updateTime)")) }
        }
        if nr.probability_24h != nil {
            tipParts.append(loc.L("Experimental community estimate; not an official reset guarantee.", "社区实验性估计，并非官方重置保证。"))
        }
        if !reportTime.isEmpty {
            tipParts.append(loc.L("Latest community report: \(reportTime)", "最新社区报告时间：\(reportTime)"))
        }
        if nr.source_delayed {
            tipParts.append(loc.L("Source delayed", "数据源延迟"))
        }
        if nr.stale {
            tipParts.append(loc.L("Cached data is stale", "缓存数据已过期"))
        }
        if let err = nr.error, !err.isEmpty {
            tipParts.append(loc.L("Error: \(err)", "错误：\(err)"))
        }

        return (
            headline: headline,
            reportFooter: reportFooter,
            statusBadge: badge,
            isWarning: isWarning,
            tooltip: tipParts.joined(separator: " · ")
        )
    }

    private func decodeISO(_ raw: String?) -> Date? {
        guard let raw = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else {
            return nil
        }
        if let date = Self.isoFractional.date(from: raw) ?? Self.isoPlain.date(from: raw) {
            return date
        }
        if let seconds = Double(raw) {
            return Date(timeIntervalSince1970: seconds)
        }
        return nil
    }

    private func formatEpochHM(_ epoch: Double?) -> String? {
        guard let epoch, epoch > 0 else { return nil }
        let date = Date(timeIntervalSince1970: epoch)
        return Calendar.current.isDateInToday(date) ? loc.formatTimeHM(date) : loc.formatWeekReset(date)
    }

    private func formatEpochWeek(_ epoch: Double) -> String {
        guard epoch > 0 else { return "" }
        return loc.formatWeekReset(Date(timeIntervalSince1970: epoch))
    }

    private func formatResetLabel(resetsAt: Double?, isWeekly: Bool, includeVerb: Bool) -> String {
        guard let resetsAt, resetsAt > 0 else { return "" }
        let formatted = isWeekly ? formatEpochWeek(resetsAt) : (formatEpochHM(resetsAt) ?? "")
        guard !formatted.isEmpty else { return "" }
        if !includeVerb { return formatted }
        return loc.L("resets \(formatted)", "\(formatted) 重置")
    }

    private func formatISOReport(_ raw: String?) -> String {
        guard let date = decodeISO(raw) else { return "" }
        return loc.formatReportDate(date)
    }

    private var formattedSnapshotTimestamp: String {
        guard let raw = model.snapshot?.timestamp.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else {
            return loc.L("Updated —", "更新于 —")
        }
        if let date = decodeISO(raw) {
            return loc.L("Updated \(loc.formatTimeHMS(date))", "更新于 \(loc.formatTimeHMS(date))")
        }
        return loc.L("Updated \(raw)", "更新于 \(raw)")
    }
}

// Use AppKit's window drag tracking over the title area, independently of material hit testing.
final class WindowDragAreaView: NSView {
    override var mouseDownCanMoveWindow: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) {
        guard event.type == .leftMouseDown else { return }
        window?.performDrag(with: event)
    }
}

struct WindowDragArea: NSViewRepresentable {
    let label: String
    func makeNSView(context: Context) -> WindowDragAreaView {
        let view = WindowDragAreaView(frame: .zero)
        view.toolTip = label
        return view
    }
    func updateNSView(_ view: WindowDragAreaView, context: Context) { view.toolTip = label }
}

final class FloatingPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

final class CustomizationWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

final class DraggableHostingView<Content: View>: NSHostingView<Content> {
    override var isOpaque: Bool { false }
    override var mouseDownCanMoveWindow: Bool { true }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    let model = FloatModel()
    let appearance = AppearanceStore()
    var panel: FloatingPanel!
    var settingsWindow: CustomizationWindow?
    var status: NSStatusItem!
    private var cancellables = Set<AnyCancellable>()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.accessory)
        let arguments = CommandLine.arguments
        if arguments.contains("--ui-checks") {
            let ok = runSelfChecks()
            exit(ok ? 0 : 1)
        }
        if let i = arguments.firstIndex(of: "--render-preview"), arguments.count > i + 1 {
            renderPreview(to: arguments[i + 1])
            return
        }
        if arguments.contains("--collapsed") && !model.collapsed {
            model.toggleCollapsed()
        }

        let content = RoutingView(
            model: model,
            appearance: appearance,
            hide: { [weak self] in self?.hidePanel() },
            resize: { [weak self] in self?.resizePanel() },
            customize: { [weak self] in self?.showCustomization() }
        )
        let host = DraggableHostingView(rootView: content)
        host.wantsLayer = true
        host.layer?.backgroundColor = NSColor.clear.cgColor
        let size = host.fittingSize

        panel = FloatingPanel(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.level = .floating
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = appearance.resolvedMaterialKind != .liquidGlassEffect
        panel.isMovableByWindowBackground = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.contentView = host
        panel.setFrameAutosaveName("RoutingFloatPanel")
        if !panel.setFrameUsingName("RoutingFloatPanel"), let screen = NSScreen.main {
            let bounds = screen.visibleFrame
            panel.setFrameOrigin(NSPoint(x: bounds.maxX - size.width - 24, y: bounds.minY + 60))
        }
        panel.orderFrontRegardless()
        resizePanel()

        status = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        status.button?.image = NSImage(systemSymbolName: "location.north.circle", accessibilityDescription: "TokenPilot")
        status.button?.target = self
        status.button?.action = #selector(toggle)
        status.button?.sendAction(on: [.leftMouseUp, .rightMouseUp])

        appearance.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in
                guard let self else { return }
                self.settingsWindow?.title = self.appearance.L("Customize TokenPilot", "自定义 TokenPilot")
                self.resizePanel()
            }
            .store(in: &cancellables)

        model.launch()
    }

    @objc func toggle() {
        if NSApplication.shared.currentEvent?.type == .rightMouseUp {
            let menu = NSMenu()
            let show = NSMenuItem(
                title: appearance.L("Show TokenPilot", "显示 TokenPilot"),
                action: #selector(showPanel),
                keyEquivalent: ""
            )
            show.target = self
            menu.addItem(show)

            let customizeItem = NSMenuItem(
                title: appearance.L("Customize…", "自定义外观…"),
                action: #selector(showCustomization),
                keyEquivalent: ","
            )
            customizeItem.target = self
            menu.addItem(customizeItem)

            menu.addItem(.separator())

            let quit = NSMenuItem(
                title: appearance.L("Quit TokenPilot", "退出 TokenPilot"),
                action: #selector(quit),
                keyEquivalent: "q"
            )
            quit.target = self
            menu.addItem(quit)

            status.menu = menu
            status.button?.performClick(nil)
            status.menu = nil
        } else if panel.isVisible {
            hidePanel()
        } else {
            showPanel()
        }
    }

    @objc func showPanel() {
        panel.orderFrontRegardless()
        recordPanelState("show")
    }

    func hidePanel() {
        panel.orderOut(nil)
        recordPanelState("hide")
    }

    @objc func showCustomization() {
        if let existing = settingsWindow {
            existing.title = appearance.L("Customize TokenPilot", "自定义 TokenPilot")
            NSApplication.shared.activate(ignoringOtherApps: true)
            existing.makeKeyAndOrderFront(nil)
            recordPanelState("customize")
            return
        }

        let rootView = CustomizationView(appearance: appearance)
        let host = NSHostingView(rootView: rootView)
        let initialSize = NSSize(width: 540, height: 600)
        host.frame = NSRect(origin: .zero, size: initialSize)

        let window = CustomizationWindow(
            contentRect: NSRect(origin: .zero, size: initialSize),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = appearance.L("Customize TokenPilot", "自定义 TokenPilot")
        window.contentMinSize = NSSize(width: 500, height: 570)
        window.isReleasedWhenClosed = false
        window.contentView = host
        window.center()
        window.setFrameAutosaveName("TokenPilotCustomizationWindow")
        settingsWindow = window

        NSApplication.shared.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
        recordPanelState("customize")
    }

    func recordPanelState(_ action: String) {
        guard let resources = Bundle.main.resourceURL,
              let config = try? Data(contentsOf: resources.appendingPathComponent("runtime.json")),
              let runtime = try? JSONDecoder().decode(Runtime.self, from: config),
              let panel = self.panel else { return }
        let file = URL(fileURLWithPath: runtime.codex_home).appendingPathComponent("argon/viewer/ui-state.json")
        let value: [String: Any] = [
            "action": action,
            "visible": panel.isVisible,
            "collapsed": model.collapsed,
            "width": panel.frame.width,
            "height": panel.frame.height,
            "top": panel.frame.maxY,
            "left": panel.frame.minX,
            "theme": appearance.theme.rawValue,
            "language": appearance.language.rawValue,
            "customization_visible": settingsWindow?.isVisible ?? false,
            "has_shadow": panel.hasShadow,
            "material_kind": appearance.resolvedMaterialKind.rawValue
        ]
        if let data = try? JSONSerialization.data(withJSONObject: value) {
            try? data.write(to: file, options: .atomic)
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showPanel()
        return true
    }

    func resizePanel() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.03) { [weak self] in
            guard let self, let panel = self.panel, let view = panel.contentView else { return }
            panel.hasShadow = self.appearance.resolvedMaterialKind != .liquidGlassEffect
            view.layoutSubtreeIfNeeded()
            let size = view.fittingSize
            guard size.width > 0, size.height > 0 else { return }
            let old = panel.frame
            var newOrigin = NSPoint(x: old.minX, y: old.maxY - size.height)
            if let screen = panel.screen ?? NSScreen.main {
                let visible = screen.visibleFrame
                newOrigin.x = min(max(newOrigin.x, visible.minX + 4), visible.maxX - size.width - 4)
                newOrigin.y = min(max(newOrigin.y, visible.minY + 4), visible.maxY - size.height - 4)
            }
            panel.setFrame(NSRect(origin: newOrigin, size: size), display: true)
            self.recordPanelState("resize")
        }
    }

    @objc func quit() { NSApplication.shared.terminate(nil) }
    func applicationWillTerminate(_ notification: Notification) { model.stop() }

    func renderPreview(to path: String) {
        let args = CommandLine.arguments
        let previewAppearance = AppearanceStore(
            userDefaults: nil,
            persistsChanges: false,
            initialReduceTransparency: false,
            initialIncreaseContrast: false
        )

        if let idx = args.firstIndex(of: "--theme"), args.count > idx + 1,
           let resolvedTheme = ThemeID.resolve(args[idx + 1]) {
            previewAppearance.theme = resolvedTheme
        }
        if let idx = args.firstIndex(of: "--language"), args.count > idx + 1,
           let resolvedLang = AppLanguage.resolve(args[idx + 1]) {
            previewAppearance.language = resolvedLang
        }
        if let idx = args.firstIndex(of: "--primary"), args.count > idx + 1 {
            _ = previewAppearance.setPrimaryHex(args[idx + 1])
        }
        if let idx = args.firstIndex(of: "--secondary"), args.count > idx + 1 {
            _ = previewAppearance.setSecondaryHex(args[idx + 1])
        }

        model.collapsed = args.contains("--collapsed")
        if let index = args.firstIndex(of: "--snapshot-file"), args.count > index + 1,
           let data = try? Data(contentsOf: URL(fileURLWithPath: args[index + 1])),
           let snapshot = try? JSONDecoder().decode(Snapshot.self, from: data) {
            model.snapshot = snapshot
            model.connected = true
            model.preference = Double(snapshot.routing.gemini_percent)
        }

        let host: NSView
        if args.contains("--customization-preview") {
            let settingsHost = NSHostingView(rootView: CustomizationView(appearance: previewAppearance))
            let fit = settingsHost.fittingSize
            let targetSize = NSSize(width: max(fit.width, 540), height: max(fit.height, 600))
            settingsHost.frame = NSRect(origin: .zero, size: targetSize)
            host = settingsHost
        } else {
            let widgetHost = DraggableHostingView(rootView: RoutingView(model: model, appearance: previewAppearance))
            widgetHost.frame = NSRect(origin: .zero, size: widgetHost.fittingSize)
            host = widgetHost
        }

        let window = NSWindow(contentRect: host.frame, styleMask: .borderless, backing: .buffered, defer: false)
        window.isOpaque = false
        window.backgroundColor = .clear
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        if let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) {
            host.cacheDisplay(in: host.bounds, to: bitmap)
            if let png = bitmap.representation(using: .png, properties: [:]) {
                try? png.write(to: URL(fileURLWithPath: path))
            }
        }
        NSApplication.shared.terminate(nil)
    }

    // MARK: - Non-UI Self-Checks (--ui-checks)

    private func runSelfChecks() -> Bool {
        var passed = 0
        var failed = 0
        func check(_ condition: Bool, _ label: String) {
            if condition {
                passed += 1
            } else {
                failed += 1
                fputs("FAIL: \(label)\n", stderr)
            }
        }

        // 1. Theme resolution & defaults
        check(ThemeID.allCases.count == 6, "Exactly 6 themes exist")
        check(ThemeID.defaultTheme == .graphite, "Default theme is graphite")
        check(ThemeID.resolve("c") == nil && ThemeID.resolve("focus") == nil, "Removed Focus/C theme does not resolve")
        check(ThemeID.resolve("a") == .cupertino && ThemeID.resolve("b") == .graphite && ThemeID.resolve("d") == .instrument, "Theme aliases resolve")

        // 2. Isolated UserDefaults persistence & per-theme color isolation
        let suiteName = "local.routingfloat.viewer.checks.\(UUID().uuidString)"
        if let defaults = UserDefaults(suiteName: suiteName) {
            defer { defaults.removePersistentDomain(forName: suiteName) }

            let store1 = AppearanceStore(userDefaults: defaults, persistsChanges: true, initialReduceTransparency: false, initialIncreaseContrast: false)
            check(store1.theme == .graphite && store1.language == .english, "Fresh store uses graphite + english")
            check(store1.setPrimaryHex("#123456", for: .graphite), "Graphite accepts valid 6-digit hex")
            check(!store1.setPrimaryHex("#12345678", for: .graphite), "Rejects 8-digit hex with alpha")
            store1.theme = .cupertino
            check(store1.setPrimaryHex("#FF3366", for: .cupertino), "Cupertino accepts custom primary")
            store1.language = .chinese

            let store2 = AppearanceStore(userDefaults: defaults, persistsChanges: true, initialReduceTransparency: false, initialIncreaseContrast: false)
            check(store2.theme == .cupertino, "Persisted theme restored")
            check(store2.language == .chinese, "Persisted language restored")
            check(store2.colorPair(for: .graphite).primaryHex == "#123456", "Graphite custom color survived theme switch")
            check(store2.colorPair(for: .cupertino).primaryHex == "#FF3366", "Cupertino custom color persisted")

            store2.resetColorsForCurrentTheme()
            check(store2.colorPair(for: .cupertino) == ThemeID.cupertino.defaultColorPair, "Reset restored current theme default")
            check(store2.colorPair(for: .graphite).primaryHex == "#123456", "Reset did not wipe Graphite custom color")
            check(store2.language == .chinese, "Reset did not alter language")

            store2.theme = .instrument
            check(!store2.theme.supportsCustomColors, "Instrument disables custom colors")
            check(!store2.setPrimaryHex("#FF0000"), "Instrument ignores setPrimaryHex")
        }

        // 3. Contrast verification across all 6 themes and extreme custom colors
        let testHexes = ["#000000", "#FFFFFF", "#FFFF00", "#0000FF", "#FF0055", "#22D3EE"]
        for theme in ThemeID.allCases {
            for hex in testHexes {
                let pair = ThemeColorPair(primaryHex: hex, secondaryHex: hex)
                let pal = ResolvedThemePalette.resolve(theme: theme, colorPair: pair, materialKind: .solid)
                let surfaces = [pal.surfaceRGB, pal.insetRGB] + pal.gradientStops
                check(RGBColor.minContrastRatio(pal.primaryTextRGB, against: surfaces) >= 4.5, "\(theme.rawValue) primaryText contrast >= 4.5")
                check(RGBColor.minContrastRatio(pal.secondaryTextRGB, against: surfaces) >= 4.5, "\(theme.rawValue) secondaryText contrast >= 4.5")
                check(RGBColor.minContrastRatio(pal.readablePrimaryTextRGB, against: surfaces) >= 4.5, "\(theme.rawValue) readablePrimary (\(hex)) contrast >= 4.5")
                check(RGBColor.minContrastRatio(pal.readableSecondaryTextRGB, against: surfaces) >= 4.5, "\(theme.rawValue) readableSecondary (\(hex)) contrast >= 4.5")
            }
        }

        // 4. Quota selection & Reset-first semantics
        let previewStore = AppearanceStore(userDefaults: nil, persistsChanges: false, initialReduceTransparency: false, initialIncreaseContrast: false)
        let testModel = FloatModel()
        testModel.snapshot = Snapshot(
            timestamp: "2026-10-24T15:33:00Z",
            codex: Usage(total_tokens: 51_200_000, cached_tokens: 50_100_000, available: true),
            gemini: Usage(total_tokens: 148_000, cached_tokens: 51_800, available: true, status: "completed", status_message: "Gemini worker completed"),
            routing: Routing(gemini_percent: 70),
            limits: Limits(
                five_hour: WindowLimit(used_percent: 3.0, resets_at: 1_792_860_000),
                weekly: WindowLimit(used_percent: 20.0, resets_at: 1_793_100_000)
            ),
            warnings: [],
            forecast: UsageForecast(
                status: "reset_before_projection",
                headline: "Next reset comes first",
                detail: "5-hour resets first · weekly quota remains",
                exhaustion_at: nil,
                tokens_per_minute: 145_000,
                five_hour: ForecastWindow(status: "reset_before_projection", message: "Next reset comes first", exhaustion_at: 1_792_900_000),
                weekly: ForecastWindow(status: "reset_before_projection", message: "Next reset comes first", exhaustion_at: 1_793_200_000),
                display_window: "five_hour",
                selection_reason: "5-hour has >95% remaining; showing weekly",
                next_reset_window: "five_hour"
            ),
            nextreset: nil
        )
        let viewEN = RoutingView(model: testModel, appearance: previewStore)
        check(viewEN.selectedCollapsedQuota.percentText == "80%", "5-hour >95% remaining selects Weekly (80%)")
        check(viewEN.selectedCollapsedQuota.label.contains("Wk") && viewEN.selectedCollapsedQuota.label.contains("reset first"), "English reset-first collapsed label")
        previewStore.language = .chinese
        let viewZH = RoutingView(model: testModel, appearance: previewStore)
        check(viewZH.selectedCollapsedQuota.label.contains("周") && viewZH.selectedCollapsedQuota.label.contains("先重置"), "Chinese reset-first collapsed label")

        print("TokenPilot UI checks: \(passed) passed, \(failed) failed")
        return failed == 0
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.run()
