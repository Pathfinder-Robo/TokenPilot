import AppKit
import Foundation
let application=NSApplication.shared
var count=0
func check(_ ok:Bool,_ message:String){count += 1;if !ok{fputs("FAIL \(message)\n",stderr);exit(1)}}
func plan(_ saved:[String:Any]?=nil,_ languages:[String]=[],_ dark:Bool?=nil)->InstallationPreferenceProposal {
 inferInstallationPreferences(savedDictionary:saved,preferredLanguages:languages,isDark:dark)
}
let saved=plan(["language":"zh-Hans","theme":"liquid"],["en-US"],true)
check(saved.language=="zh-Hans" && saved.theme=="liquid","saved choices beat system")
check(saved.languageSource == .saved && saved.themeSource == .saved,"saved evidence reported")
let explicit=inferInstallationPreferences(savedDictionary:["language":"en","theme":"liquid"],preferredLanguages:["en"],isDark:true,explicitLanguage:.chinese,explicitTheme:.vibrant)
check(explicit.language=="zh-Hans" && explicit.theme=="vibrant","explicit choices beat saved")
check(explicit.languageSource == .explicit && explicit.themeSource == .explicit,"explicit evidence reported")
check(plan(nil,["en-US"],true).theme=="graphite","dark maps to Graphite")
check(plan(nil,["zh-Hans-CN"],false).theme=="cupertino","light maps to Cupertino")
for tag in ["en","en-US","en_GB"] {check(inferSystemLanguage(from:[tag]) == .english,"English variants")}
for tag in ["zh","zh-Hans","zh-Hans-CN","zh_CN","zh-SG"] {check(inferSystemLanguage(from:[tag]) == .chinese,"Simplified Chinese variants")}
for tag in ["zh-Hant","zh-Hant-TW","zh-TW","zh-HK","zh_MO"] {check(inferSystemLanguage(from:[tag]) == nil,"Traditional Chinese not silently converted")}
check(inferSystemLanguage(from:["zh-Hant","en-GB"]) == .english,"supported secondary language")
check(inferSystemLanguage(from:["fr-FR","de-DE"]) == nil,"unsupported preferences require input")
check(inferSystemLanguage(from:[]) == nil,"missing language requires input")
let missingLanguage=plan(nil,["fr"],true)
check(missingLanguage.questions.map(\.field)==["language"] && missingLanguage.theme=="graphite","ask only missing language")
let missingTheme=plan(nil,["en"],nil)
check(missingTheme.questions.map(\.field)==["theme"] && missingTheme.language=="en","ask only missing theme")
let partial=plan(["theme":"liquid"],["en-US"],true)
check(partial.theme=="liquid" && partial.themeSource == .saved && partial.languageSource == .system,"partial saved fields inferred independently")
let badSaved=plan(["theme":"invalid","language":"invalid"],["zh-CN"],false)
check(badSaved.language=="zh-Hans" && badSaved.theme=="cupertino","invalid saved fields fall back")
let suite="local.tokenpilot.install-tests."+UUID().uuidString
let defaults=UserDefaults(suiteName:suite)!
defaults.removePersistentDomain(forName:suite)
defer {defaults.removePersistentDomain(forName:suite)}
func store()->AppearanceStore {AppearanceStore(userDefaults:defaults,persistsChanges:true,initialReduceTransparency:false,initialIncreaseContrast:false)}
func command(_ args:[String],_ languages:[String]=["en"],_ dark:Bool?=false)->(Int?,[String:Any]) {
 var output=""
 let code=handleInstallationPreferenceCommand(["TokenPilot"]+args,appearance:store(),userDefaults:defaults,preferredLanguages:languages,isDark:dark,output:{output=$0})
 let value=(try? JSONSerialization.jsonObject(with:Data(output.utf8))) as? [String:Any] ?? [:]
 return(code,value)
}
let inspect=command(["--inspect-install-preferences"],["fr"],nil)
check(inspect.1["needs_input"] as? Bool == true,"inspection exposes unanswered questions")
check(defaults.data(forKey:AppearanceStore.storageKey)==nil,"inspection does not write")
let unresolved=command(["--configure-install-preferences"],["fr"],nil)
check(unresolved.0==2 && defaults.data(forKey:AppearanceStore.storageKey)==nil,"unresolved configure does not partly write")
let first=command(["--configure-install-preferences","--language","en","--theme","graphite"])
check(first.0==0 && first.1["wrote_preferences"] as? Bool == true,"first install persists even if matching defaults")
check(defaults.data(forKey:AppearanceStore.storageKey) != nil,"first install settings actually stored")
var colors=[String:ThemeColorPair]()
for theme in ThemeID.allCases {colors[theme.rawValue]=theme.defaultColorPair}
colors["liquid"]=ThemeColorPair(primaryHex:"#FF00DD",secondaryHex:"#00FF99")
colors["vibrant"]=ThemeColorPair(primaryHex:"#AA22CC",secondaryHex:"#11DD99")
let payload=PersistedAppearanceSettings(theme:"liquid",language:"en",customColors:colors)
defaults.set(try! JSONEncoder().encode(payload),forKey:AppearanceStore.storageKey)
let changed=command(["--configure-install-preferences","--language","zh-Hans"])
check(changed.0==0,"valid language-only configure succeeds")
let decoded=try! JSONDecoder().decode(PersistedAppearanceSettings.self,from:defaults.data(forKey:AppearanceStore.storageKey)!)
check(decoded.theme=="liquid" && decoded.language=="zh-Hans","configuration keeps saved theme")
check(decoded.customColors["liquid"]==colors["liquid"] && decoded.customColors["vibrant"]==colors["vibrant"],"all custom palettes retained")
let before=defaults.data(forKey:AppearanceStore.storageKey)
let same=command(["--configure-install-preferences"])
check(same.1["wrote_preferences"] as? Bool == false && defaults.data(forKey:AppearanceStore.storageKey)==before,"unchanged saved settings not rewritten")
for args in [["--configure-install-preferences","--theme","bad"],["--configure-install-preferences","--language","zh-Hant"],["--configure-install-preferences","--unknown"],["--inspect-install-preferences","--configure-install-preferences"]] {
 let result=command(args)
 check(result.0==2 && defaults.data(forKey:AppearanceStore.storageKey)==before,"invalid or conflicting command never writes")
}
let rawPartial:[String:Any] = ["theme":"liquid","customColors":["liquid":["primaryHex":"#FF00DD","secondaryHex":"#00FF99"]]]
defaults.set(try! JSONSerialization.data(withJSONObject:rawPartial),forKey:AppearanceStore.storageKey)
let repaired=command(["--configure-install-preferences","--language","en"])
let repairedData=try! JSONDecoder().decode(PersistedAppearanceSettings.self,from:defaults.data(forKey:AppearanceStore.storageKey)!)
check(repaired.0==0 && repairedData.customColors["liquid"]==colors["liquid"],"partial-record palette survives configuration")
check(handleInstallationPreferenceCommand(["TokenPilot","--ui-checks"],appearance:store(),userDefaults:defaults,preferredLanguages:[],isDark:nil,output:{_ in})==nil,"other app CLI modes untouched")
check(inferSystemLanguage(from:["zh-Hans-HK"]) == .chinese,"explicit Simplified script takes priority over region")
print("Passed \(count) independent installation inference and isolated-persistence checks")
