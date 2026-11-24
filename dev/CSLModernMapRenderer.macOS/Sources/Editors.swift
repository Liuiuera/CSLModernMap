import AppKit
import CoreFoundation

func isBoolean(_ value: Any) -> Bool {
    guard let number = value as? NSNumber else { return false }
    return CFGetTypeID(number) == CFBooleanGetTypeID()
}

func pathValue(_ root:Any,_ path:[String]) -> Any? {
    guard let key=path.first else{return root}
    if let list=root as? [Any],let index=Int(key),list.indices.contains(index){return pathValue(list[index],Array(path.dropFirst()))}
    if let value=(root as? JSON)?[key]{return pathValue(value,Array(path.dropFirst()))};return nil
}
func replacingPath(_ root:Any,_ path:[String],_ value:Any) -> Any {
    guard let key=path.first else{return value}
    if var list=root as? [Any],let index=Int(key),list.indices.contains(index){list[index]=replacingPath(list[index],Array(path.dropFirst()),value);return list}
    var dict=object(root);dict[key]=replacingPath(dict[key] ?? [:],Array(path.dropFirst()),value);return dict
}
@MainActor final class PoiEditor:NSObject {
    unowned let owner:InterfaceController
    let window=EditorWindow("图标映射",width:760,height:600)
    var model:JSON
    var changes:JSON=[:]
    let selector=NSPopUpButton(frame:.zero,pullsDown:false)
    let form=FormView()
    var keys:[String]=[]
    init(owner:InterfaceController,model:JSON){self.owner=owner;self.model=model;super.init()}
    func entryName(_ entry: JSON) -> String {
        let name = textValue(entry["name"]), id = textValue(entry["id"])
        if name == id, name.hasPrefix("BUILDING_TITLE["), name.hasSuffix("]") { return String(name.dropFirst(15).dropLast()) }
        if name == id, name.hasPrefix("stop:") { return NativePresentation.identifier(String(name.dropFirst(5))) + " · " + localized("站点") }
        if name == id, name.hasPrefix("park:") { return NativePresentation.identifier(String(name.dropFirst(5))) + " · " + localized("功能区划") }
        return name
    }
    func show(){
        keys=object(model["entries"]).keys.sorted()
        let titles = keys.map { key -> String in let entry=object(object(model["entries"])[key]);return entryName(entry)+" · "+textValue(entry["game"]).uppercased() }
        let counts = Dictionary(grouping: titles, by: { $0 }).mapValues { $0.count }
        var occurrences: [String: Int] = [:]
        for title in titles {
            occurrences[title, default: 0] += 1
            selector.addItem(withTitle: title + (counts[title, default: 0] > 1 ? "（\(occurrences[title]!)）" : ""))
        }
        selector.target=self;selector.action=#selector(selectEntry)
        let actions=row([ActionButton("校验"){[weak self] in self?.save(validateOnly:true)},ActionButton("保存"){[weak self] in self?.save(validateOnly:false)}])
        let host=window.surface;let content=column([selector,form,actions]);pin(content,in:host,inset:18)
        form.widthAnchor.constraint(equalTo:content.widthAnchor).isActive=true;form.heightAnchor.constraint(equalTo:content.heightAnchor,constant:-80).isActive=true
        selector.widthAnchor.constraint(equalTo:content.widthAnchor).isActive=true
        window.onClose={ [weak self] in self?.owner.poiEditor=nil }
        selectEntry();owner.panels.append(window);window.center();window.makeKeyAndOrderFront(nil)
    }
    @objc func selectEntry(){
        form.stack.arrangedSubviews.forEach{$0.removeFromSuperview()};form.fields.removeAll()
        guard keys.indices.contains(selector.indexOfSelectedItem) else{return};let key=keys[selector.indexOfSelectedItem]
        let entry=object(object(model["entries"])[key]);let edited=object(changes[key]);let values=edited.isEmpty ? ["icons":entry["icons"] ?? [:],"display":entry["display"] ?? true] : edited
        form.add(label(entryName(entry),18,.semibold))
        form.add(owner.settingsHelp(localized("类别标识") + "：" + key))
        form.field("显示",["kind":"toggle"],values["display"] ?? true){[weak self] value in self?.set(key,"display",value)}
        for library in object(model["libraries"]).keys.sorted(){
            let options=array(object(model["libraries"])[library]);let choices:[[String:Any]]=[["value":"","label":"不显示图标"]]+options.map{["value":$0["file"] ?? "","label":$0["name"] ?? $0["file"] ?? ""]}
            form.field(NativePresentation.identifier(library),["kind":"choice","choices":choices],object(values["icons"])[library] ?? ""){[weak self] value in guard let self=self else{return};var icons=object(object(self.changes[key])["icons"] ?? values["icons"]);icons[library]=value;self.set(key,"icons",icons)}
            if let match=options.first(where:{textValue($0["file"])==textValue(object(values["icons"])[library])}),let url=URL(string:textValue(match["url"])),let image=NSImage(contentsOf:url){let view=NSImageView(image:image);view.imageScaling = .scaleProportionallyDown;view.heightAnchor.constraint(equalToConstant:80).isActive=true;form.add(view)}
        }
        form.add(ActionButton("恢复此项默认映射"){[weak self] in guard let self=self else{return};self.changes[key]=entry["default"];self.window.dirty=true;self.selectEntry()})
    }
    func set(_ key:String,_ field:String,_ value:Any){let entry=object(object(model["entries"])[key]);var changed=object(changes[key]);if changed.isEmpty{changed=["icons":entry["icons"] ?? [:],"display":entry["display"] ?? true]};changed[field]=value;changes[key]=changed;window.dirty=true}
    func save(validateOnly:Bool){owner.run{[self] in window.saving=true;defer{window.saving=false}
        let result=try await owner.engine.request(validateOnly ? "validatePoiMapping" : "savePoiMapping",["token":model["token"] ?? "","changes":changes])
        if validateOnly{let count=array(result["issues"]).count;owner.showStatus(count == 0 ? localized("图标映射校验通过。") : String(format:localized("图标映射有 %d 个问题，请检查后保存。"),count))}
        else{window.dirty=false;changes.removeAll();model=try await owner.engine.request("getPoiEditor");selectEntry();_=try await owner.engine.request("refreshPreview");try await owner.refreshSnapshot()}
    }}
}

@MainActor final class TextIconEditor:NSObject {
    unowned let owner:InterfaceController
    let window=EditorWindow("文字与图标",width:650,height:760)
    let form=FormView()
    var model:JSON
    var target:String
    var original: JSON
    let category: MapSymbolCategory
    init(owner: InterfaceController, model: JSON, target: String, category: MapSymbolCategory = .iconText) {
        self.owner = owner; self.model = model; self.model["documentEpoch"] = owner.epoch
        self.original = model; self.target = target; self.category = category
        super.init()
    }
    var icons: Bool { category == .icons }
    var title: String { target == "transit" && !icons ? "文字" : category.title }
    var pages: [(String, String)] { icons ? SidebarLayout.labelPages : Array(SidebarLayout.labelPages.prefix(2)) }
    var pageKey: String { target + "." + title }
    var page = "display"
    let navigationHost = NSView()
    let sectionState = SectionState()
    var positions: [String: CGFloat] = [:]
    var editableFieldKeys: [String] {
        if target == "transit" {
            return icons ? MapSymbolCategory.poiIconFields : MapSymbolCategory.poiNameFields + ["poi.game_names"]
        }
        let display: [String]
        switch category {
        case .icons: display = ["transport.stations"]
        case .iconText: display = ["transport.station_names", "poi.game_names"]
        case .roadText: display = [category.visibilityKey, "road.game_names"]
        case .districtText: display = [category.visibilityKey]
        }
        return category.styleFields + display
    }
    var editableOptionKeys: [String] { target == "transit" ? (icons ? ["facilities_enabled", "stations"] : ["station_name_placement", "station_name_size"]) : [] }
    func show() {
        updateTitle()
        let host = window.surface
        let save = ActionButton("保存并应用") { [weak self] in self?.save() }
        let actions = row([ActionButton("恢复默认") { [weak self] in self?.restoreDefaults() }, NSView(), ActionButton("取消") { [weak self] in self?.window.performClose(nil) }, save])
        for view in [navigationHost, form, actions] { view.translatesAutoresizingMaskIntoConstraints = false; host.addSubview(view) }
        NSLayoutConstraint.activate([
            navigationHost.topAnchor.constraint(equalTo: host.topAnchor, constant: 14),
            navigationHost.leadingAnchor.constraint(equalTo: host.leadingAnchor, constant: 18),
            navigationHost.trailingAnchor.constraint(equalTo: host.trailingAnchor, constant: -18),
            navigationHost.heightAnchor.constraint(equalToConstant: 32),
            form.topAnchor.constraint(equalTo: navigationHost.bottomAnchor, constant: 8),
            form.leadingAnchor.constraint(equalTo: host.leadingAnchor), form.trailingAnchor.constraint(equalTo: host.trailingAnchor),
            form.bottomAnchor.constraint(equalTo: actions.topAnchor, constant: -10),
            actions.leadingAnchor.constraint(equalTo: host.leadingAnchor, constant: 18),
            actions.trailingAnchor.constraint(equalTo: host.trailingAnchor, constant: -18),
            actions.bottomAnchor.constraint(equalTo: host.bottomAnchor, constant: -14)])
        form.sectionState = sectionState
        rebuild()
        window.onClose = { [weak self] in self?.owner.textEditor = nil }
        owner.panels.append(window); window.center(); window.makeKeyAndOrderFront(nil)
    }
    func updateTitle() {
        window.title = localized(target == "normal" ? "普通地图" : "公共交通图") + " · " + localized(title)
    }
    func selectPage(_ key: String) {
        guard pages.contains(where: { $0.0 == key }) else { return }
        window.makeFirstResponder(nil)
        positions[page] = form.contentView.bounds.minY
        page = key; owner.textEditorPages[pageKey] = key; rebuild()
    }
    func rebuild() {
        form.onScroll = nil; form.reset()
        navigationHost.subviews.forEach { $0.removeFromSuperview() }
        pin(owner.settingsNavigation(pages, selected: page) { [weak self] in self?.selectPage($0) }, in: navigationHost)
        if page == "display" { buildDisplay() }
        else if page == "style" { buildAppearance() }
        else { buildMerge() }
        window.contentView?.layoutSubtreeIfNeeded(); form.restoreScroll(positions[page] ?? 0)
        form.onScroll = { [weak self] in guard let self = self else { return }; self.positions[self.page] = $0 }
    }
    func section(_ id: String, _ title: String, expanded: Bool = true) {
        form.section(title, key: "text.\(target).\(category.rawValue).\(id)", expanded: expanded)
    }
    func field(_ key: String, title: String? = nil) {
        var spec = owner.schema(key)
        guard !spec.isEmpty, let value = object(model["fields"])[key] else { return }
        let title = title ?? textValue(spec["label"])
        let numeric = textValue(spec["kind"]) == "number"
        if numeric {
            let total = referenceSize(value)
            spec["low"] = min(spec["low"] as? Double ?? 0, total)
            spec["high"] = max(spec["high"] as? Double ?? total, total)
        }
        let control = form.field(title, spec, numeric ? referenceSize(value) : value) { [weak self] value in
            guard let self, let current = object(self.model["fields"])[key] else { return }
            self.set(["fields", key], numeric ? resizedAppearanceValue(current, to: referenceSize(value)) : value)
        }
        control.view.identifier = NSUserInterfaceItemIdentifier(key)
        control.readValue = { [weak self] in
            guard let value = object(self?.model["fields"])[key] else { return nil }
            return numeric ? referenceSize(value) : value
        }
        if target == "normal", ["road.game_names", "poi.game_names", "transport.station_names"].contains(key) {
            control.enabledWhen = { [weak self] in self?.mapCategoryVisible == true }
            control.refresh(enabled: true)
        }
    }
    func option(_ key: String, _ title: String) {
        guard let value = object(model["options"])[key] ?? object(model["optionDefaults"])[key] else { return }
        var spec = object(object(model["optionSchema"])[key])
        if key == "station_name_placement" {
            spec["choices"] = [("hidden", "隐藏"), ("station", "站点旁"), ("icon", "跟随图标")].map { ["value": $0.0, "label": localized($0.1)] }
        }
        let control = form.field(title, spec, value) { [weak self] value in
            guard let self = self else { return }; self.set(["options", key], value)
        }
        control.view.identifier = NSUserInterfaceItemIdentifier("options." + key)
        control.readValue = { [weak self] in object(self?.model["options"])[key] }
        if key == "station_name_size" { control.enabledWhen = { [weak self] in textValue(object(self?.model["options"])["station_name_placement"]) != "hidden" } }
        control.refresh(enabled: true)
    }
    var mapCategoryVisible: Bool {
        if category.usesSymbolVisibility {
            return object(model["mapSymbolVisibility"])[category.rawValue] as? Bool ?? true
        }
        return object(model["fields"])[category.visibilityKey] as? Bool ?? false
    }
    func buildDisplay() {
        if target == "normal" {
            section("display", "显示内容")
            if category.usesSymbolVisibility {
                let path = ["mapSymbolVisibility", category.rawValue]
                let master = form.field(category.visibilityTitle, ["kind": "toggle"], mapCategoryVisible) { [weak self] in self?.set(path, $0) }
                master.view.identifier = NSUserInterfaceItemIdentifier("mapSymbolVisibility." + category.rawValue)
                master.readValue = { [weak self] in self?.mapCategoryVisible }
            } else {
                field(category.visibilityKey, title: category.visibilityTitle)
            }
        }
        if category.usesSymbolVisibility {
            section("visibility", icons ? "地点图标" : "地点名称")
            let channel = icons ? "icons" : "names"
            for (group, title) in MapSymbolCategory.poiGroups {
                if target == "transit" && (group == "transit" || group == "area") { continue }
                let control = form.field(title, ["kind": "toggle"], object(object(model["visibility"])[channel])[group] ?? false) { [weak self] value in
                    self?.set(["visibility", channel, group], value)
                }
                control.view.identifier = NSUserInterfaceItemIdentifier("visibility.\(channel).\(group)")
                control.readValue = { [weak self] in object(object(self?.model["visibility"])[channel])[group] }
                control.enabledWhen = { [weak self] in
                    guard let self, self.target != "normal" || self.mapCategoryVisible else { return false }
                    if self.icons { return true }
                    let correspondingIcon = object(object(self.model["visibility"])["icons"])[group] as? Bool == true
                    let iconsVisible = object(self.model["mapSymbolVisibility"])["poi"] as? Bool != false
                    return correspondingIcon && (self.target != "normal" || iconsVisible)
                }
                control.refresh(enabled: true)
            }
            if target == "transit" && icons { option("facilities_enabled", "交通图标") }
            if !icons { form.add(owner.settingsHelp("地点名称跟随对应图标显示；隐藏图标时保留名称设置。")) }
            section("stations", "站点")
            if target == "normal" {
                field(icons ? "transport.stations" : "transport.station_names")
            } else if icons {
                option("stations", "站点圆圈")
            } else {
                option("station_name_placement", "站名标注")
                option("station_name_size", "站名字号（参考像素）")
                form.add(owner.settingsHelp("隐藏站名时，字号不可用并保留原值；站名位置独立于站点圆圈。"))
            }
        }
        if category == .roadText || category == .iconText {
            section("sources", "名称来源")
            field(category == .roadText ? "road.game_names" : "poi.game_names")
        }
    }
    func buildAppearance() {
        if category.usesSymbolVisibility {
            section("style.poi", icons ? "图标外观" : "地点名称")
            for key in icons ? MapSymbolCategory.poiIconFields : MapSymbolCategory.poiNameFields { field(key) }
        }
        if target == "normal" {
            for kind in category.textStyleKinds {
                section("style." + kind, MapSymbolCategory.textStyleTitle(kind), expanded: category.isNameLayer)
                for key in MapSymbolCategory.textStyleFields(kind) { field(key) }
            }
        }
        if icons { form.add(ActionButton("图标映射…") { [weak self] in self?.owner.showPoiEditor() }) }
    }
    func buildMerge() {
        let profile = object(model["profile"])
        section("merge.common", "合并与避让")
        profileField(["merge_enabled"], "合并兴趣点", ["kind": "toggle"])
        form.add(owner.settingsHelp("关闭合并时，分类合并和距离不可用，原设置保留。"))
        for (group, title) in [("transit", "交通"), ("ordinary", "普通建筑"), ("facility", "设施"), ("area", "功能区")] {
            let values = object(object(profile["groups"])[group]); guard !values.isEmpty else { continue }
            section("merge." + group, title)
            for key in ["merge_enabled", "distance_m"] {
                guard let value = values[key] else { continue }
                profileField(["groups", group, key], key == "merge_enabled" ? "合并邻近地点" : "合并距离（米）", ["kind": isBoolean(value) ? "toggle" : "number", "low": 0, "high": 500])
            }
        }
        section("merge.density", "密度与避让")
        profileField(["density_enabled"], "密度控制", ["kind": "toggle"])
        let tiers = [("extremely_sparse", "极稀疏"), ("very_sparse", "很稀疏"), ("sparse", "稀疏"), ("standard", "标准"), ("dense", "密集"), ("very_dense", "很密集"), ("extremely_dense", "极密集")]
        profileField(["density_tier"], "内容密度", ["kind": "choice", "choices": tiers.map { ["value": $0.0, "label": localized($0.1)] }])
        profileField(["overlap_avoidance"], "避免重叠", ["kind": "toggle"])
        form.add(owner.settingsHelp("关闭密度控制时，密度档位不可用，原设置保留。"))
        section("merge.advanced", "高级设置", expanded: false)
        profileField(["allow_different_station_names"], "允许不同站名合并", ["kind": "toggle"])
        for (key, title) in [("png_internal_buildings", "PNG 显示内部建筑"), ("children_min_zoom", "内部设施显示阈值")] {
            guard let value = profile[key] else { continue }
            profileField([key], title, ["kind": isBoolean(value) ? "toggle" : "number", "low": 0, "high": 2])
        }
        form.add(owner.settingsHelp("不同站名合并需要开启交通地点合并；内部设施显示阈值独立于 PNG 内部建筑开关。"))
    }
    func profileField(_ path: [String], _ title: String, _ spec: JSON) {
        guard let value = pathValue(model["profile"] ?? [:], path) else { return }
        let control = form.field(title, spec, value) { [weak self] in self?.set(["profile"] + path, $0) }
        control.view.identifier = NSUserInterfaceItemIdentifier((["profile"] + path).joined(separator: "."))
        control.readValue = { [weak self] in pathValue(self?.model["profile"] ?? [:], path) }
        control.enabledWhen = { [weak self] in
            let profile = object(self?.model["profile"])
            if path == ["density_tier"] { return profile["density_enabled"] as? Bool == true }
            if path.first == "groups" {
                return profile["merge_enabled"] as? Bool == true && (path.last != "distance_m" || object(object(profile["groups"])[path[1]])["merge_enabled"] as? Bool == true)
            }
            if path == ["allow_different_station_names"] { return profile["merge_enabled"] as? Bool == true && object(object(profile["groups"])["transit"])["merge_enabled"] as? Bool == true }
            return true
        }
        control.refresh(enabled: true)
    }
    func set(_ path:[String],_ value:Any){
        if let previous = pathValue(model, path), let before = try? jsonString(previous), let after = try? jsonString(value), before == after { return }
        model=object(replacingPath(model,path,value));window.dirty=true
        for control in form.fields { control.refresh(enabled: true) }
    }
    func restoreDefaults() {
        window.makeFirstResponder(nil)
        for key in editableFieldKeys { model["fields"] = replacingPath(model["fields"] ?? [:], [key], object(model["defaults"])[key]!) }
        for key in editableOptionKeys { model["options"] = replacingPath(model["options"] ?? [:], [key], object(model["optionDefaults"])[key]!) }
        if icons { model["profile"] = model["profileDefaults"] }
        if category.usesSymbolVisibility {
            let channel = icons ? "icons" : "names"
            var values = object(object(model["visibility"])[channel])
            for (key, value) in object(object(model["visibilityDefaults"])[channel]) where target == "normal" || ["facility", "ordinary"].contains(key) { values[key] = value }
            model["visibility"] = replacingPath(model["visibility"] ?? [:], [channel], values)
            if target == "normal" {
                model["mapSymbolVisibility"] = replacingPath(model["mapSymbolVisibility"] ?? [:], [category.rawValue], true)
            }
        }
        window.dirty = true; rebuild()
    }
    func commitInputs() throws {
        window.makeFirstResponder(nil)
        for control in form.fields where control.numeric && (control.view as? NSControl)?.isEnabled == true {
            let input = control.view as! NSTextField
            guard let value = Double(input.stringValue), value.isFinite, control.low.map({ value >= $0 }) ?? true, control.high.map({ value <= $0 }) ?? true else {
                window.makeFirstResponder(input)
                throw AppFailure(message: (input.toolTip ?? "") + "：" + localized("请输入有效范围内的数值。"))
            }
        }
        for control in form.fields where control.numeric { control.changed() }
    }
    func save(){owner.run{[self] in window.saving=true;defer{window.saving=false}
        try commitInputs()
        var edits: JSON = [:], options: JSON = [:], switches: JSON = [:]
        @MainActor func changed(_ path: [String]) throws -> Any? {
            guard let value = pathValue(model, path) else { return nil }
            return try jsonString(value) == jsonString(pathValue(original, path) ?? NSNull()) ? nil : value
        }
        for key in editableFieldKeys { edits[key] = try changed(["fields", key]) }
        for key in editableOptionKeys { options[key] = try changed(["options", key]) }
        let latest = try await owner.engine.request("getTextIconSettings", ["target":target])
        var visibility = object(latest["visibility"])
        if category.usesSymbolVisibility {
            let channel = icons ? "icons" : "names"
            var values = object(visibility[channel])
            for (group, _) in MapSymbolCategory.poiGroups {
                if let value = try changed(["visibility", channel, group]) { values[group] = value }
            }
            visibility[channel] = values
            if target == "normal" { switches[category.rawValue] = try changed(["mapSymbolVisibility", category.rawValue]) }
        }
        let profile = icons ? try changed(["profile"]) ?? latest["profile"] : latest["profile"]
        _ = try await owner.engine.request("setTextIconSettings", ["target":target, "documentEpoch":model["documentEpoch"] ?? -1,
            "theme":model["theme"] ?? "", "fields":edits, "profile":profile ?? [:],
            "visibility":visibility, "options":options, "mapSymbolVisibility":switches])
        original = model
        window.dirty = false
        try await owner.refreshSnapshot()
        try await owner.refreshMapPreview()
    }}
}
extension InterfaceController {
    func showThemeEditor() {
        if let editor = themeEditor { editor.window.makeKeyAndOrderFront(nil); return }
        guard engineReady, !busy, !openingThemeEditor else { return }
        let generation = epoch; openingThemeEditor = true
        run { [self] in
            defer { openingThemeEditor = false }
            let draft = try await engine.request("getThemeDraft")
            guard engineReady, epoch == generation, draft["documentEpoch"] as? Int == epoch else { return }
            let editor = ThemeEditor(owner: self, draft: draft); themeEditor = editor; editor.show()
        }
    }
    func showPoiEditor(){if let editor=poiEditor{editor.window.makeKeyAndOrderFront(nil);return};run{[self] in let model=try await engine.request("getPoiEditor");let editor=PoiEditor(owner:self,model:model);poiEditor=editor;editor.show()}}
    func textIconButtons() -> NSView {
        if context == "map" {
            return menuButton(localized("文字与图标"), entries: MapSymbolCategory.allCases.map { ($0.title, nil) })
        }
        return row([ActionButton("文字设置…") { [weak self] in self?.showTextIconEditor() },
             ActionButton("图标设置…") { [weak self] in self?.showTextIconEditor(category: .icons) }])
    }
    func showTextIconEditor(category: MapSymbolCategory = .iconText, page: String? = nil) {
        if context == "metro" { showTransitSettings(); return }
        if let editor = textEditor {
            guard !editor.window.saving else { return }
            if editor.category == category {
                if let page { editor.selectPage(page) }
                editor.window.makeKeyAndOrderFront(nil)
                return
            }
            guard editor.window.permitClose() else { return }
            editor.window.close()
        }
        run { [self] in
            let target = context == "map" ? "normal" : context, generation = epoch, targetContext = context
            let model = try await engine.request("getTextIconSettings", ["target": target])
            guard epoch == generation, context == targetContext, textEditor == nil else { return }
            let editor = TextIconEditor(owner: self, model: model, target: target, category: category)
            editor.page = (page.flatMap { item in editor.pages.contains { $0.0 == item } ? item : nil }) ?? textEditorPages[editor.pageKey] ?? "display"
            textEditor = editor; editor.show()
        }
    }
}
