import AppKit
import Foundation

// MARK: - Guided Installation Preference Inference & CLI Contract
//
// Root Integration Contract:
//   func handleInstallationPreferenceCommand(_ arguments: [String], appearance: AppearanceStore) -> Int?
//
// Testable Overload (no real UserDefaults / OS reads required):
//   func handleInstallationPreferenceCommand(
//       _ arguments: [String],
//       appearance: AppearanceStore,
//       userDefaults: UserDefaults?,
//       preferredLanguages: [String],
//       isDark: Bool?,
//       output: (String) -> Void
//   ) -> Int?
//
// Pure Inference Helper Signatures (zero OS/UserDefaults reads):
//   func inferInstallationPreferences(
//       savedSettings: PersistedAppearanceSettings?,
//       preferredLanguages: [String],
//       isDark: Bool?,
//       explicitLanguage: AppLanguage? = nil,
//       explicitTheme: ThemeID? = nil
//   ) -> InstallationPreferenceProposal
//
//   func inferInstallationPreferences(
//       savedDictionary: [String: Any]?,
//       preferredLanguages: [String],
//       isDark: Bool?,
//       explicitLanguage: AppLanguage? = nil,
//       explicitTheme: ThemeID? = nil
//   ) -> InstallationPreferenceProposal
//
//   func inferInstallationPreferences(
//       savedLanguage: String?,
//       savedTheme: String?,
//       preferredLanguages: [String],
//       isDark: Bool?,
//       explicitLanguage: AppLanguage? = nil,
//       explicitTheme: ThemeID? = nil
//   ) -> InstallationPreferenceProposal
//
//   func inferSystemLanguage(from preferredLanguages: [String]) -> AppLanguage?
//   func inferSystemTheme(isDark: Bool?) -> ThemeID?

enum PreferenceValueSource: String, Codable, Equatable, Sendable {
    case explicit = "explicit"
    case saved = "saved"
    case system = "system"
    case unresolved = "unresolved"
}

struct InstallationPreferenceOption: Codable, Equatable, Sendable {
    let value: String
    let label: String
    let summary: String
}

struct InstallationPreferenceQuestion: Codable, Equatable, Sendable {
    let field: String
    let flag: String
    let prompt: String
    let allowedValues: [String]
    let options: [InstallationPreferenceOption]

    enum CodingKeys: String, CodingKey {
        case field
        case flag
        case prompt
        case allowedValues = "allowed_values"
        case options
    }
}

struct InstallationPreferenceSources: Codable, Equatable, Sendable {
    let language: PreferenceValueSource
    let theme: PreferenceValueSource
}

struct InstallationPreferenceProposal: Codable, Equatable, Sendable {
    var status: String
    var ok: Bool
    var needsInput: Bool
    var language: String?
    var languageSource: PreferenceValueSource
    var theme: String?
    var themeSource: PreferenceValueSource
    var sources: InstallationPreferenceSources
    var unresolvedFields: [String]
    var questions: [InstallationPreferenceQuestion]
    var wrotePreferences: Bool?
    var customColors: [String: ThemeColorPair]?
    var bundleId: String = "local.routingfloat.viewer"
    var storageKey: String = AppearanceStore.storageKey
    var error: String?

    enum CodingKeys: String, CodingKey {
        case status
        case ok
        case needsInput = "needs_input"
        case language
        case languageSource = "language_source"
        case theme
        case themeSource = "theme_source"
        case sources
        case unresolvedFields = "unresolved_fields"
        case questions
        case wrotePreferences = "wrote_preferences"
        case customColors = "custom_colors"
        case bundleId = "bundle_id"
        case storageKey = "storage_key"
        case error
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(status, forKey: .status)
        try container.encode(ok, forKey: .ok)
        try container.encode(needsInput, forKey: .needsInput)
        try container.encode(language, forKey: .language)
        try container.encode(languageSource, forKey: .languageSource)
        try container.encode(theme, forKey: .theme)
        try container.encode(themeSource, forKey: .themeSource)
        try container.encode(sources, forKey: .sources)
        try container.encode(unresolvedFields, forKey: .unresolvedFields)
        try container.encode(questions, forKey: .questions)
        try container.encodeIfPresent(wrotePreferences, forKey: .wrotePreferences)
        try container.encodeIfPresent(customColors, forKey: .customColors)
        try container.encode(bundleId, forKey: .bundleId)
        try container.encode(storageKey, forKey: .storageKey)
        try container.encodeIfPresent(error, forKey: .error)
    }
}

// MARK: - Pure Inference Logic (No OS or UserDefaults Reads)

enum InstallationPreferenceInference {
    static func parseExplicitLanguage(_ raw: String?) -> AppLanguage? {
        guard let cleaned = raw?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
              !cleaned.isEmpty else {
            return nil
        }
        switch cleaned {
        case "en":
            return .english
        case "zh-hans":
            return .chinese
        default:
            return nil
        }
    }

    static func parseExplicitTheme(_ raw: String?) -> ThemeID? {
        guard let cleaned = raw?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
              !cleaned.isEmpty else {
            return nil
        }
        return ThemeID(rawValue: cleaned)
    }

    static func parseSupportedLanguageTag(_ raw: String?) -> AppLanguage? {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty else {
            return nil
        }
        let normalized = trimmed.replacingOccurrences(of: "_", with: "-").lowercased()
        let parts = normalized.split(separator: "-", omittingEmptySubsequences: false).map(String.init)
        guard let primary = parts.first, !primary.isEmpty, !parts.contains("") else {
            return nil
        }

        if primary == "en" {
            return .english
        }

        if primary == "zh" {
            if parts.count == 1 {
                return .chinese
            }
            // Never silently map Traditional Chinese tags to Simplified Chinese.
            if parts.contains("hant") { return nil }
            if parts.count > 1 && parts[1] == "hans" { return .chinese }
            if parts.contains("tw") || parts.contains("hk") || parts.contains("mo") {
                return nil
            }
            let second = parts[1]
            if second == "hans" || second == "cn" || second == "sg" {
                return .chinese
            }
            return nil
        }

        return nil
    }

    static func parseSavedLanguage(_ raw: String?) -> AppLanguage? {
        if let exact = parseExplicitLanguage(raw) {
            return exact
        }
        return parseSupportedLanguageTag(raw)
    }

    static func parseSavedTheme(_ raw: String?) -> ThemeID? {
        guard let cleaned = raw?.trimmingCharacters(in: .whitespacesAndNewlines),
              !cleaned.isEmpty else {
            return nil
        }
        if let exact = ThemeID(rawValue: cleaned.lowercased()) {
            return exact
        }
        return ThemeID.resolve(cleaned)
    }

    static func inferSystemLanguage(from preferredLanguages: [String]) -> AppLanguage? {
        for tag in preferredLanguages {
            if let supported = parseSupportedLanguageTag(tag) {
                return supported
            }
        }
        return nil
    }

    static func inferSystemTheme(isDark: Bool?) -> ThemeID? {
        guard let isDark else {
            return nil
        }
        return isDark ? .graphite : .cupertino
    }

    static func languageQuestion() -> InstallationPreferenceQuestion {
        InstallationPreferenceQuestion(
            field: "language",
            flag: "--language",
            prompt: "Choose interface language / 请选择界面语言 (en, zh-Hans)",
            allowedValues: AppLanguage.allCases.map(\.rawValue),
            options: [
                InstallationPreferenceOption(
                    value: AppLanguage.english.rawValue,
                    label: "English",
                    summary: "English interface"
                ),
                InstallationPreferenceOption(
                    value: AppLanguage.chinese.rawValue,
                    label: "简体中文",
                    summary: "Simplified Chinese interface / 简体中文界面"
                ),
            ]
        )
    }

    static func themeQuestion() -> InstallationPreferenceQuestion {
        InstallationPreferenceQuestion(
            field: "theme",
            flag: "--theme",
            prompt: "Choose appearance theme / 请选择外观主题 (cupertino, graphite, instrument, frosted, liquid, vibrant)",
            allowedValues: ThemeID.allCases.map(\.rawValue),
            options: ThemeID.allCases.map { item in
                InstallationPreferenceOption(
                    value: item.rawValue,
                    label: "\(item.displayName(in: .english)) / \(item.displayName(in: .chinese))",
                    summary: item.summary(in: .english)
                )
            }
        )
    }

    static func infer(
        savedLanguage: String?,
        savedTheme: String?,
        preferredLanguages: [String],
        isDark: Bool?,
        explicitLanguage: AppLanguage? = nil,
        explicitTheme: ThemeID? = nil
    ) -> InstallationPreferenceProposal {
        let resolvedLanguage: AppLanguage?
        let languageSource: PreferenceValueSource
        if let explicitLanguage {
            resolvedLanguage = explicitLanguage
            languageSource = .explicit
        } else if let saved = parseSavedLanguage(savedLanguage) {
            resolvedLanguage = saved
            languageSource = .saved
        } else if let system = inferSystemLanguage(from: preferredLanguages) {
            resolvedLanguage = system
            languageSource = .system
        } else {
            resolvedLanguage = nil
            languageSource = .unresolved
        }

        let resolvedTheme: ThemeID?
        let themeSource: PreferenceValueSource
        if let explicitTheme {
            resolvedTheme = explicitTheme
            themeSource = .explicit
        } else if let saved = parseSavedTheme(savedTheme) {
            resolvedTheme = saved
            themeSource = .saved
        } else if let system = inferSystemTheme(isDark: isDark) {
            resolvedTheme = system
            themeSource = .system
        } else {
            resolvedTheme = nil
            themeSource = .unresolved
        }

        var unresolvedFields: [String] = []
        var questions: [InstallationPreferenceQuestion] = []

        if resolvedLanguage == nil {
            unresolvedFields.append("language")
            questions.append(languageQuestion())
        }
        if resolvedTheme == nil {
            unresolvedFields.append("theme")
            questions.append(themeQuestion())
        }

        let needsInput = !unresolvedFields.isEmpty
        return InstallationPreferenceProposal(
            status: needsInput ? "needs_input" : "ready",
            ok: !needsInput,
            needsInput: needsInput,
            language: resolvedLanguage?.rawValue,
            languageSource: languageSource,
            theme: resolvedTheme?.rawValue,
            themeSource: themeSource,
            sources: InstallationPreferenceSources(
                language: languageSource,
                theme: themeSource
            ),
            unresolvedFields: unresolvedFields,
            questions: questions
        )
    }
}

func inferSystemLanguage(from preferredLanguages: [String]) -> AppLanguage? {
    InstallationPreferenceInference.inferSystemLanguage(from: preferredLanguages)
}

func inferSystemTheme(isDark: Bool?) -> ThemeID? {
    InstallationPreferenceInference.inferSystemTheme(isDark: isDark)
}

func inferInstallationPreferences(
    savedSettings: PersistedAppearanceSettings?,
    preferredLanguages: [String],
    isDark: Bool?,
    explicitLanguage: AppLanguage? = nil,
    explicitTheme: ThemeID? = nil
) -> InstallationPreferenceProposal {
    InstallationPreferenceInference.infer(
        savedLanguage: savedSettings?.language,
        savedTheme: savedSettings?.theme,
        preferredLanguages: preferredLanguages,
        isDark: isDark,
        explicitLanguage: explicitLanguage,
        explicitTheme: explicitTheme
    )
}

func inferInstallationPreferences(
    savedDictionary: [String: Any]?,
    preferredLanguages: [String],
    isDark: Bool?,
    explicitLanguage: AppLanguage? = nil,
    explicitTheme: ThemeID? = nil
) -> InstallationPreferenceProposal {
    let rawLanguage = savedDictionary?["language"] as? String
    let rawTheme = savedDictionary?["theme"] as? String
    return InstallationPreferenceInference.infer(
        savedLanguage: rawLanguage,
        savedTheme: rawTheme,
        preferredLanguages: preferredLanguages,
        isDark: isDark,
        explicitLanguage: explicitLanguage,
        explicitTheme: explicitTheme
    )
}

func inferInstallationPreferences(
    savedLanguage: String?,
    savedTheme: String?,
    preferredLanguages: [String],
    isDark: Bool?,
    explicitLanguage: AppLanguage? = nil,
    explicitTheme: ThemeID? = nil
) -> InstallationPreferenceProposal {
    InstallationPreferenceInference.infer(
        savedLanguage: savedLanguage,
        savedTheme: savedTheme,
        preferredLanguages: preferredLanguages,
        isDark: isDark,
        explicitLanguage: explicitLanguage,
        explicitTheme: explicitTheme
    )
}

// MARK: - Command Parsing & Local Persistence Helpers

private enum InstallationCommandMode: Equatable {
    case inspect
    case configure
}

private struct ParsedInstallationCommand: Equatable {
    let mode: InstallationCommandMode
    let explicitLanguage: AppLanguage?
    let explicitTheme: ThemeID?
}

private enum InstallationCommandParseResult: Equatable {
    case notApplicable
    case valid(ParsedInstallationCommand)
    case invalid(String)
}

private func parseInstallationCommandArguments(_ arguments: [String]) -> InstallationCommandParseResult {
    let inspectFlag = "--inspect-install-preferences"
    let configureFlag = "--configure-install-preferences"

    let touchesInspect = arguments.contains { $0 == inspectFlag || $0.hasPrefix(inspectFlag + "=") }
    let touchesConfigure = arguments.contains { $0 == configureFlag || $0.hasPrefix(configureFlag + "=") }

    guard touchesInspect || touchesConfigure else {
        return .notApplicable
    }

    var tokens = arguments
    if let first = tokens.first, !first.hasPrefix("-") {
        tokens.removeFirst()
    }

    var mode: InstallationCommandMode?
    var explicitLanguage: AppLanguage?
    var explicitTheme: ThemeID?

    var index = 0
    while index < tokens.count {
        let token = tokens[index]
        switch token {
        case inspectFlag:
            if mode != nil && mode != .inspect {
                return .invalid("Cannot combine \(inspectFlag) and \(configureFlag).")
            }
            mode = .inspect
            index += 1

        case configureFlag:
            if mode != nil && mode != .configure {
                return .invalid("Cannot combine \(inspectFlag) and \(configureFlag).")
            }
            mode = .configure
            index += 1

        case "--language":
            guard index + 1 < tokens.count else {
                return .invalid("Missing value for --language. Allowed values: en, zh-Hans.")
            }
            let rawValue = tokens[index + 1]
            if rawValue.hasPrefix("-") {
                return .invalid("Missing value for --language. Allowed values: en, zh-Hans.")
            }
            guard let parsed = InstallationPreferenceInference.parseExplicitLanguage(rawValue) else {
                return .invalid("Invalid value for --language: '\(rawValue)'. Allowed values: en, zh-Hans.")
            }
            if let existing = explicitLanguage, existing != parsed {
                return .invalid("Conflicting values provided for --language.")
            }
            explicitLanguage = parsed
            index += 2

        case "--theme":
            guard index + 1 < tokens.count else {
                return .invalid("Missing value for --theme. Allowed values: cupertino, graphite, instrument, frosted, liquid, vibrant.")
            }
            let rawValue = tokens[index + 1]
            if rawValue.hasPrefix("-") {
                return .invalid("Missing value for --theme. Allowed values: cupertino, graphite, instrument, frosted, liquid, vibrant.")
            }
            guard let parsed = InstallationPreferenceInference.parseExplicitTheme(rawValue) else {
                return .invalid("Invalid value for --theme: '\(rawValue)'. Allowed values: cupertino, graphite, instrument, frosted, liquid, vibrant.")
            }
            if let existing = explicitTheme, existing != parsed {
                return .invalid("Conflicting values provided for --theme.")
            }
            explicitTheme = parsed
            index += 2

        default:
            if token.hasPrefix("--language=") {
                let rawValue = String(token.dropFirst("--language=".count))
                guard let parsed = InstallationPreferenceInference.parseExplicitLanguage(rawValue) else {
                    return .invalid("Invalid value for --language: '\(rawValue)'. Allowed values: en, zh-Hans.")
                }
                if let existing = explicitLanguage, existing != parsed {
                    return .invalid("Conflicting values provided for --language.")
                }
                explicitLanguage = parsed
                index += 1
            } else if token.hasPrefix("--theme=") {
                let rawValue = String(token.dropFirst("--theme=".count))
                guard let parsed = InstallationPreferenceInference.parseExplicitTheme(rawValue) else {
                    return .invalid("Invalid value for --theme: '\(rawValue)'. Allowed values: cupertino, graphite, instrument, frosted, liquid, vibrant.")
                }
                if let existing = explicitTheme, existing != parsed {
                    return .invalid("Conflicting values provided for --theme.")
                }
                explicitTheme = parsed
                index += 1
            } else {
                return .invalid("Unknown or unsupported argument: '\(token)'.")
            }
        }
    }

    guard let resolvedMode = mode else {
        return .notApplicable
    }

    return .valid(
        ParsedInstallationCommand(
            mode: resolvedMode,
            explicitLanguage: explicitLanguage,
            explicitTheme: explicitTheme
        )
    )
}

private struct SavedAppearanceSnapshot {
    var language: String?
    var theme: String?
    var customColors: [ThemeID: ThemeColorPair]
    var hasCompleteCanonicalRecord: Bool
}

private func parseCustomColorsDictionary(_ rawMap: [String: Any]) -> [ThemeID: ThemeColorPair] {
    var result: [ThemeID: ThemeColorPair] = [:]
    for (rawKey, rawValue) in rawMap {
        guard let keyTheme = ThemeID.resolve(rawKey), keyTheme.supportsCustomColors else {
            continue
        }
        guard let pairDict = rawValue as? [String: Any] else {
            continue
        }
        let def = keyTheme.defaultColorPair
        let primary = RGBColor.normalizeHex(pairDict["primaryHex"] as? String) ?? def.primaryHex
        let secondary = RGBColor.normalizeHex(pairDict["secondaryHex"] as? String) ?? def.secondaryHex
        result[keyTheme] = ThemeColorPair(primaryHex: primary, secondaryHex: secondary)
    }
    return result
}

private func readSavedAppearanceSnapshot(from userDefaults: UserDefaults?) -> SavedAppearanceSnapshot {
    guard let defaults = userDefaults,
          let rawObject = defaults.object(forKey: AppearanceStore.storageKey) else {
        return SavedAppearanceSnapshot(
            language: nil,
            theme: nil,
            customColors: [:],
            hasCompleteCanonicalRecord: false
        )
    }

    let rawData: Data?
    if let data = rawObject as? Data {
        rawData = data
    } else if let str = rawObject as? String {
        rawData = str.data(using: .utf8)
    } else {
        rawData = nil
    }

    if let data = rawData {
        if let decoded = try? JSONDecoder().decode(PersistedAppearanceSettings.self, from: data) {
            var colors: [ThemeID: ThemeColorPair] = [:]
            for (rawKey, pair) in decoded.customColors {
                guard let keyTheme = ThemeID.resolve(rawKey), keyTheme.supportsCustomColors else {
                    continue
                }
                let def = keyTheme.defaultColorPair
                let p = RGBColor.normalizeHex(pair.primaryHex) ?? def.primaryHex
                let s = RGBColor.normalizeHex(pair.secondaryHex) ?? def.secondaryHex
                colors[keyTheme] = ThemeColorPair(primaryHex: p, secondaryHex: s)
            }
            let validLang = InstallationPreferenceInference.parseSavedLanguage(decoded.language)
            let validTheme = InstallationPreferenceInference.parseSavedTheme(decoded.theme)
            let isCanonical = (validLang != nil && validTheme != nil && decoded.language == validLang?.rawValue && decoded.theme == validTheme?.rawValue)
            return SavedAppearanceSnapshot(
                language: decoded.language,
                theme: decoded.theme,
                customColors: colors,
                hasCompleteCanonicalRecord: isCanonical
            )
        }

        if let dict = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            let colors = (dict["customColors"] as? [String: Any]).map(parseCustomColorsDictionary) ?? [:]
            return SavedAppearanceSnapshot(
                language: dict["language"] as? String,
                theme: dict["theme"] as? String,
                customColors: colors,
                hasCompleteCanonicalRecord: false
            )
        }

        return SavedAppearanceSnapshot(
            language: nil,
            theme: nil,
            customColors: [:],
            hasCompleteCanonicalRecord: false
        )
    }

    if let dict = rawObject as? [String: Any] {
        let colors = (dict["customColors"] as? [String: Any]).map(parseCustomColorsDictionary) ?? [:]
        return SavedAppearanceSnapshot(
            language: dict["language"] as? String,
            theme: dict["theme"] as? String,
            customColors: colors,
            hasCompleteCanonicalRecord: false
        )
    }

    return SavedAppearanceSnapshot(
        language: nil,
        theme: nil,
        customColors: [:],
        hasCompleteCanonicalRecord: false
    )
}

func detectSystemAppearanceIsDark(from effectiveAppearance: NSAppearance? = nil) -> Bool? {
    let appearance = effectiveAppearance ?? NSApp?.effectiveAppearance ?? NSApplication.shared.effectiveAppearance
    guard let match = appearance.bestMatch(from: [.aqua, .darkAqua]) else {
        return nil
    }
    if match == .darkAqua {
        return true
    }
    if match == .aqua {
        return false
    }
    return nil
}

private func currentCustomColorsMap(from appearance: AppearanceStore) -> [String: ThemeColorPair] {
    var map: [String: ThemeColorPair] = [:]
    for item in ThemeID.allCases {
        map[item.rawValue] = appearance.colorPair(for: item)
    }
    return map
}

private func encodeProposalJSON(_ proposal: InstallationPreferenceProposal) -> String {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    guard let data = try? encoder.encode(proposal),
          let text = String(data: data, encoding: .utf8) else {
        return "{\"error\":\"Failed to encode installation preference JSON.\",\"needs_input\":false,\"ok\":false,\"status\":\"error\"}"
    }
    return text
}

// MARK: - Root Entry Point & Testable Command Handler

func handleInstallationPreferenceCommand(
    _ arguments: [String],
    appearance: AppearanceStore
) -> Int? {
    let parseResult = parseInstallationCommandArguments(arguments)
    if case .notApplicable = parseResult {
        return nil
    }
    let resolvedDefaults = UserDefaults.standard
    return handleInstallationPreferenceCommand(
        arguments,
        appearance: appearance,
        userDefaults: resolvedDefaults,
        preferredLanguages: Locale.preferredLanguages,
        isDark: detectSystemAppearanceIsDark(),
        output: { print($0) }
    )
}

func handleInstallationPreferenceCommand(
    _ arguments: [String],
    appearance: AppearanceStore,
    userDefaults: UserDefaults?,
    preferredLanguages: [String],
    isDark: Bool?,
    output: (String) -> Void = { print($0) }
) -> Int? {
    let parseResult = parseInstallationCommandArguments(arguments)
    switch parseResult {
    case .notApplicable:
        return nil

    case .invalid(let message):
        let errorProposal = InstallationPreferenceProposal(
            status: "error",
            ok: false,
            needsInput: false,
            language: nil,
            languageSource: .unresolved,
            theme: nil,
            themeSource: .unresolved,
            sources: InstallationPreferenceSources(language: .unresolved, theme: .unresolved),
            unresolvedFields: [],
            questions: [],
            wrotePreferences: false,
            customColors: nil,
            error: message
        )
        output(encodeProposalJSON(errorProposal))
        return 2

    case .valid(let command):
        let savedSnapshot = readSavedAppearanceSnapshot(from: userDefaults)
        var proposal = InstallationPreferenceInference.infer(
            savedLanguage: savedSnapshot.language,
            savedTheme: savedSnapshot.theme,
            preferredLanguages: preferredLanguages,
            isDark: isDark,
            explicitLanguage: command.explicitLanguage,
            explicitTheme: command.explicitTheme
        )

        switch command.mode {
        case .inspect:
            output(encodeProposalJSON(proposal))
            return 0

        case .configure:
            guard !proposal.needsInput,
                  let rawLanguage = proposal.language,
                  let resolvedLanguage = AppLanguage(rawValue: rawLanguage),
                  let rawTheme = proposal.theme,
                  let resolvedTheme = ThemeID(rawValue: rawTheme) else {
                proposal.status = "needs_input"
                proposal.ok = false
                proposal.wrotePreferences = false
                proposal.error = "Unresolved installation preferences require explicit --language and/or --theme."
                output(encodeProposalJSON(proposal))
                return 2
            }

            let savedLanguage = InstallationPreferenceInference.parseSavedLanguage(savedSnapshot.language)
            let savedTheme = InstallationPreferenceInference.parseSavedTheme(savedSnapshot.theme)
            let alreadyPersistedAndMatching = savedSnapshot.hasCompleteCanonicalRecord
                && savedLanguage == resolvedLanguage
                && savedTheme == resolvedTheme
                && appearance.language == resolvedLanguage
                && appearance.theme == resolvedTheme

            if alreadyPersistedAndMatching {
                proposal.wrotePreferences = false
            } else {
                for (themeKey, pair) in savedSnapshot.customColors where themeKey.supportsCustomColors {
                    if appearance.colorPair(for: themeKey) == themeKey.defaultColorPair {
                        appearance.setPrimaryHex(pair.primaryHex, for: themeKey)
                        appearance.setSecondaryHex(pair.secondaryHex, for: themeKey)
                    }
                }
                appearance.language = resolvedLanguage
                appearance.theme = resolvedTheme
                proposal.wrotePreferences = true
            }

            _ = userDefaults?.synchronize()
            proposal.status = "configured"
            proposal.ok = true
            proposal.needsInput = false
            proposal.customColors = currentCustomColorsMap(from: appearance)
            output(encodeProposalJSON(proposal))
            return 0
        }
    }
}

// MARK: - Pure & Isolated Suite Self-Tests (No Real UserDefaults Access)

func runInstallationPreferenceSelfTests() -> [String] {
    var failures: [String] = []
    func check(_ condition: @autoclosure () -> Bool, _ label: String) {
        if !condition() {
            failures.append(label)
        }
    }

    // 1. Language inference rules
    check(inferSystemLanguage(from: ["en-US", "zh-Hans-CN"]) == .english, "en-US should resolve to .english")
    check(inferSystemLanguage(from: ["zh-Hans-CN", "en-US"]) == .chinese, "zh-Hans-CN should resolve to .chinese")
    check(inferSystemLanguage(from: ["zh-CN"]) == .chinese, "zh-CN should resolve to .chinese")
    check(inferSystemLanguage(from: ["zh-SG"]) == .chinese, "zh-SG should resolve to .chinese")
    check(inferSystemLanguage(from: ["zh"]) == .chinese, "generic zh should resolve to .chinese")
    check(inferSystemLanguage(from: ["zh-Hant-TW"]) == nil, "zh-Hant-TW alone must not map to .chinese")
    check(inferSystemLanguage(from: ["zh-TW", "zh-HK", "zh-MO"]) == nil, "Traditional Chinese regions must not map to .chinese")
    check(inferSystemLanguage(from: ["zh-Hant-HK", "en-GB"]) == .english, "Later supported language after zh-Hant-HK should win")
    check(inferSystemLanguage(from: ["ja-JP", "fr-FR"]) == nil, "Unsupported languages should return nil")

    // 2. Theme inference rules
    check(inferSystemTheme(isDark: true) == .graphite, "Dark appearance should infer .graphite")
    check(inferSystemTheme(isDark: false) == .cupertino, "Light appearance should infer .cupertino")
    check(inferSystemTheme(isDark: nil) == nil, "Unknown appearance should return nil")

    // 3. Precedence & independent fallback
    let savedLiquid = PersistedAppearanceSettings(theme: "liquid", language: "zh-Hans", customColors: [:])
    let p1 = inferInstallationPreferences(savedSettings: savedLiquid, preferredLanguages: ["en-US"], isDark: true)
    check(!p1.needsInput && p1.theme == "liquid" && p1.themeSource == .saved && p1.language == "zh-Hans" && p1.languageSource == .saved, "Saved settings must beat system")

    let p2 = inferInstallationPreferences(savedSettings: savedLiquid, preferredLanguages: ["en-US"], isDark: true, explicitLanguage: .english, explicitTheme: .vibrant)
    check(!p2.needsInput && p2.theme == "vibrant" && p2.themeSource == .explicit && p2.language == "en" && p2.languageSource == .explicit, "Explicit flags must beat saved")

    let partialSaved: [String: Any] = ["theme": "corrupt-theme", "language": "zh-Hans"]
    let p3 = inferInstallationPreferences(savedDictionary: partialSaved, preferredLanguages: ["en-US"], isDark: false)
    check(!p3.needsInput && p3.language == "zh-Hans" && p3.languageSource == .saved && p3.theme == "cupertino" && p3.themeSource == .system, "Corrupt saved theme must fall back to system while keeping valid saved language")

    let unresolvedLang = inferInstallationPreferences(savedSettings: nil, preferredLanguages: ["zh-Hant-TW"], isDark: true)
    check(unresolvedLang.needsInput && unresolvedLang.language == nil && unresolvedLang.theme == "graphite" && unresolvedLang.questions.map(\.field) == ["language"], "Unresolved language should ask only language question")

    // 4. Isolated UserDefaults suite tests
    let suiteName = "local.routingfloat.viewer.selftest.\(UUID().uuidString)"
    if let suite = UserDefaults(suiteName: suiteName) {
        defer {
            suite.removePersistentDomain(forName: suiteName)
        }
        let store = AppearanceStore(
            userDefaults: suite,
            persistsChanges: true,
            initialReduceTransparency: false,
            initialIncreaseContrast: false
        )

        check(handleInstallationPreferenceCommand(["TokenPilot"], appearance: store, userDefaults: suite, preferredLanguages: ["en-US"], isDark: true) == nil, "Normal launch args must return nil")

        var captured: [String] = []
        let inspectCode = handleInstallationPreferenceCommand(
            ["TokenPilot", "--inspect-install-preferences"],
            appearance: store,
            userDefaults: suite,
            preferredLanguages: ["en-US"],
            isDark: true,
            output: { captured.append($0) }
        )
        check(inspectCode == 0 && suite.data(forKey: AppearanceStore.storageKey) == nil, "Inspect must be read-only")

        let unresolvedConfigureCode = handleInstallationPreferenceCommand(
            ["TokenPilot", "--configure-install-preferences"],
            appearance: store,
            userDefaults: suite,
            preferredLanguages: ["zh-Hant-TW"],
            isDark: true,
            output: { captured.append($0) }
        )
        check(unresolvedConfigureCode == 2 && suite.data(forKey: AppearanceStore.storageKey) == nil, "Unresolved configure must exit 2 with no writes")

        store.setPrimaryHex("#123456", for: .liquid)
        let configureCode = handleInstallationPreferenceCommand(
            ["TokenPilot", "--configure-install-preferences", "--language", "zh-Hans", "--theme", "liquid"],
            appearance: store,
            userDefaults: suite,
            preferredLanguages: ["en-US"],
            isDark: true,
            output: { captured.append($0) }
        )
        check(configureCode == 0 && store.language == .chinese && store.theme == .liquid && store.colorPair(for: .liquid).primaryHex == "#123456", "Configure must persist choices and preserve custom accents")

        var repeatOutput = ""
        let repeatCode = handleInstallationPreferenceCommand(
            ["TokenPilot", "--configure-install-preferences", "--language", "zh-Hans", "--theme", "liquid"],
            appearance: store,
            userDefaults: suite,
            preferredLanguages: ["en-US"],
            isDark: true,
            output: { repeatOutput = $0 }
        )
        check(repeatCode == 0 && repeatOutput.contains("\"wrote_preferences\":false"), "Unchanged repeat configure must not rewrite preferences gratuitously")
    }

    return failures
}
