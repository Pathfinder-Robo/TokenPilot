import Foundation
import UserNotifications

@MainActor
private final class TestQueue: WeeklyResetNotificationQueue {
    var pending: UNNotificationRequest?
    var authorized = true
    var failNextAdd = false
    var pauseNextAdd = false
    var paused: CheckedContinuation<Void, Never>?
    var additions = 0
    var removals = 0

    func pendingReminder() async -> UNNotificationRequest? { pending }
    func allowsScheduling() async -> Bool { authorized }

    func add(_ request: UNNotificationRequest) async throws {
        additions += 1
        if pauseNextAdd {
            pauseNextAdd = false
            await withCheckedContinuation { paused = $0 }
        }
        if failNextAdd {
            failNextAdd = false
            throw NSError(domain: "TestQueue", code: 1)
        }
        pending = request
    }

    func removeReminder() {
        removals += 1
        pending = nil
    }

    var fireDate: Date? {
        (pending?.trigger as? UNCalendarNotificationTrigger)?.nextTriggerDate()
    }
}

@main
private struct WeeklyResetReminderChecks {
    @MainActor
    static func main() async {
        let base = Date(timeIntervalSince1970: Date().timeIntervalSince1970.rounded(.down))
        let day: TimeInterval = 86_400
        var time = base
        let iso = ISO8601DateFormatter()
        iso.formatOptions.insert(.withFractionalSeconds)
        let queue = TestQueue()
        var scheduler = WeeklyResetReminder(queue: queue, now: { time })

        @discardableResult
        func observe(_ offset: TimeInterval, at stamp: Date? = nil,
                     chinese: Bool = true) -> Task<Void, Never>? {
            let stamp = iso.string(from: stamp ?? time)
            return scheduler.observe(resetAt: base.timeIntervalSince1970 + offset, fetchedAt: stamp, chinese: chinese)
        }

        await observe(day * 6)?.value
        precondition(queue.pending?.identifier == WeeklyResetReminder.identifier)
        precondition(queue.pending?.content.sound != nil)
        precondition(queue.pending?.content.title == "Codex 周额度重置提醒")
        precondition(queue.fireDate == base.addingTimeInterval(day * 5))
        precondition((queue.pending?.trigger as? UNCalendarNotificationTrigger)?.repeats == false)

        precondition(observe(day * 6) == nil)
        time.addTimeInterval(60)
        await observe(day * 6)?.value
        precondition(queue.additions == 1 && queue.removals == 0)

        scheduler = WeeklyResetReminder(queue: queue, now: { time })
        await observe(day * 6)?.value
        precondition(queue.additions == 1)

        time.addTimeInterval(60)
        await observe(day * 7)?.value
        precondition(queue.additions == 2 && queue.removals == 0)
        precondition(queue.fireDate == base.addingTimeInterval(day * 6))
        precondition(observe(day * 4, at: time.addingTimeInterval(-1)) == nil)
        precondition(observe(day * 4, at: time.addingTimeInterval(-121)) == nil)
        precondition(scheduler.observe(resetAt: nil, fetchedAt: iso.string(from: time), chinese: true) == nil)
        precondition(queue.fireDate == base.addingTimeInterval(day * 6))

        time.addTimeInterval(60)
        queue.failNextAdd = true
        await observe(day * 4)?.value
        precondition(queue.fireDate == base.addingTimeInterval(day * 6) && queue.removals == 0)
        time.addTimeInterval(60)
        await observe(day * 4)?.value
        precondition(queue.fireDate == base.addingTimeInterval(day * 3))

        time.addTimeInterval(60)
        queue.pauseNextAdd = true
        let oldTask = observe(day * 5)
        for _ in 0..<1_000 {
            if queue.paused != nil { break }
            await Task.yield()
        }
        guard let paused = queue.paused else { fatalError("Old add did not suspend") }
        time.addTimeInterval(60)
        let newTask = observe(day * 3, chinese: false)
        queue.paused = nil
        paused.resume()
        await oldTask?.value
        await newTask?.value
        precondition(queue.fireDate == base.addingTimeInterval(day * 2))
        precondition(queue.pending?.content.title == "Codex weekly reset reminder")

        time.addTimeInterval(60)
        await observe(10 * 3_600)?.value
        precondition(queue.pending == nil && queue.removals == 1)
        time.addTimeInterval(60)
        let count = queue.additions
        await observe(10 * 3_600)?.value
        precondition(queue.additions == count)

        time.addTimeInterval(60)
        queue.authorized = false
        await observe(day * 5)?.value
        precondition(queue.pending == nil && queue.additions == count)
        time.addTimeInterval(60)
        queue.authorized = true
        await observe(day * 5)?.value
        precondition(queue.fireDate == base.addingTimeInterval(day * 4))
        time.addTimeInterval(60)
        await observe(-60)?.value
        precondition(queue.pending == nil && queue.removals == 2)
        print("Weekly reset scheduling, replacement, duplicate suppression, freshness and retry checks passed.")
    }
}
