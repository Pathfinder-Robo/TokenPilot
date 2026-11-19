import Charts
import Foundation
import SwiftUI

enum ChatUsageDateParser {
    private static let fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
    private static let plain = ISO8601DateFormatter()

    static func parse(_ value: String) -> Date? {
        fractional.date(from: value) ?? plain.date(from: value)
    }
}

struct ChatUsageTrend: Decodable, Equatable {
    struct TopChat: Decodable, Identifiable, Equatable {
        let id: String
        let title: String?
        let codex_usd: Double

        private enum CodingKeys: String, CodingKey {
            case id
            case title
            case codex_usd
        }

        init(id: String, title: String?, codex_usd: Double) {
            self.id = id
            self.title = title
            self.codex_usd = codex_usd
        }

        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            self.id = try container.decode(String.self, forKey: .id)
            self.title = try container.decodeIfPresent(String.self, forKey: .title)
            self.codex_usd = try container.decodeIfPresent(Double.self, forKey: .codex_usd) ?? 0
        }
    }

    struct Bucket: Decodable, Equatable {
        let start_at: String
        let end_at: String
        let total_usd: Double?
        let chat_usd: [String: Double?]

        private enum CodingKeys: String, CodingKey {
            case start_at
            case end_at
            case total_usd
            case chat_usd
        }

        private struct DynamicChatCodingKey: CodingKey {
            var stringValue: String
            var intValue: Int? { nil }

            init?(stringValue: String) {
                self.stringValue = stringValue
            }

            init?(intValue: Int) {
                nil
            }
        }

        init(
            start_at: String,
            end_at: String,
            total_usd: Double?,
            chat_usd: [String: Double?]
        ) {
            self.start_at = start_at
            self.end_at = end_at
            self.total_usd = total_usd
            self.chat_usd = chat_usd
        }

        init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            self.start_at = try container.decode(String.self, forKey: .start_at)
            self.end_at = try container.decode(String.self, forKey: .end_at)
            self.total_usd = try container.decodeIfPresent(Double.self, forKey: .total_usd)

            var parsedChatUSD: [String: Double?] = [:]
            if container.contains(.chat_usd),
               (try? container.decodeNil(forKey: .chat_usd)) != true {
                let chatContainer = try container.nestedContainer(
                    keyedBy: DynamicChatCodingKey.self,
                    forKey: .chat_usd
                )
                for key in chatContainer.allKeys {
                    if try chatContainer.decodeNil(forKey: key) {
                        parsedChatUSD[key.stringValue] = .some(nil)
                    } else {
                        let amount = try chatContainer.decode(Double.self, forKey: key)
                        parsedChatUSD[key.stringValue] = .some(amount)
                    }
                }
            }
            self.chat_usd = parsedChatUSD
        }
    }

    let bucket_seconds: Int
    let partial: Bool
    let top_chats: [TopChat]
    let buckets: [Bucket]

    private enum CodingKeys: String, CodingKey {
        case bucket_seconds
        case partial
        case top_chats
        case buckets
    }

    init(
        bucket_seconds: Int,
        partial: Bool,
        top_chats: [TopChat],
        buckets: [Bucket]
    ) {
        self.bucket_seconds = bucket_seconds
        self.partial = partial
        self.top_chats = top_chats
        self.buckets = buckets
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.bucket_seconds = try container.decode(Int.self, forKey: .bucket_seconds)
        self.partial = try container.decodeIfPresent(Bool.self, forKey: .partial) ?? false
        self.top_chats = try container.decodeIfPresent([TopChat].self, forKey: .top_chats) ?? []
        self.buckets = try container.decodeIfPresent([Bucket].self, forKey: .buckets) ?? []
    }
}

struct ChatUsageTrendView: View {
    let trend: ChatUsageTrend
    let timeZone: TimeZone
    let loc: TokenPilotLocalizer
    let palette: ResolvedThemePalette

    init(
        trend: ChatUsageTrend,
        timeZone: TimeZone,
        loc: TokenPilotLocalizer,
        palette: ResolvedThemePalette
    ) {
        self.trend = trend
        self.timeZone = timeZone
        self.loc = loc
        self.palette = palette
    }

    private var localCalendar: Calendar {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        return cal
    }

    var body: some View {
        let calendar = localCalendar
        let prepared = PreparedTrendData.build(
            trend: trend,
            isDark: palette.isDark,
            calendar: calendar,
            loc: loc
        )

        VStack(alignment: .leading, spacing: 8) {
            TrendSectionHeaderView(
                bucketLabel: prepared.bucketWidthLabel,
                rangeLabel: prepared.windowRangeLabel,
                partial: trend.partial,
                loc: loc,
                palette: palette
            )

            if let window = prepared.window {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: 12) {
                        TotalUsageCardView(
                            window: window,
                            calendar: calendar,
                            chartHeight: 148,
                            loc: loc,
                            palette: palette
                        )
                        .frame(minWidth: 320, idealWidth: 340, maxWidth: .infinity)

                        TopChatsCardView(
                            window: window,
                            calendar: calendar,
                            chartHeight: 148,
                            loc: loc,
                            palette: palette
                        )
                        .frame(minWidth: 320, idealWidth: 340, maxWidth: .infinity)
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        TotalUsageCardView(
                            window: window,
                            calendar: calendar,
                            chartHeight: 120,
                            loc: loc,
                            palette: palette
                        )
                        .frame(maxWidth: .infinity)

                        TopChatsCardView(
                            window: window,
                            calendar: calendar,
                            chartHeight: 120,
                            loc: loc,
                            palette: palette
                        )
                        .frame(maxWidth: .infinity)
                    }
                }
            } else {
                TrendEmptyStateCardView(
                    message: loc.L(
                        "No valid usage intervals in this reset window.",
                        "此重置窗口内暂无有效用量时段。"
                    ),
                    palette: palette
                )
            }
        }
        .environment(\.timeZone, timeZone)
        .environment(\.calendar, calendar)
    }
}

// MARK: - Decomposed Subviews

private struct TrendSectionHeaderView: View {
    let bucketLabel: String
    let rangeLabel: String?
    let partial: Bool
    let loc: TokenPilotLocalizer
    let palette: ResolvedThemePalette

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    primaryLabels
                    Spacer(minLength: 8)
                    unitLabel
                }

                VStack(alignment: .leading, spacing: 2) {
                    primaryLabels
                    unitLabel
                }
            }

            if partial {
                HStack(spacing: 5) {
                    Circle()
                        .fill(palette.warningOrangeColor)
                        .frame(width: 6, height: 6)
                        .accessibilityHidden(true)
                    Text(
                        loc.L(
                            "Partial data · showing recorded usage",
                            "数据不完整 · 显示已记录用量"
                        )
                    )
                    .font(.caption2)
                    .foregroundStyle(palette.warningOrangeColor)
                    .lineLimit(2)
                }
            }
        }
    }

    private var primaryLabels: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(loc.L("Codex usage trend", "Codex 用量趋势"))
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(palette.readablePrimaryTextColor)

            Text(bucketLabel)
                .font(.caption2.weight(.medium))
                .foregroundStyle(palette.readablePrimaryTextColor)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(
                    Capsule(style: .continuous)
                        .fill(palette.insetColor)
                )
                .overlay(
                    Capsule(style: .continuous)
                        .strokeBorder(
                            palette.secondaryTextColor.opacity(palette.isDark ? 0.30 : 0.22),
                            lineWidth: 0.75
                        )
                )

            if let rangeLabel {
                Text(rangeLabel)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(palette.secondaryTextColor)
                    .lineLimit(1)
            }
        }
    }

    private var unitLabel: some View {
        Text(loc.L("Gemini-equiv. ref. USD (not billed)", "Gemini 等价参考 USD（非账单金额）"))
            .font(.caption2)
            .foregroundStyle(palette.secondaryTextColor)
            .lineLimit(1)
    }
}

private struct TotalUsageCardView: View {
    let window: PreparedWindowModel
    let calendar: Calendar
    let chartHeight: CGFloat
    let loc: TokenPilotLocalizer
    let palette: ResolvedThemePalette

    @State private var hoveredBucketIndex: Int?

    private var hoveredBucket: ParsedBucketSlot? {
        guard let idx = hoveredBucketIndex,
              let slot = window.bucketsByIndex[idx],
              slot.totalUSD != nil else {
            return nil
        }
        return slot
    }

    var body: some View {
        let title = loc.L("Total Codex usage", "Codex 总用量")

        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(title)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(palette.readablePrimaryTextColor)
                    .lineLimit(1)

                Spacer(minLength: 6)

                if let slot = hoveredBucket {
                    Text(
                        "\(TrendFormatting.bucketSpanLabel(slot, calendar: calendar, loc: loc)) · \(ReferencePricing.formatUSD(slot.totalUSD))"
                    )
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(palette.readablePrimaryTextColor)
                    .lineLimit(1)
                } else if window.totalPlot.hasData {
                    Text(
                        "\(loc.L("Known sum", "已知合计")) \(ReferencePricing.formatUSD(window.totalPlot.knownSumUSD))"
                    )
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(palette.secondaryTextColor)
                    .lineLimit(1)
                }
            }

            if window.totalPlot.hasData {
                TotalUsagePlotView(
                    plot: window.totalPlot,
                    xDomain: window.xDomain,
                    bucketSeconds: window.bucketSeconds,
                    validSlots: window.validSlots,
                    hoveredBucketIndex: $hoveredBucketIndex,
                    calendar: calendar,
                    chartHeight: chartHeight,
                    loc: loc,
                    palette: palette
                )

                HStack(spacing: 8) {
                    SeriesMarkerSwatchView(
                        color: window.totalPlot.color,
                        symbol: .circle
                    )
                    Text("Codex")
                        .font(.caption2)
                        .foregroundStyle(palette.secondaryTextColor)
                        .lineLimit(1)
                    Spacer(minLength: 6)
                    Text(
                        "\(loc.L("Peak", "峰值")) \(ReferencePricing.formatUSD(window.totalPlot.peakUSD))"
                    )
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(palette.secondaryTextColor)
                    .lineLimit(1)
                }
                .accessibilityElement(children: .combine)
            } else {
                TrendPlotEmptyPlaceholderView(
                    message: loc.L("No recorded Codex usage in this window", "此时间段暂无 Codex 用量记录"),
                    height: chartHeight + 20,
                    palette: palette
                )
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(palette.insetColor)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(
                    palette.secondaryTextColor.opacity(palette.isDark ? 0.22 : 0.16),
                    lineWidth: 0.75
                )
        )
    }
}

private struct TotalUsagePlotView: View {
    let plot: SingleSeriesPlotModel
    let xDomain: ClosedRange<Date>
    let bucketSeconds: Int
    let validSlots: [ParsedBucketSlot]
    @Binding var hoveredBucketIndex: Int?
    let calendar: Calendar
    let chartHeight: CGFloat
    let loc: TokenPilotLocalizer
    let palette: ResolvedThemePalette

    var body: some View {
        let xLabel = loc.L("Local time", "本地时间")
        let yLabel = loc.L("Gemini-equiv. USD", "Gemini 等价 USD")

        Chart {
            ForEach(plot.linePoints) { pt in
                LineMark(
                    x: .value(xLabel, pt.midpoint),
                    y: .value(yLabel, pt.usd),
                    series: .value("Segment", pt.segmentID)
                )
                .interpolationMethod(.linear)
                .foregroundStyle(by: .value("Segment", pt.segmentID))
                .lineStyle(StrokeStyle(lineWidth: 2))
            }

            ForEach(plot.markerPoints) { pt in
                PointMark(
                    x: .value(xLabel, pt.midpoint),
                    y: .value(yLabel, pt.usd)
                )
                .foregroundStyle(by: .value("Segment", pt.segmentID))
                .symbolSize(26)
            }

            if let hoveredIdx = hoveredBucketIndex,
               let slot = validSlots.first(where: { $0.bucketIndex == hoveredIdx }),
               slot.totalUSD != nil {
                RuleMark(x: .value(xLabel, slot.midpoint))
                    .foregroundStyle(palette.secondaryTextColor.opacity(0.40))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
            }
        }
        .chartForegroundStyleScale(domain: plot.segmentDomain, range: plot.segmentColors)
        .chartLegend(.hidden)
        .chartXScale(domain: xDomain)
        .chartYScale(domain: 0...plot.yUpperBound)
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 4)) { value in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                    .foregroundStyle(palette.secondaryTextColor.opacity(palette.isDark ? 0.18 : 0.14))
                AxisTick(stroke: StrokeStyle(lineWidth: 0.5))
                    .foregroundStyle(palette.secondaryTextColor.opacity(0.35))
                AxisValueLabel {
                    if let date = value.as(Date.self) {
                        Text(
                            TrendFormatting.axisDateLabel(
                                date,
                                domain: xDomain,
                                bucketSeconds: bucketSeconds,
                                calendar: calendar
                            )
                        )
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(palette.secondaryTextColor)
                        .lineLimit(1)
                    }
                }
            }
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                    .foregroundStyle(palette.secondaryTextColor.opacity(palette.isDark ? 0.18 : 0.14))
                AxisTick(stroke: StrokeStyle(lineWidth: 0.5))
                    .foregroundStyle(palette.secondaryTextColor.opacity(0.35))
                AxisValueLabel {
                    if let usd = value.as(Double.self) {
                        Text(ReferencePricing.formatUSD(usd))
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(palette.secondaryTextColor)
                            .lineLimit(1)
                    }
                }
            }
        }
        .chartOverlay { proxy in
            ChartHoverOverlayView(
                proxy: proxy,
                validSlots: validSlots,
                hoveredBucketIndex: $hoveredBucketIndex
            )
        }
        .frame(height: chartHeight)
        .accessibilityLabel(Text(loc.L("Total Codex usage chart", "Codex 总用量图表")))
        .accessibilityValue(
            Text(
                loc.L(
                    "\(plot.markerPoints.count) intervals, sum \(ReferencePricing.formatUSD(plot.knownSumUSD)), peak \(ReferencePricing.formatUSD(plot.peakUSD))",
                    "\(plot.markerPoints.count) 个时段，合计 \(ReferencePricing.formatUSD(plot.knownSumUSD))，峰值 \(ReferencePricing.formatUSD(plot.peakUSD))"
                )
            )
        )
    }
}

private struct TopChatsCardView: View {
    let window: PreparedWindowModel
    let calendar: Calendar
    let chartHeight: CGFloat
    let loc: TokenPilotLocalizer
    let palette: ResolvedThemePalette

    @State private var hoveredBucketIndex: Int?

    private var hoveredSlot: ParsedBucketSlot? {
        guard let idx = hoveredBucketIndex else { return nil }
        return window.bucketsByIndex[idx]
    }

    var body: some View {
        let title = loc.L("Top chats by Codex usage", "Codex 用量前三")

        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(title)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(palette.readablePrimaryTextColor)
                    .lineLimit(1)

                Spacer(minLength: 6)

                if let slot = hoveredSlot {
                    Text(TrendFormatting.bucketSpanLabel(slot, calendar: calendar, loc: loc))
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(palette.readablePrimaryTextColor)
                        .lineLimit(1)
                }
            }

            if window.topChatsPlot.hasSeries && window.topChatsPlot.hasPlottedPoints {
                TopChatsPlotView(
                    plot: window.topChatsPlot,
                    xDomain: window.xDomain,
                    bucketSeconds: window.bucketSeconds,
                    validSlots: window.validSlots,
                    hoveredBucketIndex: $hoveredBucketIndex,
                    calendar: calendar,
                    chartHeight: chartHeight,
                    loc: loc,
                    palette: palette
                )

                TopChatsLegendView(
                    seriesList: window.topChatsPlot.seriesList,
                    hoveredSlot: hoveredSlot,
                    loc: loc,
                    palette: palette
                )
            } else if window.topChatsPlot.hasSeries {
                TrendPlotEmptyPlaceholderView(
                    message: loc.L(
                        "No known interval values for top chats",
                        "前三对话在此时间段内暂无已知分桶值"
                    ),
                    height: chartHeight,
                    palette: palette
                )

                TopChatsLegendView(
                    seriesList: window.topChatsPlot.seriesList,
                    hoveredSlot: nil,
                    loc: loc,
                    palette: palette
                )
            } else {
                TrendPlotEmptyPlaceholderView(
                    message: loc.L(
                        "No attributed Codex chat usage in this window",
                        "此时间段内暂无可归属的 Codex 对话用量"
                    ),
                    height: chartHeight + 20,
                    palette: palette
                )
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(palette.insetColor)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(
                    palette.secondaryTextColor.opacity(palette.isDark ? 0.22 : 0.16),
                    lineWidth: 0.75
                )
        )
    }
}

private struct TopChatsPlotView: View {
    let plot: MultiSeriesPlotModel
    let xDomain: ClosedRange<Date>
    let bucketSeconds: Int
    let validSlots: [ParsedBucketSlot]
    @Binding var hoveredBucketIndex: Int?
    let calendar: Calendar
    let chartHeight: CGFloat
    let loc: TokenPilotLocalizer
    let palette: ResolvedThemePalette

    var body: some View {
        let xLabel = loc.L("Local time", "本地时间")
        let yLabel = loc.L("Gemini-equiv. USD", "Gemini 等价 USD")

        Chart {
            ForEach(plot.linePoints) { pt in
                LineMark(
                    x: .value(xLabel, pt.midpoint),
                    y: .value(yLabel, pt.usd),
                    series: .value("Segment", pt.segmentID)
                )
                .interpolationMethod(.linear)
                .foregroundStyle(by: .value("Segment", pt.segmentID))
                .lineStyle(StrokeStyle(lineWidth: 2))
            }

            ForEach(plot.markerPoints) { pt in
                PointMark(
                    x: .value(xLabel, pt.midpoint),
                    y: .value(yLabel, pt.usd)
                )
                .foregroundStyle(by: .value("Segment", pt.segmentID))
                .symbol(by: .value("Segment", pt.segmentID))
                .symbolSize(28)
            }

            if let hoveredIdx = hoveredBucketIndex,
               let slot = validSlots.first(where: { $0.bucketIndex == hoveredIdx }) {
                RuleMark(x: .value(xLabel, slot.midpoint))
                    .foregroundStyle(palette.secondaryTextColor.opacity(0.40))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
            }
        }
        .chartForegroundStyleScale(domain: plot.segmentDomain, range: plot.segmentColors)
        .chartSymbolScale(domain: plot.segmentDomain, range: plot.segmentSymbols)
        .chartLegend(.hidden)
        .chartXScale(domain: xDomain)
        .chartYScale(domain: 0...plot.yUpperBound)
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 4)) { value in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                    .foregroundStyle(palette.secondaryTextColor.opacity(palette.isDark ? 0.18 : 0.14))
                AxisTick(stroke: StrokeStyle(lineWidth: 0.5))
                    .foregroundStyle(palette.secondaryTextColor.opacity(0.35))
                AxisValueLabel {
                    if let date = value.as(Date.self) {
                        Text(
                            TrendFormatting.axisDateLabel(
                                date,
                                domain: xDomain,
                                bucketSeconds: bucketSeconds,
                                calendar: calendar
                            )
                        )
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(palette.secondaryTextColor)
                        .lineLimit(1)
                    }
                }
            }
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                    .foregroundStyle(palette.secondaryTextColor.opacity(palette.isDark ? 0.18 : 0.14))
                AxisTick(stroke: StrokeStyle(lineWidth: 0.5))
                    .foregroundStyle(palette.secondaryTextColor.opacity(0.35))
                AxisValueLabel {
                    if let usd = value.as(Double.self) {
                        Text(ReferencePricing.formatUSD(usd))
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(palette.secondaryTextColor)
                            .lineLimit(1)
                    }
                }
            }
        }
        .chartOverlay { proxy in
            ChartHoverOverlayView(
                proxy: proxy,
                validSlots: validSlots,
                hoveredBucketIndex: $hoveredBucketIndex
            )
        }
        .frame(height: chartHeight)
        .accessibilityLabel(Text(loc.L("Top chats by Codex usage chart", "Codex 用量前三图表")))
        .accessibilityValue(Text(plot.accessibilitySummary))
    }
}

private struct TopChatsLegendView: View {
    let seriesList: [ChatSeriesInfo]
    let hoveredSlot: ParsedBucketSlot?
    let loc: TokenPilotLocalizer
    let palette: ResolvedThemePalette

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: 10) {
                ForEach(seriesList) { item in
                    ChatLegendItemView(
                        item: item,
                        hoveredSlot: hoveredSlot,
                        loc: loc,
                        palette: palette
                    )
                }
                Spacer(minLength: 0)
            }

            VStack(alignment: .leading, spacing: 3) {
                ForEach(seriesList) { item in
                    ChatLegendItemView(
                        item: item,
                        hoveredSlot: hoveredSlot,
                        loc: loc,
                        palette: palette
                    )
                }
            }
        }
    }
}

private struct ChatLegendItemView: View {
    let item: ChatSeriesInfo
    let hoveredSlot: ParsedBucketSlot?
    let loc: TokenPilotLocalizer
    let palette: ResolvedThemePalette

    private var valueText: String {
        if let slot = hoveredSlot {
            let bucketValue = slot.chatUSD[item.chatID] ?? nil
            return ReferencePricing.formatUSD(bucketValue)
        }
        if item.hasKnownPoints {
            return ReferencePricing.formatUSD(item.knownWindowUSD)
        }
        return ReferencePricing.formatUSD(nil)
    }

    var body: some View {
        HStack(spacing: 5) {
            SeriesMarkerSwatchView(color: item.color, symbol: item.symbolKind)

            Text(item.displayLabel)
                .font(.caption2.weight(.medium))
                .foregroundStyle(palette.readablePrimaryTextColor)
                .lineLimit(1)
                .truncationMode(.tail)

            Text(valueText)
                .font(.caption2.monospacedDigit())
                .foregroundStyle(palette.secondaryTextColor)
                .lineLimit(1)
        }
        .help(item.helpText)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(item.accessibilityLabel))
        .accessibilityValue(
            Text(
                hoveredSlot != nil
                    ? "\(loc.L("Selected interval", "当前时段")) \(valueText)"
                    : "\(loc.L("Window total", "窗口合计")) \(valueText)"
            )
        )
    }
}

private struct SeriesMarkerSwatchView: View {
    let color: Color
    let symbol: SeriesSymbolKind

    var body: some View {
        ZStack {
            Capsule(style: .continuous)
                .fill(color.opacity(0.85))
                .frame(width: 14, height: 2)

            switch symbol {
            case .circle:
                Circle()
                    .fill(color)
                    .frame(width: 6.5, height: 6.5)
            case .diamond:
                DiamondMarkerShape()
                    .fill(color)
                    .frame(width: 7.5, height: 7.5)
            case .triangle:
                TriangleMarkerShape()
                    .fill(color)
                    .frame(width: 7.5, height: 7)
            }
        }
        .frame(width: 15, height: 9)
        .accessibilityHidden(true)
    }
}

private struct DiamondMarkerShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.midX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        path.addLine(to: CGPoint(x: rect.midX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.midY))
        path.closeSubpath()
        return path
    }
}

private struct TriangleMarkerShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.midX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.closeSubpath()
        return path
    }
}

private struct ChartHoverOverlayView: View {
    let proxy: ChartProxy
    let validSlots: [ParsedBucketSlot]
    @Binding var hoveredBucketIndex: Int?

    var body: some View {
        GeometryReader { geo in
            Color.clear
                .contentShape(Rectangle())
                .onContinuousHover { phase in
                    switch phase {
                    case .active(let location):
                        guard let plotAnchor = proxy.plotFrame else {
                            hoveredBucketIndex = nil
                            return
                        }
                        let plotRect = geo[plotAnchor]
                        let relativeX = location.x - plotRect.origin.x
                        guard relativeX >= 0,
                              relativeX <= plotRect.width,
                              let hoveredDate = proxy.value(atX: relativeX, as: Date.self) else {
                            hoveredBucketIndex = nil
                            return
                        }
                        hoveredBucketIndex = nearestSlotIndex(for: hoveredDate)
                    case .ended:
                        hoveredBucketIndex = nil
                    }
                }
        }
    }

    private func nearestSlotIndex(for date: Date) -> Int? {
        if let containing = validSlots.first(where: { date >= $0.start && date <= $0.end }) {
            return containing.bucketIndex
        }
        return validSlots.min(by: {
            abs($0.midpoint.timeIntervalSince(date)) < abs($1.midpoint.timeIntervalSince(date))
        })?.bucketIndex
    }
}

private struct TrendEmptyStateCardView: View {
    let message: String
    let palette: ResolvedThemePalette

    var body: some View {
        HStack(spacing: 8) {
            Text(message)
                .font(.caption)
                .foregroundStyle(palette.secondaryTextColor)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 16)
        .frame(maxWidth: .infinity)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(palette.insetColor)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(
                    palette.secondaryTextColor.opacity(palette.isDark ? 0.22 : 0.16),
                    lineWidth: 0.75
                )
        )
    }
}

private struct TrendPlotEmptyPlaceholderView: View {
    let message: String
    let height: CGFloat
    let palette: ResolvedThemePalette

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .fill(palette.secondaryTextColor.opacity(palette.isDark ? 0.07 : 0.05))
            Text(message)
                .font(.caption)
                .foregroundStyle(palette.secondaryTextColor)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 12)
        }
        .frame(maxWidth: .infinity)
        .frame(height: height)
    }
}

// MARK: - Prepared Data & Series Segment Builders

private enum SeriesSymbolKind: CaseIterable, Equatable {
    case circle
    case diamond
    case triangle

    var chartSymbol: BasicChartSymbolShape {
        switch self {
        case .circle:
            return .circle
        case .diamond:
            return .diamond
        case .triangle:
            return .triangle
        }
    }

    func localizedName(_ loc: TokenPilotLocalizer) -> String {
        switch self {
        case .circle:
            return loc.L("circle marker", "圆形标记")
        case .diamond:
            return loc.L("diamond marker", "菱形标记")
        case .triangle:
            return loc.L("triangle marker", "三角形标记")
        }
    }
}

private struct TrendPlotPoint: Identifiable, Equatable {
    let id: String
    let segmentID: String
    let midpoint: Date
    let usd: Double
}

private struct ParsedBucketSlot: Equatable {
    let bucketIndex: Int
    let start: Date
    let end: Date
    let midpoint: Date
    let totalUSD: Double?
    let chatUSD: [String: Double?]
}

private struct SingleSeriesPlotModel {
    let color: Color
    let linePoints: [TrendPlotPoint]
    let markerPoints: [TrendPlotPoint]
    let segmentDomain: [String]
    let segmentColors: [Color]
    let yUpperBound: Double
    let knownSumUSD: Double
    let peakUSD: Double

    var hasData: Bool {
        !markerPoints.isEmpty
    }
}

private struct ChatSeriesInfo: Identifiable, Equatable {
    let id: String
    let chatID: String
    let rank: Int
    let displayLabel: String
    let fullTitleOrID: String
    let helpText: String
    let accessibilityLabel: String
    let color: Color
    let symbolKind: SeriesSymbolKind
    let codexUSD: Double
    let knownWindowUSD: Double
    let hasKnownPoints: Bool
}

private struct MultiSeriesPlotModel {
    let seriesList: [ChatSeriesInfo]
    let linePoints: [TrendPlotPoint]
    let markerPoints: [TrendPlotPoint]
    let segmentDomain: [String]
    let segmentColors: [Color]
    let segmentSymbols: [BasicChartSymbolShape]
    let yUpperBound: Double
    let accessibilitySummary: String

    var hasSeries: Bool {
        !seriesList.isEmpty
    }

    var hasPlottedPoints: Bool {
        !markerPoints.isEmpty
    }
}

private struct PreparedWindowModel {
    let bucketSeconds: Int
    let xDomain: ClosedRange<Date>
    let validSlots: [ParsedBucketSlot]
    let bucketsByIndex: [Int: ParsedBucketSlot]
    let totalPlot: SingleSeriesPlotModel
    let topChatsPlot: MultiSeriesPlotModel
}

private struct PreparedTrendData {
    let bucketWidthLabel: String
    let windowRangeLabel: String?
    let window: PreparedWindowModel?

    static func build(
        trend: ChatUsageTrend,
        isDark: Bool,
        calendar: Calendar,
        loc: TokenPilotLocalizer
    ) -> PreparedTrendData {
        let bucketWidthLabel = TrendFormatting.bucketWidthLabel(
            bucketSeconds: trend.bucket_seconds,
            loc: loc
        )

        var validSlots: [ParsedBucketSlot] = []
        validSlots.reserveCapacity(trend.buckets.count)

        for (idx, bucket) in trend.buckets.enumerated() {
            guard let start = ChatUsageDateParser.parse(bucket.start_at),
                  let end = ChatUsageDateParser.parse(bucket.end_at),
                  end >= start else {
                continue
            }
            let midpoint = start.addingTimeInterval(end.timeIntervalSince(start) * 0.5)
            validSlots.append(
                ParsedBucketSlot(
                    bucketIndex: idx,
                    start: start,
                    end: end,
                    midpoint: midpoint,
                    totalUSD: sanitizeUSD(bucket.total_usd),
                    chatUSD: bucket.chat_usd.mapValues(sanitizeUSD)
                )
            )
        }

        guard !validSlots.isEmpty,
              let minStart = validSlots.map(\.start).min(),
              let maxEnd = validSlots.map(\.end).max() else {
            return PreparedTrendData(
                bucketWidthLabel: bucketWidthLabel,
                windowRangeLabel: nil,
                window: nil
            )
        }

        let xDomain = makeSafeXDomain(
            minStart: minStart,
            maxEnd: maxEnd,
            bucketSeconds: trend.bucket_seconds
        )
        let rangeLabel = TrendFormatting.windowRangeLabel(
            start: minStart,
            end: maxEnd,
            calendar: calendar,
            loc: loc
        )

        let gapToleranceSeconds = max(5.0, Double(max(trend.bucket_seconds, 1)) * 0.05)

        let totalColor = isDark
            ? Color(red: 0.38, green: 0.68, blue: 1.00)
            : Color(red: 0.08, green: 0.40, blue: 0.85)
        let totalPlot = buildSingleSeriesPlot(
            seriesPrefix: "total",
            color: totalColor,
            validSlots: validSlots,
            gapTolerance: gapToleranceSeconds,
            valueAt: { $0.totalUSD }
        )

        let topChatsPlot = buildTopChatsPlot(
            topChats: Array(trend.top_chats.prefix(3)),
            validSlots: validSlots,
            gapTolerance: gapToleranceSeconds,
            isDark: isDark,
            loc: loc
        )

        var slotsByIndex: [Int: ParsedBucketSlot] = [:]
        slotsByIndex.reserveCapacity(validSlots.count)
        for slot in validSlots {
            slotsByIndex[slot.bucketIndex] = slot
        }

        let window = PreparedWindowModel(
            bucketSeconds: trend.bucket_seconds,
            xDomain: xDomain,
            validSlots: validSlots,
            bucketsByIndex: slotsByIndex,
            totalPlot: totalPlot,
            topChatsPlot: topChatsPlot
        )

        return PreparedTrendData(
            bucketWidthLabel: bucketWidthLabel,
            windowRangeLabel: rangeLabel,
            window: window
        )
    }

    private static func sanitizeUSD(_ raw: Double?) -> Double? {
        guard let value = raw, value.isFinite, value >= 0 else {
            return nil
        }
        return value == 0 ? 0.0 : value
    }

    private static func makeSafeXDomain(
        minStart: Date,
        maxEnd: Date,
        bucketSeconds: Int
    ) -> ClosedRange<Date> {
        let rawSpan = maxEnd.timeIntervalSince(minStart)
        if rawSpan >= 60 {
            return minStart...maxEnd
        }
        let midpoint = minStart.addingTimeInterval(max(rawSpan, 0) * 0.5)
        let halfSpan = max(Double(max(bucketSeconds, 120)) * 0.5, 300.0)
        return midpoint.addingTimeInterval(-halfSpan)...midpoint.addingTimeInterval(halfSpan)
    }

    private static func buildSingleSeriesPlot(
        seriesPrefix: String,
        color: Color,
        validSlots: [ParsedBucketSlot],
        gapTolerance: TimeInterval,
        valueAt: (ParsedBucketSlot) -> Double?
    ) -> SingleSeriesPlotModel {
        let runs = extractContiguousRuns(
            validSlots: validSlots,
            gapTolerance: gapTolerance,
            valueAt: valueAt
        )

        var linePoints: [TrendPlotPoint] = []
        var markerPoints: [TrendPlotPoint] = []
        var segmentDomain: [String] = []
        var segmentColors: [Color] = []
        var knownSum = 0.0
        var peak = 0.0

        for (segIdx, run) in runs.enumerated() {
            let segID = "\(seriesPrefix)#\(segIdx)"
            segmentDomain.append(segID)
            segmentColors.append(color)

            let points = run.map { entry in
                knownSum += entry.usd
                if entry.usd > peak {
                    peak = entry.usd
                }
                return TrendPlotPoint(
                    id: "\(segID):\(entry.slot.bucketIndex)",
                    segmentID: segID,
                    midpoint: entry.slot.midpoint,
                    usd: entry.usd
                )
            }
            markerPoints.append(contentsOf: points)
            if points.count >= 2 {
                linePoints.append(contentsOf: points)
            }
        }

        return SingleSeriesPlotModel(
            color: color,
            linePoints: linePoints,
            markerPoints: markerPoints,
            segmentDomain: segmentDomain,
            segmentColors: segmentColors,
            yUpperBound: niceYUpperBound(peak),
            knownSumUSD: knownSum,
            peakUSD: peak
        )
    }

    private static func buildTopChatsPlot(
        topChats: [ChatUsageTrend.TopChat],
        validSlots: [ParsedBucketSlot],
        gapTolerance: TimeInterval,
        isDark: Bool,
        loc: TokenPilotLocalizer
    ) -> MultiSeriesPlotModel {
        let colors: [Color] = isDark
            ? [
                Color(red: 0.35, green: 0.72, blue: 1.00),
                Color(red: 0.24, green: 0.84, blue: 0.62),
                Color(red: 0.88, green: 0.52, blue: 0.98)
            ]
            : [
                Color(red: 0.06, green: 0.42, blue: 0.86),
                Color(red: 0.05, green: 0.56, blue: 0.40),
                Color(red: 0.62, green: 0.20, blue: 0.76)
            ]
        let symbols = SeriesSymbolKind.allCases

        let shortIDs = makeDisambiguatedShortIDs(for: topChats.map(\.id))

        var seriesList: [ChatSeriesInfo] = []
        var linePoints: [TrendPlotPoint] = []
        var markerPoints: [TrendPlotPoint] = []
        var segmentDomain: [String] = []
        var segmentColors: [Color] = []
        var segmentSymbols: [BasicChartSymbolShape] = []
        var peakUSD = 0.0

        for (seriesIdx, chat) in topChats.enumerated() {
            let color = colors[seriesIdx % colors.count]
            let symbolKind = symbols[seriesIdx % symbols.count]
            let seriesKey = "s\(seriesIdx)"

            let runs = extractContiguousRuns(
                validSlots: validSlots,
                gapTolerance: gapTolerance,
                valueAt: { slot in
                    slot.chatUSD[chat.id] ?? nil
                }
            )

            var chatKnownSum = 0.0
            var chatPointCount = 0

            for (segIdx, run) in runs.enumerated() {
                let segID = "\(seriesKey)#\(segIdx)"
                segmentDomain.append(segID)
                segmentColors.append(color)
                segmentSymbols.append(symbolKind.chartSymbol)

                let points = run.map { entry in
                    chatKnownSum += entry.usd
                    chatPointCount += 1
                    if entry.usd > peakUSD {
                        peakUSD = entry.usd
                    }
                    return TrendPlotPoint(
                        id: "\(segID):\(entry.slot.bucketIndex)",
                        segmentID: segID,
                        midpoint: entry.slot.midpoint,
                        usd: entry.usd
                    )
                }
                markerPoints.append(contentsOf: points)
                if points.count >= 2 {
                    linePoints.append(contentsOf: points)
                }
            }

            let normalizedTitle = normalizeTitle(chat.title)
            let shortID = shortIDs[seriesIdx]
            let displayLabel = normalizedTitle ?? shortID
            let fullIdentity = normalizedTitle ?? chat.id

            let codexPart = "\(loc.L("Codex ref.", "Codex 参考")) \(ReferencePricing.formatUSD(sanitizeUSD(chat.codex_usd)))"
            let windowPart = chatPointCount > 0
                ? "\(loc.L("Window total", "窗口合计")) \(ReferencePricing.formatUSD(chatKnownSum))"
                : loc.L("No known interval values", "暂无已知分桶值")

            let helpText: String
            if let normalizedTitle {
                helpText = "\(normalizedTitle)\nID: \(chat.id)\n\(codexPart) · \(windowPart)"
            } else {
                helpText = "ID: \(chat.id)\n\(codexPart) · \(windowPart)"
            }

            let axLabel = loc.L(
                "Rank \(seriesIdx + 1) (\(symbolKind.localizedName(loc))): \(fullIdentity), \(codexPart)",
                "第 \(seriesIdx + 1) 名（\(symbolKind.localizedName(loc))）：\(fullIdentity)，\(codexPart)"
            )

            seriesList.append(
                ChatSeriesInfo(
                    id: seriesKey,
                    chatID: chat.id,
                    rank: seriesIdx + 1,
                    displayLabel: displayLabel,
                    fullTitleOrID: fullIdentity,
                    helpText: helpText,
                    accessibilityLabel: axLabel,
                    color: color,
                    symbolKind: symbolKind,
                    codexUSD: chat.codex_usd,
                    knownWindowUSD: chatKnownSum,
                    hasKnownPoints: chatPointCount > 0
                )
            )
        }

        let summaryParts = seriesList.map { info in
            let amountText = info.hasKnownPoints
                ? ReferencePricing.formatUSD(info.knownWindowUSD)
                : ReferencePricing.formatUSD(nil)
            return "#\(info.rank) \(info.fullTitleOrID): \(amountText)"
        }

        return MultiSeriesPlotModel(
            seriesList: seriesList,
            linePoints: linePoints,
            markerPoints: markerPoints,
            segmentDomain: segmentDomain,
            segmentColors: segmentColors,
            segmentSymbols: segmentSymbols,
            yUpperBound: niceYUpperBound(peakUSD),
            accessibilitySummary: summaryParts.joined(separator: "; ")
        )
    }

    private struct SlotValuePair {
        let slot: ParsedBucketSlot
        let usd: Double
    }

    private static func extractContiguousRuns(
        validSlots: [ParsedBucketSlot],
        gapTolerance: TimeInterval,
        valueAt: (ParsedBucketSlot) -> Double?
    ) -> [[SlotValuePair]] {
        var runs: [[SlotValuePair]] = []
        var currentRun: [SlotValuePair] = []

        for slot in validSlots {
            guard let usd = valueAt(slot) else {
                if !currentRun.isEmpty {
                    runs.append(currentRun)
                    currentRun.removeAll(keepingCapacity: true)
                }
                continue
            }

            if let prev = currentRun.last {
                let isAdjacentIndex = slot.bucketIndex == prev.slot.bucketIndex + 1
                let isContiguousInTime = abs(slot.start.timeIntervalSince(prev.slot.end)) <= gapTolerance
                if !isAdjacentIndex || !isContiguousInTime {
                    runs.append(currentRun)
                    currentRun.removeAll(keepingCapacity: true)
                }
            }

            currentRun.append(SlotValuePair(slot: slot, usd: usd))
        }

        if !currentRun.isEmpty {
            runs.append(currentRun)
        }

        return runs
    }

    private static func normalizeTitle(_ raw: String?) -> String? {
        guard let raw else { return nil }
        let collapsed = raw
            .components(separatedBy: .whitespacesAndNewlines)
            .filter { !$0.isEmpty }
            .joined(separator: " ")
        return collapsed.isEmpty ? nil : collapsed
    }

    private static func makeDisambiguatedShortIDs(for ids: [String]) -> [String] {
        let baseShort = ids.map(baseShortID)
        var counts: [String: Int] = [:]
        for short in baseShort {
            counts[short, default: 0] += 1
        }
        return zip(ids, baseShort).map { fullID, short in
            guard (counts[short] ?? 0) > 1 else { return short }
            let trimmed = fullID.trimmingCharacters(in: .whitespacesAndNewlines)
            guard trimmed.count > 9 else { return trimmed }
            return "\(trimmed.prefix(4))…\(trimmed.suffix(4))"
        }
    }

    private static func baseShortID(_ rawID: String) -> String {
        let trimmed = rawID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "?" }
        let core: Substring
        if let colon = trimmed.lastIndex(of: ":"), trimmed.index(after: colon) < trimmed.endIndex {
            core = trimmed[trimmed.index(after: colon)...]
        } else {
            core = trimmed[...]
        }
        if core.count <= 8 {
            return String(core)
        }
        return String(core.prefix(8))
    }

    static func niceYUpperBound(_ maxVal: Double) -> Double {
        guard maxVal.isFinite, maxVal > 0 else { return 0.04 }
        let padded = maxVal * 1.12
        let exponent = floor(log10(padded))
        let magnitude = pow(10.0, exponent)
        guard magnitude.isFinite, magnitude > 0 else { return max(padded, 0.04) }
        let normalized = padded / magnitude
        let niceNorm: Double
        if normalized <= 1.2 {
            niceNorm = 1.2
        } else if normalized <= 1.5 {
            niceNorm = 1.5
        } else if normalized <= 2.0 {
            niceNorm = 2.0
        } else if normalized <= 2.5 {
            niceNorm = 2.5
        } else if normalized <= 3.0 {
            niceNorm = 3.0
        } else if normalized <= 4.0 {
            niceNorm = 4.0
        } else if normalized <= 5.0 {
            niceNorm = 5.0
        } else if normalized <= 6.0 {
            niceNorm = 6.0
        } else if normalized <= 8.0 {
            niceNorm = 8.0
        } else {
            niceNorm = 10.0
        }
        let upper = niceNorm * magnitude
        return upper.isFinite && upper > 0 ? upper : 0.04
    }
}

// MARK: - Localized Time & Interval Formatting

private enum TrendFormatting {
    static func bucketWidthLabel(bucketSeconds: Int, loc: TokenPilotLocalizer) -> String {
        switch bucketSeconds {
        case 3600:
            return loc.L("Hourly", "每小时")
        case 21600:
            return loc.L("Every 6 hours", "每 6 小时")
        case 86400:
            return loc.L("Daily", "每天")
        default:
            if bucketSeconds <= 0 {
                return loc.L("Custom interval", "自定义间隔")
            } else if bucketSeconds % 86400 == 0 {
                let days = bucketSeconds / 86400
                return loc.L("Every \(days) days", "每 \(days) 天")
            } else if bucketSeconds % 3600 == 0 {
                let hours = bucketSeconds / 3600
                return loc.L("Every \(hours) hours", "每 \(hours) 小时")
            } else if bucketSeconds % 60 == 0 {
                let mins = bucketSeconds / 60
                return loc.L("Every \(mins) min", "每 \(mins) 分钟")
            } else {
                return loc.L("Every \(bucketSeconds)s", "每 \(bucketSeconds) 秒")
            }
        }
    }

    static func axisDateLabel(
        _ date: Date,
        domain: ClosedRange<Date>,
        bucketSeconds: Int,
        calendar: Calendar
    ) -> String {
        let comps = calendar.dateComponents([.month, .day, .hour, .minute], from: date)
        let month = comps.month ?? 1
        let day = comps.day ?? 1
        let hour = comps.hour ?? 0
        let minute = comps.minute ?? 0

        let span = domain.upperBound.timeIntervalSince(domain.lowerBound)
        if span >= 3 * 86400 && (bucketSeconds >= 86400 || (hour == 0 && minute == 0)) {
            return "\(month)/\(day)"
        }

        let hhmm = String(format: "%02d:%02d", hour, minute)
        if hour == 0 && minute == 0 && !calendar.isDate(domain.lowerBound, inSameDayAs: domain.upperBound) {
            return "\(month)/\(day)"
        }
        return hhmm
    }

    static func windowRangeLabel(
        start: Date,
        end: Date,
        calendar: Calendar,
        loc: TokenPilotLocalizer
    ) -> String {
        let s = calendar.dateComponents([.month, .day, .hour, .minute], from: start)
        let e = calendar.dateComponents([.month, .day, .hour, .minute], from: end)
        let sMD = monthDayString(month: s.month ?? 1, day: s.day ?? 1, loc: loc)
        let sHM = String(format: "%02d:%02d", s.hour ?? 0, s.minute ?? 0)
        let eHM = String(format: "%02d:%02d", e.hour ?? 0, e.minute ?? 0)
        let tzAbbrev = calendar.timeZone.abbreviation(for: end) ?? calendar.timeZone.identifier

        if start == end {
            return "\(sMD) \(sHM) (\(tzAbbrev))"
        }
        if calendar.isDate(start, inSameDayAs: end) {
            return "\(sMD) \(sHM)–\(eHM) (\(tzAbbrev))"
        }
        let eMD = monthDayString(month: e.month ?? 1, day: e.day ?? 1, loc: loc)
        return "\(sMD) \(sHM) – \(eMD) \(eHM) (\(tzAbbrev))"
    }

    static func bucketSpanLabel(
        _ slot: ParsedBucketSlot,
        calendar: Calendar,
        loc: TokenPilotLocalizer
    ) -> String {
        let s = calendar.dateComponents([.month, .day, .hour, .minute], from: slot.start)
        let e = calendar.dateComponents([.month, .day, .hour, .minute], from: slot.end)
        let sMD = monthDayString(month: s.month ?? 1, day: s.day ?? 1, loc: loc)
        let sHM = String(format: "%02d:%02d", s.hour ?? 0, s.minute ?? 0)
        let eHM = String(format: "%02d:%02d", e.hour ?? 0, e.minute ?? 0)
        if slot.start == slot.end {
            return "\(sMD) \(sHM)"
        }
        if calendar.isDate(slot.start, inSameDayAs: slot.end) {
            return "\(sMD) \(sHM)–\(eHM)"
        }
        let eMD = monthDayString(month: e.month ?? 1, day: e.day ?? 1, loc: loc)
        return "\(sMD) \(sHM)–\(eMD) \(eHM)"
    }

    private static func monthDayString(month: Int, day: Int, loc: TokenPilotLocalizer) -> String {
        let enMonths = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
        ]
        let enMonth = (1...12).contains(month) ? enMonths[month - 1] : "\(month)"
        return loc.L("\(enMonth) \(day)", "\(month)月\(day)日")
    }
}
