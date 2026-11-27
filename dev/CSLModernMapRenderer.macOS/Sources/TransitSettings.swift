import AppKit

enum TransitSettingsLayout {
    static let publicFields: Set<String> = ["layers", "line_label_enabled", "line_width", "line_color_mode", "line_label_style", "line_label_size", "line_label_interval",
        "facilities_enabled", "stations", "facility_icon_size", "station_diameter", "station_name_placement", "station_name_size", "merge_enabled", "merge_range", "base_road", "ordinary_pois"]
    static let labelStyles = [("solid-sign", "轻量实色"), ("underline-sign", "下沿色带"), ("side-sign", "白底色签"),
                              ("split-sign", "图标分段"), ("frame-sign", "细框路牌")]
    static let metroFields: Set<String> = ["merge_enabled", "merge_range", "spacing", "schematic_options", "schematic_water", "water_smoothing", "facilities_enabled"]
    static func allows(_ key: String, child: String?, metro: Bool) -> Bool {
        guard (metro ? metroFields : publicFields).contains(key) else { return false }
        if key == "layers" { return !metro && child == "lines" }
        if key == "schematic_options" { return metro && ["adaptive_scale", "adaptive_strength"].contains(child ?? "") }
        return child == nil
    }
}

@MainActor final class TransitTypeChoices: NSStackView {
    private var fullWidths: [NSLayoutConstraint] = []
    private var halfWidths: [NSLayoutConstraint] = []
    private var heights: [(NSButton, NSLayoutConstraint)] = []
    func addPair(_ controls: [NSView]) {
        let pair = row(controls, gap: 8)
        addArrangedSubview(pair); pair.widthAnchor.constraint(equalTo: widthAnchor).isActive = true
        for control in controls {
            fullWidths.append(control.widthAnchor.constraint(equalTo: pair.widthAnchor))
            halfWidths.append(control.widthAnchor.constraint(equalTo: pair.widthAnchor, multiplier: 0.5, constant: -4))
            if let button = control as? NSButton {
                let height = button.heightAnchor.constraint(greaterThanOrEqualToConstant: 22); height.isActive = true
                heights.append((button, height))
                button.cell?.wraps = true; button.cell?.lineBreakMode = .byWordWrapping; button.alignment = .left
            }
        }
        needsLayout = true
    }
    override func layout() {
        let width = bounds.width > 0 ? bounds.width : 300
        let longest = heights.map { ($0.0.title as NSString).size(withAttributes: [.font: $0.0.font ?? NSFont.systemFont(ofSize: 13)]).width + 28 }.max() ?? 0
        let vertical = width < max(230, longest * 2 + 8)
        // Wrap long labels into a single column.
        if (vertical ? fullWidths : halfWidths).first?.isActive != true {
            NSLayoutConstraint.deactivate(vertical ? halfWidths : fullWidths)
            NSLayoutConstraint.activate(vertical ? fullWidths : halfWidths)
        }
        for case let pair as NSStackView in arrangedSubviews {
            if pair.orientation != (vertical ? .vertical : .horizontal) {
                pair.orientation = vertical ? .vertical : .horizontal
                pair.alignment = vertical ? .leading : .centerY
                pair.distribution = .fill
            }
        }
        for (button, height) in heights {
            let textWidth = max(30, (vertical ? width : (width - 8) / 2) - 28)
            let rect = (button.title as NSString).boundingRect(with: NSSize(width: textWidth, height: .greatestFiniteMagnitude),
                options: [.usesLineFragmentOrigin, .usesFontLeading], attributes: [.font: button.font ?? NSFont.systemFont(ofSize: 13)])
            let desired = max(22, ceil(rect.height) + 6)
            if height.constant != desired { height.constant = desired }
        }
        super.layout()
    }
}

extension InterfaceController {
    func loadTransitSettings() async throws {
        guard ["transit", "metro"].contains(context) else { return }
        let target = context, generation = epoch, request = UUID()
        sidebarNavigation.requestID = request
        let schema = try await engine.request("getTransitSchema", ["metro": target == "metro"])
        guard context == target, epoch == generation, sidebarNavigation.requestID == request else { return }
        let state = try await engine.request("getSnapshot")
        guard context == target, epoch == generation, sidebarNavigation.requestID == request else { return }
        applySnapshot(state); transitSchema = schema; renderTransitSettings()
    }
    func renderTransitSettings() {
        guard ["transit", "metro"].contains(context) else { return }
        let metro = context == "metro"
        let pageKey = metro ? "metro.generation" : "transit." + sidebarNavigation.transitPage
        prepareSidebar(pageKey); selectedLayer = nil
        let form = FormView(); form.sectionState = sectionState; transitForm = form
        panelHeader(leftHeader, title: metro ? "地铁生成设置" : "公共交通设置")
        let navigation: NSView
        if metro {
            navigation = metroGenerationControls()
        } else {
            navigation = settingsNavigation(SidebarLayout.transitPages, selected: sidebarNavigation.transitPage) { [weak self] page in
                guard let self = self, self.context == "transit" else { return }
                self.sidebarNavigation.transitPage = page; self.renderTransitSettings()
            }
        }
        installSidebarForm(form, navigation: navigation)
        transitSelectionSummary = nil
        if metro { buildMetroSettings(form) } else { buildPublicTransitSettings(form) }
        finishSidebar(form, key: pageKey)
    }
    func currentTransitOptions(_ metro: Bool) -> JSON { object(snapshot[metro ? "metroOptions" : "transitOptions"]) }
    func addTransitField(_ form: FormView, key: String, title: String, child: String? = nil,
                         choices: [(String, String)] = [], multiplier: Double = 1,
                         enabled: @escaping (JSON) -> Bool = { _ in true }) {
        let metro = context == "metro", generation = epoch, target = context, options = currentTransitOptions(context == "metro")
        guard TransitSettingsLayout.allows(key, child: child, metro: metro), var spec = transitSchema[key] as? JSON,
              let value = child.flatMap({ object(options[key])[$0] }) ?? (child == nil ? options[key] : nil) else { return }
        if child != nil { spec = ["kind": isBoolean(value) ? "toggle" : "number", "low": 0, "high": 1] }
        if !choices.isEmpty { spec["kind"] = "choice"; spec["choices"] = choices.map { ["value": $0.0, "label": localized($0.1)] } }
        if textValue(spec["kind"]) == "font" { spec["choices"] = snapshot["fonts"] }
        let displayed: Any = multiplier != 1 ? (value as? Double ?? 0) * multiplier : value
        if multiplier != 1 { spec["low"] = (spec["low"] as? Double ?? 0) * multiplier; spec["high"] = (spec["high"] as? Double ?? 1) * multiplier }
        if ["line_width", "facility_icon_size", "station_name_size", "spacing", "water_smoothing"].contains(key) || child == "adaptive_strength" { spec["slider"] = true }
        if key == "line_width" { spec["sliderMaximum"] = 20 }
        let field = form.field(title, spec, displayed) { [weak self, weak form] changed in
            guard let self = self, let form = form, self.context == target, self.epoch == generation, self.transitForm === form,
                  self.engineReady, !self.busy, !metro || !self.rmp.busy else { return }
            let value: Any = multiplier != 1 ? (changed as? Double ?? 0) / multiplier : changed
            let patch: Any
            if let child = child { patch = [child: value] } else { patch = value }
            self.update("setTransitOption", ["metro": metro, "draft": self.cityDraft != nil, "key": key, "value": patch], refresh: !metro)
        }
        field.view.identifier = NSUserInterfaceItemIdentifier((metro ? "metro." : "transit.") + key + (child.map { "." + $0 } ?? ""))
        if key == "line_label_style", let popup = field.popup {
            for (item, choice) in zip(popup.itemArray, choices) {
                let image = resourceImage(choice.0, folder: "TransitLabels")
                image?.size = NSSize(width: 56, height: 22.4)
                item.image = image
            }
        }
        field.readValue = { [weak self] in
            guard let self = self else { return nil }
            let options = self.currentTransitOptions(metro)
            let value = child.flatMap { object(options[key])[$0] } ?? (child == nil ? options[key] : nil)
            return multiplier != 1 ? (value as? Double ?? 0) * multiplier : value
        }
        field.enabledWhen = { [weak self] in
            guard let self = self, self.context == target, self.epoch == generation else { return false }
            return enabled(self.currentTransitOptions(metro)) && (!metro || !self.rmp.busy)
        }
    }
    func addTransitTypes(_ form: FormView) {
        let metro = context == "metro", target = context, generation = epoch
        var seen = Set<String>()
        let values = array(snapshot["transitTypes"]).filter { let key = textValue($0["key"]); return !key.isEmpty && seen.insert(key).inserted }
            .sorted { TransitTypePresentation.precedes(textValue($0["key"]), textValue($1["key"])) }
        let grid = TransitTypeChoices(); grid.orientation = .vertical; grid.alignment = .leading; grid.spacing = 8
        grid.translatesAutoresizingMaskIntoConstraints = false
        for start in stride(from: 0, to: values.count, by: 2) {
            let controls = values[start..<min(start + 2, values.count)].map { item -> NSView in
                let key = textValue(item["key"]), title = TransitTypePresentation.title(textValue(item["key"]), types: values)
                let field = FieldControl(title, schema: ["kind": "toggle"], value: (currentTransitOptions(metro)["types"] as? [String] ?? []).contains(key)) { [weak self, weak form] value in
                    guard let self = self, let form = form, self.context == target, self.epoch == generation, self.transitForm === form,
                          self.engineReady, !self.busy, !metro || !self.rmp.busy else { return }
                    self.update("setTransitType", ["key": key, "enabled": value, "metro": metro, "context": !metro, "draft": self.cityDraft != nil], refresh: !metro)
                }
                field.view.identifier = NSUserInterfaceItemIdentifier((metro ? "metro.type." : "transit.type.") + key)
                field.readValue = { [weak self] in (self?.currentTransitOptions(metro)["types"] as? [String] ?? []).contains(key) }
                field.enabledWhen = { [weak self] in self?.context == target && self?.epoch == generation && (!metro || self?.rmp.busy == false) }
                (field.view as? NSButton)?.cell?.wraps = true
                field.view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
                form.fields.append(field); return field.view
            }
            grid.addPair(controls)
        }
        form.add(grid)
    }
    func addLineSelection(_ form: FormView) {
        let metro = context == "metro"
        let choose = ActionButton("选择线路…") { [weak self] in self?.showLines(metro: metro) }
        choose.image = symbol("line.3.horizontal.decrease.circle", size: 14); choose.imagePosition = .imageLeading; choose.toolTip = choose.title
        choose.enabledWhen = { [weak self] in self?.context == (metro ? "metro" : "transit") && (!metro || self?.rmp.busy == false) }
        form.add(choose)
        let summary = settingsHelp(""); transitSelectionSummary = summary; form.add(summary); addTransitTypes(form)
    }
    func refreshLineSelectionSummary() {
        for panel in panels.compactMap({ $0 as? EditorWindow }) {
            if let selector = panel.retained.first as? TransitLineSelector {
                selector.synchronizeEnabledTypes(Set(currentTransitOptions(selector.metro)["types"] as? [String] ?? []))
                selector.refreshAvailability()
            }
        }
        guard context == "transit" || context == "metro" else { return }
        let summary = object(object(snapshot["lineSelection"])[context])
        let types = (summary["types"] as? [String] ?? []).sorted(by: TransitTypePresentation.precedes)
            .map { TransitTypePresentation.title($0, types: array(snapshot["transitTypes"])) }
        transitSelectionSummary?.stringValue = String(format: localized("已选 %d 条 / 可用 %d 条"), summary["selected"] as? Int ?? 0, summary["total"] as? Int ?? 0) +
            (types.isEmpty ? "" : " · " + types.joined(separator: "、"))
    }
    func buildPublicTransitSettings(_ form: FormView) {
        switch sidebarNavigation.transitPage {
        case "stations":
            form.section("站点符号", key: "transit.stations.symbols")
            addTransitField(form, key: "facilities_enabled", title: "交通图标")
            addTransitField(form, key: "stations", title: "站点圆圈")
            addTransitField(form, key: "facility_icon_size", title: "图标大小（参考像素）", enabled: { $0["facilities_enabled"] as? Bool == true })
            addTransitField(form, key: "station_diameter", title: "圆圈直径（参考像素）", enabled: { $0["stations"] as? Bool == true })
            form.section("站名标注", key: "transit.stations.names")
            addTransitField(form, key: "station_name_placement", title: "名称位置", choices: [("hidden", "隐藏"), ("station", "站点旁"), ("icon", "跟随图标")])
            addTransitField(form, key: "station_name_size", title: "站名字号（参考像素）", enabled: { textValue($0["station_name_placement"]) != "hidden" })
            form.add(settingsHelp("跟随图标时，隐藏图标也会隐藏名称。"))
            let appearance = ActionButton("图标旁名称的文字样式…") { [weak self] in self?.showTextIconEditor(page: "style") }
            appearance.enabledWhen = { [weak self] in textValue(self?.currentTransitOptions(false)["station_name_placement"]) == "icon" }
            form.add(appearance)
            form.section("邻近站点圆圈", key: "transit.stations.merge", expanded: false)
            addTransitField(form, key: "merge_enabled", title: "合并邻近站点")
            addTransitField(form, key: "merge_range", title: "合并距离（米）", enabled: { $0["merge_enabled"] as? Bool == true })
            form.add(settingsHelp("只合并站点圆圈，不改变线路站点数据。"))
        case "base":
            form.section("地图背景", key: "transit.base.background")
            addTransitField(form, key: "base_road", title: "显示道路")
            form.section("非交通地点", key: "transit.base.places")
            addTransitField(form, key: "ordinary_pois", title: "地点图标", choices: [("hidden", "隐藏"), ("dim", "随底图淡化"), ("visible", "正常显示")])
            form.add(settingsHelp("医院、学校、商店等建筑。"))
            form.add(textIconButtons())
        default:
            form.section("线路显示", key: "transit.lines.display")
            addTransitField(form, key: "layers", title: "显示线路", child: "lines")
            addTransitField(form, key: "line_label_enabled", title: "线路名称")
            addTransitField(form, key: "line_width", title: "线路粗细（参考像素）", enabled: { object($0["layers"])["lines"] as? Bool != false })
            addTransitField(form, key: "line_color_mode", title: "线路颜色", choices: [("game", "游戏原色"), ("classic", "经典配色"), ("soft", "柔和配色")])
            form.section("线路名称", key: "transit.lines.names", expanded: false)
            addTransitField(form, key: "line_label_style", title: "标签样式", choices: TransitSettingsLayout.labelStyles,
                            enabled: { $0["line_label_enabled"] as? Bool == true })
            addTransitField(form, key: "line_label_size", title: "标签字号（参考像素）", enabled: { $0["line_label_enabled"] as? Bool == true })
            addTransitField(form, key: "line_label_interval", title: "重复间隔（像素）", enabled: { $0["line_label_enabled"] as? Bool == true })
            form.section("线路与交通类型", key: "transit.lines.selection"); addLineSelection(form)
        }
    }
    private func metroGenerationControls() -> NSView {
        let status = settingsHelp("")
        let generate = ActionButton("生成示意图") { [weak self] in
            guard let self, self.context == "metro", self.canGenerateMetro() else { return }
            self.run { try await self.rmp.generate() }
        }
        metroSettingsStatus = status
        metroGenerateButton = generate
        let controls = column([status, generate], gap: 6)
        status.widthAnchor.constraint(equalTo: controls.widthAnchor).isActive = true
        generate.widthAnchor.constraint(lessThanOrEqualTo: controls.widthAnchor).isActive = true
        return controls
    }
    func buildMetroSettings(_ form: FormView) {
        form.section("交通类型与线路", key: "metro.generation.selection"); addLineSelection(form)
        form.section("站点合并", key: "metro.generation.merge")
        addTransitField(form, key: "merge_enabled", title: "合并相邻站点")
        addTransitField(form, key: "merge_range", title: "合并距离（米）", enabled: { $0["merge_enabled"] as? Bool == true })
        form.section("示意布局", key: "metro.generation.layout")
        addTransitField(form, key: "spacing", title: "站间距")
        addTransitField(form, key: "schematic_options", title: "自适应布局", child: "adaptive_scale")
        form.section("布局详细设置", key: "metro.generation.adaptive", expanded: false)
        addTransitField(form, key: "schematic_options", title: "布局调整强度（%）", child: "adaptive_strength", multiplier: 100,
                        enabled: { object($0["schematic_options"])["adaptive_scale"] as? Bool == true })
        form.section("背景与设施", key: "metro.generation.background")
        addTransitField(form, key: "schematic_water", title: "保留水域")
        addTransitField(form, key: "facilities_enabled", title: "显示接驳设施")
        form.section("水域详细设置", key: "metro.generation.water", expanded: false)
        addTransitField(form, key: "water_smoothing", title: "岸线平滑强度", enabled: { $0["schematic_water"] as? Bool == true })
        form.section("操作", key: "metro.generation.actions")
        let reset = ActionButton("恢复生成默认设置") { [weak self] in self?.update("resetMetroGenerationOptions", [:], refresh: false) }
        reset.image = symbol("arrow.counterclockwise", size: 14); reset.imagePosition = .imageLeading; reset.toolTip = reset.title
        form.add(reset)
    }
}
