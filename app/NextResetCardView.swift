// SwiftUI card presentation and validation for public NextReset forecast summaries.
// NextReset 公共社区重置预测摘要的 SwiftUI 卡片展示与数据校验组件。
import AppKit
import Foundation
import SwiftUI

struct NextResetCardPresentation: Equatable {
    var probability24Text: String
    var probability48Text: String
    var probability72Text: String
    var statusText: String
    var chanceCaption: String
    var countdownText: String?
    var isCountdownElapsed: Bool
    var warningBadgeText: String?
    var isStaleOrDelayed: Bool
    var isAnnounced: Bool
    var isAvailable: Bool
    var signalURL: URL?
    var signalLinkText: String
    var headerTitle: String
    var label48h: String
    var label72h: String
    var unavailableFooterText: String?
    var tooltipText: String
    var accessibilitySummary: String
}

struct NextResetCardView: View {
    let info: NextResetInfo?
    let language: AppLanguage
    let palette: ResolvedThemePalette
    let openHome: () -> Void

    init(
        info: NextResetInfo?,
        language: AppLanguage,
        palette: ResolvedThemePalette,
        openHome: @escaping () -> Void
    ) {
        self.info = info
        self.language = language
        self.palette = palette
        self.openHome = openHome
    }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30.0)) { context in
            cardBody(now: context.date)
        }
    }

    @ViewBuilder
    func cardBody(now: Date) -> some View {
        let model = Self.presentation(for: info, language: language, now: now)
        let isLiquid = palette.materialKind == .liquidGlassEffect

        VStack(alignment: .leading, spacing: 6) {
            headerRow(model: model)

            heroRow(model: model)

            if let countdownText = model.countdownText {
                countdownRow(text: countdownText, model: model)
            }

            Rectangle()
                .fill(
                    palette.borderColor.opacity(
                        isLiquid ? (palette.isDark ? 0.24 : 0.18) : (palette.isDark ? 0.55 : 0.45)
                    )
                )
                .frame(height: 1)
                .padding(.vertical, 1)

            footerRow(model: model)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(isLiquid ? Color.clear : palette.insetColor)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(
                    isLiquid ? Color.clear : palette.borderColor.opacity(palette.isDark ? 0.75 : 0.65),
                    lineWidth: 1
                )
        )
        .help(model.tooltipText)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(model.accessibilitySummary)
    }

    @ViewBuilder
    private func headerRow(model: NextResetCardPresentation) -> some View {
        HStack(alignment: .center, spacing: 8) {
            Button(action: openHome) {
                FrostText(model.headerTitle)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(palette.readablePrimaryTextColor)
                    .lineLimit(1)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(language.L("NextReset home", "NextReset 首页"))
            .accessibilityHint(language.L("Opens nextreset.ai in browser", "在浏览器中打开 nextreset.ai"))

            Spacer(minLength: 6)

            if let warningBadge = model.warningBadgeText {
                HStack(spacing: 4) {
                    Circle()
                        .fill(palette.warningOrangeColor)
                        .frame(width: 5, height: 5)
                    FrostText(warningBadge)
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(palette.warningOrangeColor)
                        .lineLimit(1)
                }
            } else if model.isAvailable {
                HStack(spacing: 4) {
                    Circle()
                        .fill(model.isAnnounced ? palette.statusGreenColor : palette.secondaryTextColor.opacity(0.75))
                        .frame(width: 5, height: 5)
                    FrostText(
                        model.isAnnounced
                            ? language.L("Announced", "已宣布")
                            : language.L("Live", "实时")
                    )
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(palette.secondaryTextColor)
                    .lineLimit(1)
                }
            }
        }
    }

    @ViewBuilder
    private func heroRow(model: NextResetCardPresentation) -> some View {
        HStack(alignment: .center, spacing: 10) {
            FrostText(model.probability24Text)
                .font(.system(size: 32, weight: palette.theme == .liquid ? .semibold : .bold, design: palette.theme == .liquid ? .default : .rounded))
                .monospacedDigit()
                .foregroundStyle(model.isAvailable ? palette.primaryTextColor : palette.secondaryTextColor)
                .lineLimit(1)
                .fixedSize(horizontal: true, vertical: false)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 5) {
                    Circle()
                        .fill(statusIndicatorColor(model: model))
                        .frame(width: 6, height: 6)

                    FrostText(model.statusText)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(statusTextColor(model: model))
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }

                FrostText(model.chanceCaption)
                    .font(.system(size: 10.5, weight: palette.theme == .liquid ? .medium : .regular))
                    .foregroundStyle(palette.secondaryTextColor)
                    .lineLimit(1)
                    .minimumScaleFactor(0.88)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder
    private func countdownRow(text: String, model: NextResetCardPresentation) -> some View {
        HStack(alignment: .center, spacing: 5) {
            Circle()
                .fill(
                    model.isCountdownElapsed
                        ? palette.secondaryTextColor.opacity(0.85)
                        : palette.statusGreenColor
                )
                .frame(width: 5, height: 5)

            FrostText(text)
                .font(.system(size: 11, weight: .medium))
                .monospacedDigit()
                .foregroundStyle(
                    model.isCountdownElapsed
                        ? palette.secondaryTextColor
                        : palette.readablePrimaryTextColor
                )
                .lineLimit(1)
                .minimumScaleFactor(0.85)
        }
    }

    @ViewBuilder
    private func footerRow(model: NextResetCardPresentation) -> some View {
        HStack(alignment: .center, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                probabilityChip(label: model.label48h, value: model.probability48Text)

                FrostText("·")
                    .font(.system(size: 10.5, weight: palette.theme == .liquid ? .medium : .regular))
                    .foregroundStyle(palette.secondaryTextColor)
                    .lineLimit(1)

                probabilityChip(label: model.label72h, value: model.probability72Text)
            }

            Spacer(minLength: 6)

            if let signalURL = model.signalURL {
                Link(destination: signalURL) {
                    FrostText(model.signalLinkText)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(palette.readablePrimaryTextColor)
                        .lineLimit(1)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(language.L("Original reset signal", "原始重置信号"))
                .accessibilityHint(
                    language.L("Opens original announcement post in browser", "在浏览器中打开原始公告帖子")
                )
            } else if let unavailableNote = model.unavailableFooterText {
                FrostText(unavailableNote)
                    .font(.system(size: 10, weight: .regular))
                    .foregroundStyle(palette.secondaryTextColor)
                    .lineLimit(1)
            }
        }
    }

    @ViewBuilder
    private func probabilityChip(label: String, value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            FrostText(label)
                .font(.system(size: 10.5, weight: palette.theme == .liquid ? .medium : .regular))
                .foregroundStyle(palette.secondaryTextColor)
                .lineLimit(1)

            FrostText(value)
                .font(.system(size: 11, weight: .semibold))
                .monospacedDigit()
                .foregroundStyle(palette.readablePrimaryTextColor)
                .lineLimit(1)
        }
    }

    private func statusIndicatorColor(model: NextResetCardPresentation) -> Color {
        if !model.isAvailable {
            return palette.warningOrangeColor
        }
        if model.isAnnounced {
            return model.isStaleOrDelayed ? palette.warningOrangeColor : palette.statusGreenColor
        }
        if model.isStaleOrDelayed {
            return palette.warningOrangeColor
        }
        return palette.secondaryTextColor
    }

    private func statusTextColor(model: NextResetCardPresentation) -> Color {
        if !model.isAvailable {
            return palette.secondaryTextColor
        }
        return palette.primaryTextColor
    }

    // MARK: - Pure Testable Helpers

    static func parseISO8601Date(_ raw: String?) -> Date? {
        guard let raw else { return nil }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              trimmed.contains("T") || trimmed.contains("t") || trimmed.contains(" ")
        else {
            return nil
        }

        var normalized = trimmed
        if normalized.hasSuffix("z") {
            normalized.removeLast()
            normalized.append("Z")
        }
        if let tIndex = normalized.firstIndex(of: "t"),
            normalized.distance(from: normalized.startIndex, to: tIndex) == 10 {
            normalized.replaceSubrange(tIndex...tIndex, with: "T")
        } else if let spaceIndex = normalized.firstIndex(of: " "),
                  normalized.distance(from: normalized.startIndex, to: spaceIndex) == 10 {
            normalized.replaceSubrange(spaceIndex...spaceIndex, with: "T")
        }

        let fractionalFormatter = ISO8601DateFormatter()
        fractionalFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = fractionalFormatter.date(from: normalized) {
            return date
        }

        let standardFormatter = ISO8601DateFormatter()
        standardFormatter.formatOptions = [.withInternetDateTime]
        if let date = standardFormatter.date(from: normalized) {
            return date
        }

        if let dotIndex = normalized.firstIndex(of: ".") {
            let afterDot = normalized.index(after: dotIndex)
            let suffix = normalized[afterDot...]
            if let tzStart = suffix.firstIndex(where: { $0 == "Z" || $0 == "+" || $0 == "-" }) {
                let digits = suffix[..<tzStart]
                if !digits.isEmpty && digits.allSatisfy(\.isNumber) {
                    let padded = String(digits.prefix(3)).padding(toLength: 3, withPad: "0", startingAt: 0)
                    let rebuilt = String(normalized[..<afterDot]) + padded + String(suffix[tzStart...])
                    if let date = fractionalFormatter.date(from: rebuilt) {
                        return date
                    }
                }
            }
        }

        return nil
    }

    static func formatProbability(_ value: Double?) -> String {
        guard let value, value.isFinite, value >= 0.0, value <= 1.0 else {
            return "—"
        }
        let percentage = min(100, max(0, Int((value * 100.0).rounded())))
        return "\(percentage)%"
    }

    static func validatedSignalURL(_ raw: String?) -> URL? {
        guard let raw else { return nil }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              !trimmed.unicodeScalars.contains(where: {
                  CharacterSet.whitespacesAndNewlines.contains($0) || CharacterSet.controlCharacters.contains($0)
              }),
              let components = URLComponents(string: trimmed),
              let scheme = components.scheme?.lowercased(),
              scheme == "https",
              components.user == nil,
              components.password == nil,
              let host = components.host?.lowercased(),
              !host.isEmpty
        else {
            return nil
        }

        let allowedHost =
            host == "x.com" ||
            host == "www.x.com" ||
            host == "mobile.x.com" ||
            host == "twitter.com" ||
            host == "www.twitter.com" ||
            host == "mobile.twitter.com"
        guard allowedHost else {
            return nil
        }

        if let port = components.port, !(1...65535).contains(port) {
            return nil
        }
        return components.url
    }

    static func isAnnounced(_ info: NextResetInfo?) -> Bool {
        if let rawState = info?.announcement_state?.trimmingCharacters(in: .whitespacesAndNewlines),
           !rawState.isEmpty {
            return rawState.lowercased() == "announced"
        }
        return parseISO8601Date(info?.expected_by) != nil
    }

    static func hasUsableData(_ info: NextResetInfo?) -> Bool {
        guard let info else { return false }
        if info.probability_24h != nil ||
            info.probability_48h != nil ||
            info.probability_72h != nil ||
            info.expected_by != nil ||
            info.source_as_of != nil ||
            info.latest_report_at != nil ||
            isAnnounced(info) {
            return true
        }
        let trimmedHeadline = info.headline.trimmingCharacters(in: .whitespacesAndNewlines)
        return info.error == nil && !trimmedHeadline.isEmpty
    }

    static func isSnapshotStale(_ info: NextResetInfo?, now: Date = Date()) -> Bool {
        guard let info else { return false }
        if info.stale {
            return true
        }
        if let expiresAt = parseISO8601Date(info.expires_at), now >= expiresAt {
            return true
        }
        return false
    }

    static func formatCountdown(
        expectedBy: String?,
        language: AppLanguage = .english,
        now: Date = Date()
    ) -> String? {
        guard let expectedByDate = parseISO8601Date(expectedBy) else {
            return nil
        }
        return formatCountdown(expectedByDate: expectedByDate, language: language, now: now)
    }

    static func formatCountdown(
        expectedByDate: Date,
        language: AppLanguage = .english,
        now: Date = Date()
    ) -> String {
        let remaining = expectedByDate.timeIntervalSince(now)
        if remaining <= 0 {
            return language.L(
                "Window ended · awaiting confirmation",
                "窗口已结束 · 等待确认"
            )
        }
        if remaining < 60 {
            return language.L(
                "Window ends in <1m",
                "预计窗口剩余 <1分钟"
            )
        }
        let totalMinutes = Int(remaining / 60.0)
        let hours = totalMinutes / 60
        let minutes = totalMinutes % 60
        if hours > 0 {
            if minutes > 0 {
                return language.L(
                    "Window ends in \(hours)h \(minutes)m",
                    "预计窗口剩余 \(hours)小时 \(minutes)分"
                )
            }
            return language.L(
                "Window ends in \(hours)h",
                "预计窗口剩余 \(hours)小时"
            )
        }
        return language.L(
            "Window ends in \(minutes)m",
            "预计窗口剩余 \(minutes)分钟"
        )
    }

    static func statusText(
        for info: NextResetInfo?,
        language: AppLanguage = .english,
        now: Date = Date()
    ) -> String {
        guard hasUsableData(info) else {
            return language.L("Forecast unavailable", "预测暂不可用")
        }
        if isAnnounced(info) {
            return language.L("Reset announced · awaiting rollout", "已宣布重置 · 等待推送")
        }
        return language.L("Community reset forecast", "社区重置预测")
    }

    static func warningBadgeText(
        for info: NextResetInfo?,
        language: AppLanguage = .english,
        now: Date = Date()
    ) -> String? {
        guard let info, hasUsableData(info) else {
            return language.L("Unavailable", "不可用")
        }
        let stale = isSnapshotStale(info, now: now)
        let delayed = info.source_delayed
        if stale && delayed {
            return language.L("Stale · Source delayed", "数据过期 · 源端延迟")
        }
        if stale {
            return language.L("Stale snapshot", "快照已过期")
        }
        if delayed {
            return language.L("Source delayed", "源端延迟")
        }
        if let err = info.error?.trimmingCharacters(in: .whitespacesAndNewlines), !err.isEmpty {
            return language.L("Refresh delayed", "刷新延迟")
        }
        return nil
    }

    static func presentation(
        for info: NextResetInfo?,
        language: AppLanguage = .english,
        now: Date = Date()
    ) -> NextResetCardPresentation {
        let available = hasUsableData(info)
        let announced = available && isAnnounced(info)
        let prob24 = available ? formatProbability(info?.probability_24h) : "—"
        let prob48 = available ? formatProbability(info?.probability_48h) : "—"
        let prob72 = available ? formatProbability(info?.probability_72h) : "—"
        let status = statusText(for: info, language: language, now: now)
        let caption = language.L(
            "Estimated chance in next 24h",
            "预计未来 24 小时重置概率"
        )

        var countdownText: String?
        var isCountdownElapsed = false
        if announced {
            if let expectedDate = parseISO8601Date(info?.expected_by) {
                isCountdownElapsed = expectedDate.timeIntervalSince(now) <= 0
                countdownText = formatCountdown(expectedByDate: expectedDate, language: language, now: now)
            } else {
                countdownText = language.L(
                    "Rollout window pending · awaiting confirmation",
                    "推送窗口待定 · 等待确认"
                )
            }
        }

        let warningBadge = warningBadgeText(for: info, language: language, now: now)
        let staleOrDelayed = !available || warningBadge != nil
        let validSignal = available ? validatedSignalURL(info?.signal_url) : nil
        let signalTitle = language.L("Original signal ↗", "原始信号 ↗")
        let headerTitle = "NextReset ↗"
        let label48 = language.L("48h", "48小时")
        let label72 = language.L("72h", "72小时")

        let unavailableFooter: String? = available
            ? nil
            : language.L("Public forecast offline", "公共预测暂不可用")

        var summaryParts: [String] = [
            "NextReset",
            status,
            "\(caption): \(prob24)",
            "\(label48) \(prob48)",
            "\(label72) \(prob72)",
        ]
        if let countdownText {
            summaryParts.append(countdownText)
        }
        if let warningBadge {
            summaryParts.append(warningBadge)
        }
        let summary = summaryParts.joined(separator: " · ")

        var tooltipParts = [summary, language.L(
            "Public community forecast, not your personal quota. Experimental estimate; an announcement is not a confirmed reset.",
            "公共社区预测，并非个人配额。此为实验性估计；公告不代表重置已完成。")]
        if let updated = info?.source_as_of { tooltipParts.append(language.L("Source updated: ", "源更新时间：") + updated) }
        if let err = info?.error?.trimmingCharacters(in: .whitespacesAndNewlines), !err.isEmpty {
            tooltipParts.append(err)
        }
        let tooltip = tooltipParts.joined(separator: "\n")

        return NextResetCardPresentation(
            probability24Text: prob24,
            probability48Text: prob48,
            probability72Text: prob72,
            statusText: status,
            chanceCaption: caption,
            countdownText: countdownText,
            isCountdownElapsed: isCountdownElapsed,
            warningBadgeText: warningBadge,
            isStaleOrDelayed: staleOrDelayed,
            isAnnounced: announced,
            isAvailable: available,
            signalURL: validSignal,
            signalLinkText: signalTitle,
            headerTitle: headerTitle,
            label48h: label48,
            label72h: label72,
            unavailableFooterText: unavailableFooter,
            tooltipText: tooltip,
            accessibilitySummary: summary
        )
    }
}
