import Foundation
import UserNotifications

@MainActor
protocol WeeklyResetNotificationQueue {
    func pendingReminder() async -> UNNotificationRequest?
    func allowsScheduling() async -> Bool
    func add(_ request: UNNotificationRequest) async throws
    func removeReminder()
}

@MainActor
private struct SystemWeeklyResetNotificationQueue: WeeklyResetNotificationQueue {
    private var center: UNUserNotificationCenter { .current() }

    func pendingReminder() async -> UNNotificationRequest? {
        await center.pendingNotificationRequests().first { $0.identifier == WeeklyResetReminder.identifier }
    }

    func allowsScheduling() async -> Bool {
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional:
            return true
        case .notDetermined:
            return (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
        default:
            return false
        }
    }

    func add(_ request: UNNotificationRequest) async throws {
        try await center.add(request)
    }

    func removeReminder() {
        center.removePendingNotificationRequests(withIdentifiers: [WeeklyResetReminder.identifier])
    }
}

@MainActor
final class WeeklyResetReminder {
    // The same identifier also replaces a reminder queued by the earlier one-time scheduler.
    static let identifier = "local.routingfloat.viewer.weekly-reset-reminder"
    private static let resetTimeKey = "weeklyResetAt"

    private struct Plan: Equatable {
        private static let advance: TimeInterval = 86_400
        let resetAt: Date
        let chinese: Bool

        var notifyAt: Date { resetAt.addingTimeInterval(-Self.advance) }
    }

    private let queue: any WeeklyResetNotificationQueue
    private let now: () -> Date
    private let fractionalISO = ISO8601DateFormatter()
    private let plainISO = ISO8601DateFormatter()
    private var lastFetchedAt: Date?
    private var queued: Plan?
    private var running: Task<Void, Never>?

    init(queue: any WeeklyResetNotificationQueue, now: @escaping () -> Date = Date.init) {
        self.queue = queue
        self.now = now
        fractionalISO.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    }

    convenience init() {
        self.init(queue: SystemWeeklyResetNotificationQueue())
    }

    @discardableResult
    func observe(resetAt: Double?, fetchedAt: String?, chinese: Bool) -> Task<Void, Never>? {
        let current = now()
        guard let epoch = resetAt, epoch.isFinite,
              epoch > 0,
              let fetchedAt,
              let fetched = fractionalISO.date(from: fetchedAt) ?? plainISO.date(from: fetchedAt),
              (-10...120).contains(current.timeIntervalSince(fetched)),
              lastFetchedAt.map({ fetched > $0 }) ?? true else { return nil }

        lastFetchedAt = fetched
        queued = Plan(resetAt: Date(timeIntervalSince1970: epoch.rounded(.down)), chinese: chinese)
        if running == nil {
            running = Task { [weak self] in await self?.reconcileQueuedPlans() }
        }
        return running
    }

    private func reconcileQueuedPlans() async {
        defer { running = nil }
        while let plan = queued {
            queued = nil
            await reconcile(plan)
        }
    }

    private func reconcile(_ plan: Plan) async {
        let pending = await queue.pendingReminder()
        guard queued == nil || queued == plan else { return }

        // A new account deadline can move inside the advance window. Cancel the old alert.
        if plan.notifyAt <= now() {
            if pending != nil { queue.removeReminder() }
            return
        }
        if let pending,
           let trigger = pending.trigger as? UNCalendarNotificationTrigger,
           !trigger.repeats,
           trigger.nextTriggerDate() == plan.notifyAt,
           pending.content.sound != nil,
           pending.content.userInfo[Self.resetTimeKey] as? Double == plan.resetAt.timeIntervalSince1970,
           pending.content.title == title(for: plan) {
            return
        }

        let allowed = await queue.allowsScheduling()
        guard queued == nil || queued == plan else { return }
        if plan.notifyAt <= now() {
            if pending != nil { queue.removeReminder() }
            return
        }
        guard allowed else { return }

        do {
            // Using the same identifier updates the pending request as a single operation.
            try await queue.add(makeRequest(for: plan))
        } catch {
            // The next distinct fresh quota reading retries without accumulating requests.
            NSLog("TokenPilot could not schedule the weekly reset reminder: %@", error.localizedDescription)
        }
    }

    static func printStatus() async {
        let center = UNUserNotificationCenter.current()
        let queue = SystemWeeklyResetNotificationQueue()
        let pending = await queue.pendingReminder()
        let settings = await center.notificationSettings()
        var report: [String: Any] = [
            "pending": pending != nil,
            "sound_requested": pending?.content.sound != nil,
            "sounds_enabled": settings.soundSetting == .enabled,
        ]
        if let trigger = pending?.trigger as? UNCalendarNotificationTrigger,
           let fireDate = trigger.nextTriggerDate() {
            report["scheduled_at"] = ISO8601DateFormatter().string(from: fireDate)
        }
        if let resetEpoch = pending?.content.userInfo[Self.resetTimeKey] as? Double {
            report["reset_at"] = ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: resetEpoch))
        }
        if let data = try? JSONSerialization.data(withJSONObject: report, options: .sortedKeys) {
            print(String(decoding: data, as: UTF8.self))
        }
    }

    private func title(for plan: Plan) -> String {
        plan.chinese ? "Codex 周额度重置提醒" : "Codex weekly reset reminder"
    }

    private func makeRequest(for plan: Plan) -> UNNotificationRequest {
        let format = DateFormatter()
        format.locale = Locale(identifier: plan.chinese ? "zh_CN" : "en_US")
        format.timeZone = .current
        format.setLocalizedDateFormatFromTemplate("MMMdjm")
        let date = format.string(from: plan.resetAt)

        let content = UNMutableNotificationContent()
        content.title = title(for: plan)
        content.body = plan.chinese
            ? "周额度预计于\(date)重置，距现在约 24 小时。"
            : "Your weekly quota is expected to reset in about 24 hours, on \(date)."
        content.sound = .default
        content.threadIdentifier = "local.routingfloat.viewer.account-quota"
        content.userInfo = [Self.resetTimeKey: plan.resetAt.timeIntervalSince1970]

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .gmt
        var components = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second],
                                                 from: plan.notifyAt)
        components.calendar = calendar
        components.timeZone = .gmt
        let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
        return UNNotificationRequest(identifier: Self.identifier, content: content, trigger: trigger)
    }
}
