import AppKit

extension InterfaceController {
    func schema(_ key:String) -> JSON {
        var field=object(object(snapshot["fieldSchema"])[key])
        if field["kind"] as? String == "font" { field["choices"]=snapshot["fonts"] }
        return field
    }
    func showLayer(_ name:String) {
        guard let key=layerKeys[name] else{return}
        let category = MapSymbolCategory(rawValue: key)
        let page = category.flatMap { sidebarNavigation.textPages[$0] } ?? "display"
        let pageKey = "map.\(key)" + (category == nil ? "" : ".\(page)")
        prepareSidebar(pageKey)
        sidebarNavigation.mapLayer = key
        selectedLayer=name
        panelHeader(leftHeader,title:name,back:true)
        let form=FormView();form.sectionState=sectionState;sidebarForm=form
        let navigation = category.map { category in
            settingsNavigation(Array(SidebarLayout.labelPages.prefix(2)), selected: page) { [weak self] page in
                guard let self, self.context == "map" else { return }
                self.sidebarNavigation.textPages[category] = page
                self.showLayer(name)
            }
        }
        installSidebarForm(form, navigation: navigation)
        form.section("显示",key:"layer.\(key).visibility")
        let generation = epoch
        let visibility = form.field(category?.visibilityTitle ?? "显示图层",["kind":"toggle"],defaultVisibility(name)) { [weak self, weak form] value in
            guard let self = self, self.context == "map", self.epoch == generation, form != nil, self.sidebarForm === form else { return }
            self.changeLayerVisibility(key, enabled: value)
        }
        if key == "satellite" { visibility.enabledWhen = { [weak self] in self?.snapshot["satelliteAvailable"] as? Bool == true } }
        visibility.readValue = { [weak self] in self?.defaultVisibility(name) }
        visibility.view.identifier = NSUserInterfaceItemIdentifier("layer.\(key).visible")
        if key == "satellite", snapshot["satelliteAvailable"] as? Bool != true {
            form.add(settingsHelp("当前城市未包含卫星影像，请使用带影像的城市文件。"))
        }
        if let category {
            buildMapSymbolSidebar(form, category: category, page: page)
        } else {
            for group in SidebarLayout.layerGroups(key) {
                let keys = group.fields.filter { !schema($0).isEmpty && object(snapshot["fields"])[$0] != nil }
                guard !keys.isEmpty else { continue }
                form.section(group.title, key: "layer.\(key).\(group.id)", expanded: group.expanded)
                for fieldKey in keys { addSchemaField(form: form, key: fieldKey, spec: schema(fieldKey), value: object(snapshot["fields"])[fieldKey]!) }
            }
        }
        if key == "poi" {
            form.add(ActionButton("设施筛选") { [weak self] in self?.selectFacilities() })
            form.add(ActionButton("图标映射…") { [weak self] in self?.showPoiEditor() })
        }
        if key == "satellite" {
            let recommendation = ActionButton("应用卫星推荐显示") { [weak self] in self?.recommendSatellite() }
            recommendation.enabledWhen = { [weak self] in self?.snapshot["satelliteAvailable"] as? Bool == true }
            form.add(recommendation)
        }
        form.section("操作",key:"layer.\(key).actions")
        if let category {
            let title = category == .icons ? "图标设置…" : category.title + "设置…"
            form.add(ActionButton(title) { [weak self] in self?.showTextIconEditor(category: category, page: page) })
        }
        if key == "district" || key == "zone" { form.add(ActionButton("选择区域…") { [weak self] in self?.showRegions(key == "district" ? "district" : "park") }) }
        form.add(ActionButton("恢复本图层默认设置") { [weak self] in
            guard let self else { return }
            if let category {
                self.update("resetFields", ["keys": category.styleFields])
            } else {
                self.update("resetLayer", ["layer":key])
            }
        })
        form.add(ActionButton("编辑主题…") { [weak self] in self?.showThemeEditor() })
        finishSidebar(form, key: pageKey)
    }
    func buildMapSymbolSidebar(_ form: FormView, category: MapSymbolCategory, page: String) {
        let prefix = "layer.\(category.rawValue)"
        let fields = object(snapshot["fields"])
        func addFields(_ keys: [String], split: Bool = true) {
            for key in keys {
                if let value = fields[key], !schema(key).isEmpty {
                    addSchemaField(form: form, key: key, spec: schema(key), value: value, splitComponents: split)
                }
            }
        }
        if page == "style" {
            if category.usesSymbolVisibility {
                form.section(category == .icons ? "图标外观" : "地点名称", key: prefix + ".appearance")
                addFields(category == .icons ? MapSymbolCategory.poiIconFields : MapSymbolCategory.poiNameFields, split: false)
            }
            for kind in category.textStyleKinds {
                form.section(MapSymbolCategory.textStyleTitle(kind), key: prefix + ".style." + kind, expanded: category.isNameLayer)
                addFields(MapSymbolCategory.textStyleFields(kind), split: false)
            }
            let sizeKeys = category.styleFields.filter { fields[$0] is JSON && ($0.hasPrefix("label.size.") || ["poi.size", "poi.name_size"].contains($0)) }
            if !sizeKeys.isEmpty {
                form.section("大小随缩放的变化", key: prefix + ".scaling", expanded: false)
                addFields(sizeKeys)
            }
            return
        }
        if category.usesSymbolVisibility {
            let icons = category == .icons
            form.section(icons ? "地点图标" : "地点名称", key: prefix + ".places")
            let generation = epoch
            for (group, title) in MapSymbolCategory.poiGroups {
                let item = group + (icons ? "_icon" : "_icon_text")
                let toggle = form.field(title, ["kind": "toggle"], object(snapshot["labelChildren"])[item] ?? false) { [weak self, weak form] value in
                    guard let self, let form, self.context == "map", self.epoch == generation, self.sidebarForm === form else { return }
                    self.update("setLabelChild", ["key": item, "enabled": value])
                }
                toggle.view.identifier = NSUserInterfaceItemIdentifier("label." + item)
                toggle.readValue = { [weak self] in object(self?.snapshot["labelChildren"])[item] }
                toggle.enabledWhen = { [weak self] in
                    guard let self, self.defaultVisibility(category.title) else { return false }
                    if icons { return true }
                    return self.defaultVisibility(MapSymbolCategory.icons.title)
                        && object(self.snapshot["labelChildren"])[group + "_icon"] as? Bool == true
                }
            }
            if !icons { form.add(settingsHelp("地点名称跟随对应图标显示；隐藏图标时保留名称设置。")) }
            form.section("站点", key: prefix + ".stations")
            addFields([icons ? "transport.stations" : "transport.station_names"])
        }
        if category == .roadText || category == .iconText {
            form.section("名称来源", key: prefix + ".sources")
            addFields([category == .roadText ? "road.game_names" : "poi.game_names"])
        }
    }
    func addSchemaField(form:FormView,key:String,spec:JSON,value:Any,splitComponents:Bool = true) {
        var spec = spec
        if ["terrain.shade", "terrain.sun"].contains(key) { spec["slider"] = true }
        let firstField = form.fields.count
        let generation = epoch
        if let pair=value as? JSON, Set(pair.keys).isSubset(of:["fixed","scaled"]), splitComponents {
            var pairDraft = pair
            for child in ["fixed","scaled"] where pair[child] != nil {
                var componentSpec = spec; componentSpec["low"] = 0
                let field = form.field(textValue(spec["label"])+" · "+localized(child == "fixed" ? "固定" : "缩放"),componentSpec,pair[child]!) { [weak self, weak form] value in
                    guard let self=self, self.context == "map", self.epoch == generation, form != nil, self.sidebarForm === form else{return};pairDraft[child]=value;self.update("setField",["key":key,"value":pairDraft])
                }
                field.readValue = { [weak self] in
                    guard let self = self else { return nil }
                    let current = object(object(self.snapshot["fields"])[key])
                    pairDraft = current
                    return current[child]
                }
                field.view.identifier = NSUserInterfaceItemIdentifier(key + "." + child)
            }
        } else {
            let pair = value as? JSON
            let displayed: Any
            if let pair = pair { displayed = (pair["fixed"] as? Double ?? 0) + (pair["scaled"] as? Double ?? 0) }
            else { displayed = value }
            let field = form.field(textValue(spec["label"]),spec,displayed) { [weak self, weak form] value in
                guard let self = self, self.context == "map", self.epoch == generation, form != nil, self.sidebarForm === form else { return }
                let current = object(self.snapshot["fields"])[key] ?? value
                self.update("setField",["key":key,"value":pair == nil ? value : resizedAppearanceValue(current, to: referenceSize(value))])
            }
            field.view.identifier = NSUserInterfaceItemIdentifier(key)
            field.readValue = { [weak self] in
                let current = object(self?.snapshot["fields"])[key]
                if let pair = current as? JSON { return (pair["fixed"] as? Double ?? 0) + (pair["scaled"] as? Double ?? 0) }
                return current
            }
        }
        let deps=object(snapshot["fieldDependencies"])[key] as? [String] ?? []
        for field in form.fields.dropFirst(firstField) {
            field.enabledWhen = { [weak self] in
                guard let self = self, self.context == "map", self.epoch == generation else { return false }
                if key.hasPrefix("satellite."), self.snapshot["satelliteAvailable"] as? Bool != true { return false }
                guard deps.allSatisfy({object(self.snapshot["layers"])[$0] as? Bool != false}) else { return false }
                if ["terrain.shade", "terrain.sun"].contains(key) { return textValue(object(self.snapshot["fields"])["terrain.mode"]) == "shade" }
                if key == "transport.distance" { return object(self.snapshot["fields"])["transport.merge"] as? Bool == true }
                if key == "transport.diameter" { return object(self.snapshot["fields"])["transport.stations"] as? Bool == true }
                if key == "transport.station_names" || key == "poi.game_names" { return self.defaultVisibility(MapSymbolCategory.iconText.title) }
                if key == "road.game_names" { return self.defaultVisibility(MapSymbolCategory.roadText.title) }
                return true
            }
        }
    }
    func buildInspector() {
        rightContent.subviews.forEach { $0.removeFromSuperview() }
        let form = FormView()
        form.sectionState = sectionState
        inspectorForm = form
        pin(form, in: rightContent)
        let hasCity = snapshot["hasDocument"] as? Bool == true && (snapshot["documentEpoch"] as? Int) == epoch
        panelHeader(rightHeader, title: hasCity ? "城市概览" : "对象属性", inspector: true)
        if hasCity {
            let cityName = textValue(object(snapshot["cityInfo"])["name"])
            form.add(label(cityName, 20, .semibold))
            let stats = object(snapshot["stats"])
            let facts = [("建筑", "buildings", 1.0, ""), ("道路", "roadMeters", 1000, " km"),
                ("公共交通", "transitMeters", 1000, " km"), ("建成区", "builtArea", 1_000_000, " km²")]
            for (title, key, unit, suffix) in facts {
                guard let number = stats[key] as? NSNumber else { continue }
                let value = (number.doubleValue / unit).formatted(.number.precision(.fractionLength(suffix.isEmpty ? 0 : 1))) + suffix
                let name = label(title, 13, .regular, .secondaryLabelColor)
                let amount = label(value, 13, .medium)
                amount.alignment = .right
                form.add(row([name, NSView(), amount]))
            }
            form.add(rule())
        }
        form.add(label("未选择对象", 13, .medium))
        form.add(settingsHelp("点选地图上的对象以查看属性。"))
    }
    func showObject(_ selection:JSON) async throws {
        let generation=epoch
        let model=try await engine.request("getObjectInspector",["object":selection,"context":context,"documentEpoch":epoch])
        guard epoch==generation, textValue(selectedObject?["featureId"])==textValue(selection["featureId"]), textValue(selectedObject?["objectType"])==textValue(selection["objectType"]) else{return}
        rightContent.subviews.forEach{$0.removeFromSuperview()};let form=FormView();form.sectionState=sectionState;inspectorForm=form;pin(form,in:rightContent)
        panelHeader(rightHeader,title:"对象属性",inspector:true)
        form.add(label(textValue(model["title"] ?? "对象属性"),16,.semibold))
        for fact in array(model["summary"]) { form.add(label(textValue(fact["label"])+"："+textValue(fact["value"]),12)) }
        for group in array(model["sections"]) {
            form.section(textValue(group["label"] ?? group["title"]),key:"inspector.\(textValue(selection["objectType"])).\(textValue(group["key"]))")
            let scope=NSTextField(wrappingLabelWithString:textValue(group["scope"]))
            scope.font = .systemFont(ofSize:11);scope.textColor = .secondaryLabelColor;form.add(scope)
            for field in array(group["fields"]) {
                var spec=field;if spec["kind"] as? String=="font"{spec["choices"]=model["fonts"]}
                form.field(textValue(field["label"]),spec,field["value"] ?? "") { [weak self] value in self?.run {
                    guard let self=self,!self.busy,self.epoch==generation else{return}
                    _=try await self.engine.request("setInspectorField",["object":selection,"context":self.context,"documentEpoch":generation,"settingsRevision":model["settingsRevision"] ?? "","key":field["key"] ?? "","value":value])
                    try await self.refreshSnapshot();_=try await self.engine.request("refreshPreview");try await self.applySceneState();try await self.showObject(selection)
                } }
                (form.fields.last?.view as? NSControl)?.isEnabled = field["enabled"] as? Bool ?? true
            }
        }
        if !array(model["details"]).isEmpty { form.section("详细信息",key:"inspector.details") }
        for fact in array(model["details"]) { form.add(label(textValue(fact["label"])+"："+textValue(fact["value"]),12)) }
    }
    func showRegions(_ group: String) {
        let plural = group == "district" ? "districts" : "parks"
        let identifier = NSUserInterfaceItemIdentifier("regions." + plural)
        if let existing = panels.first(where: { $0.identifier == identifier && $0.isVisible }) {
            existing.makeKeyAndOrderFront(nil); return
        }
        guard engineReady, context == "map", !busy, !fieldWriting, fieldQueue.isEmpty,
              snapshot["hasDocument"] as? Bool == true, !openingRegionSelector else { return }
        let generation = epoch
        openingRegionSelector = true
        run { [self] in
            defer { openingRegionSelector = false }
            let catalog = try await engine.request("getRegionCatalog")
            guard engineReady, epoch == generation, context == "map", !busy else { return }
            let selector = RegionSelector(title: group == "district" ? "行政区划" : "功能区划",
                items: array(catalog[plural]), selected: Set(catalog[group + "_ids"] as? [Int] ?? []))
            selector.window.identifier = identifier
            selector.unavailableReason = { [weak self] in
                guard let self = self else { return localized("引擎未就绪，请稍后重试。") }
                if self.epoch != generation { return localized("城市已切换，请重新打开区域选择。") }
                if !self.engineReady { return localized("引擎未就绪，请稍后重试。") }
                if self.context != "map" { return localized("请返回普通地图后应用区域选择。") }
                if self.busy || self.fieldWriting || !self.fieldQueue.isEmpty {
                    return localized("请等待当前操作完成后再修改区域选择。")
                }
                return nil
            }
            selector.apply = { [weak self] selector in
                guard let self = self else { selector.window.saving = false; selector.refreshAvailability(); return }
                let ids = selector.selected.sorted()
                self.run {
                    defer { selector.window.saving = false; selector.refreshAvailability() }
                    if let reason = selector.unavailableReason?() { throw AppFailure(message: reason) }
                    _ = try await self.engine.request("setRegionSelection", ["group": plural,
                        "ids": ids, "documentEpoch": generation])
                    selector.window.dirty = false; selector.window.close()
                    guard self.epoch == generation else { return }
                    try await self.refreshSnapshot()
                    guard self.epoch == generation, self.context == "map" else { return }
                    let result = try await self.engine.request("refreshPreview")
                    if result["started"] as? Bool != true, self.epoch == generation, self.context == "map" {
                        try await self.applySceneState()
                    }
                }
            }
            selector.window.retained.append(selector)
            selector.window.onClose = { [weak self, weak selector] in
                guard let selector = selector else { return }
                self?.panels.removeAll { $0 === selector.window }
                selector.window.retained.removeAll()
            }
            panels.append(selector.window); selector.refreshAvailability(); selector.show()
        }
    }
}
