import AppKit

struct LayerSettingsGroup {
    let id: String
    let title: String
    let fields: [String]
    var expanded = true
}

enum MapSymbolCategory: String, CaseIterable {
    case iconText = "poi_text"
    case roadText = "road_text"
    case districtText = "district_text"
    case icons = "poi"

    var title: String {
        switch self {
        case .iconText: return "图标文字"
        case .roadText: return "道路文字"
        case .districtText: return "区划文字"
        case .icons: return "图标"
        }
    }

    var symbolName: String {
        switch self {
        case .iconText: return "textformat"
        case .roadText: return "road.lanes"
        case .districtText: return "map"
        case .icons: return "mappin.and.ellipse"
        }
    }

    var visibilityKey: String {
        switch self {
        case .roadText: return "label.roads"
        case .districtText: return "label.districts"
        default: return rawValue
        }
    }

    var textStyleKinds: [String] {
        switch self {
        case .iconText: return ["building", "park", "stop"]
        case .roadText: return ["road"]
        case .districtText: return ["district"]
        case .icons: return []
        }
    }

    var isNameLayer: Bool { self == .roadText || self == .districtText }
    var usesSymbolVisibility: Bool { !isNameLayer }

    var visibilityTitle: String {
        switch self {
        case .iconText: return "显示图标文字"
        case .roadText: return "显示道路名称"
        case .districtText: return "显示行政区划名称"
        case .icons: return "显示地点图标"
        }
    }

    static let poiGroups = [("transit", "交通地点"), ("facility", "设施"), ("ordinary", "普通地点"), ("area", "功能区")]
    static let textStyleAttributes = ["font", "size", "color", "outline", "stroke_width", "stroke_color"]
    static let poiNameFields = ["poi.font", "poi.name_size", "poi.name_stroke", "poi.name_stroke_width"]
    static let poiIconFields = ["poi.set", "poi.size"]

    static func textStyleTitle(_ kind: String) -> String {
        switch kind {
        case "road": return "道路名称"
        case "district": return "行政区划名称"
        case "building": return "建筑名称"
        case "park": return "功能区名称"
        default: return "站点名称"
        }
    }

    static func textStyleFields(_ kind: String) -> [String] {
        textStyleAttributes.map { "label.\($0).\(kind)" }
    }

    var styleFields: [String] {
        switch self {
        case .icons: return Self.poiIconFields
        case .iconText: return Self.poiNameFields + textStyleKinds.flatMap(Self.textStyleFields)
        default: return textStyleKinds.flatMap(Self.textStyleFields)
        }
    }
}

// Keep sidebar order independent of schema order.
enum SidebarLayout {
    static let transitPages = [("lines", "线路"), ("stations", "站点"), ("base", "底图")]
    static let labelPages = [("display", "显示内容"), ("style", "外观"), ("merge", "合并与避让")]
    static let metroGenerationKeys = ["types", "line_ids", "merge_enabled", "merge_range", "spacing", "schematic_water", "water_smoothing", "facilities_enabled"]

    static func layerGroups(_ layer: String) -> [LayerSettingsGroup] {
        switch layer {
        case "road":
            return [LayerSettingsGroup(id: "overall", title: "整体设置", fields: ["road.tunnel", "road.bridge", "road.names", "road.accurate_intersections"])] +
                [("highway", "高速公路"), ("main", "主干道路"), ("branch", "支路"), ("special", "特殊道路"), ("quay", "码头道路"), ("beautification", "景观道路")].map { key, title in
                    LayerSettingsGroup(id: key, title: title, fields: ["fill", "border", "line_width", "footprint_scale", "border_width"].map { "road.level.\(key).\($0)" }, expanded: false)
                }
        case "poi": return [LayerSettingsGroup(id: "appearance", title: "图标外观", fields: ["poi.set", "poi.size"])]
        case "satellite": return [LayerSettingsGroup(id: "appearance", title: "影像设置", fields: ["satellite.quality", "satellite.preset", "satellite.intensity"])]
        case "terrain": return [
            LayerSettingsGroup(id: "appearance", title: "地形表现", fields: ["terrain.mode", "terrain.color", "terrain.forest"]),
            LayerSettingsGroup(id: "hillshade", title: "山体阴影", fields: ["terrain.shade", "terrain.sun"], expanded: false)]
        case "water": return [LayerSettingsGroup(id: "appearance", title: "水域外观", fields: ["water.color", "water.border", "water.width"])]
        case "building": return [
            LayerSettingsGroup(id: "appearance", title: "建筑着色", fields: ["building.color_mode", "building.default", "building.poi_tint.contrast", "building.border"]),
            LayerSettingsGroup(id: "advanced", title: "高级外观", fields: ["building.shadow"], expanded: false)]
        case "metro": return [
            LayerSettingsGroup(id: "lines", title: "线路与站点", fields: ["transport.width", "transport.stations", "transport.station_names", "transport.diameter"]),
            LayerSettingsGroup(id: "merge", title: "站点合并", fields: ["transport.merge", "transport.distance"], expanded: false)]
        case "railway": return [LayerSettingsGroup(id: "appearance", title: "铁路外观", fields: ["railway.color", "railway.sleepers"])]
        case "built_area": return [LayerSettingsGroup(id: "appearance", title: "建成区外观", fields: ["built_area.color"])]
        case "traffic": return [LayerSettingsGroup(id: "appearance", title: "路况颜色", fields: ["traffic.free", "traffic.slow", "traffic.busy"])]
        case "district": return [LayerSettingsGroup(id: "appearance", title: "边界与填色", fields: ["district.color", "district.width", "district.opacity"])]
        case "zone": return [LayerSettingsGroup(id: "appearance", title: "区域外观", fields: ["zone.color", "zone.opacity"])]
        case "land_use": return [LayerSettingsGroup(id: "appearance", title: "网格外观", fields: ["land_use.opacity"])]
        case "procedural_objects": return [LayerSettingsGroup(id: "appearance", title: "对象外观", fields: ["procedural_objects.color", "procedural_objects.border"])]
        case "watermark": return [LayerSettingsGroup(id: "appearance", title: "水印内容与位置", fields: ["watermark.text", "watermark.position", "watermark.opacity"])]
        default: return []
        }
    }
}

@MainActor final class SidebarNavigation {
    var mapLayer: String?
    var textPages: [MapSymbolCategory: String] = [:]
    var transitPage = "lines"
    var positions: [String: CGFloat] = [:]
    var activeKey = "map.layers"
    var requestID = UUID()
}

extension InterfaceController {
    func installSidebarForm(_ form: FormView, navigation: NSView? = nil) {
        guard let navigation = navigation else { pin(form, in: leftContent); return }
        navigation.translatesAutoresizingMaskIntoConstraints = false
        form.translatesAutoresizingMaskIntoConstraints = false
        leftContent.addSubview(navigation); leftContent.addSubview(form)
        NSLayoutConstraint.activate([
            navigation.topAnchor.constraint(equalTo: leftContent.topAnchor, constant: 8),
            navigation.leadingAnchor.constraint(equalTo: leftContent.leadingAnchor, constant: 12),
            navigation.trailingAnchor.constraint(equalTo: leftContent.trailingAnchor, constant: -12),
            form.topAnchor.constraint(equalTo: navigation.bottomAnchor, constant: 4),
            form.leadingAnchor.constraint(equalTo: leftContent.leadingAnchor),
            form.trailingAnchor.constraint(equalTo: leftContent.trailingAnchor),
            form.bottomAnchor.constraint(equalTo: leftContent.bottomAnchor)])
    }
    func rememberSidebarPosition() {
        if let form = sidebarForm ?? transitForm { sidebarNavigation.positions[sidebarNavigation.activeKey] = form.contentView.bounds.minY }
    }
    func prepareSidebar(_ key: String) {
        rememberSidebarPosition()
        sidebarNavigation.activeKey = key
        sidebarNavigation.requestID = UUID()
        sidebarForm = nil; transitForm = nil
        leftContent.subviews.forEach { $0.removeFromSuperview() }
    }
    func finishSidebar(_ form: FormView, key: String) {
        let position = sidebarNavigation.positions[key] ?? 0
        root.layoutSubtreeIfNeeded()
        form.restoreScroll(position)
        form.onScroll = { [weak self, weak form] offset in
            guard let self = self, let form = form, self.sidebarForm === form || self.transitForm === form else { return }
            self.sidebarNavigation.positions[key] = offset
        }
        updateCommands()
    }
    func restoreMapSidebar() {
        if let key = sidebarNavigation.mapLayer, let name = layerKeys.first(where: { $0.value == key })?.key { showLayer(name) }
        else { buildSidebar() }
    }
    func settingsHelp(_ text: String) -> NSTextField {
        let view = NSTextField(wrappingLabelWithString: localized(text))
        view.font = .systemFont(ofSize: 11); view.textColor = .secondaryLabelColor
        return view
    }
    func settingsNavigation(_ pages: [(String, String)], selected: String, select: @escaping (String) -> Void) -> NSView {
        let current = pages.firstIndex { $0.0 == selected } ?? 0
        return ChoiceSegments(pages.map { $0.1 }, selected: current) { select(pages[$0].0) }
    }
    func metroInputFingerprint() -> String {
        let options = object(snapshot["metroOptions"])
        var inputs: JSON = [:]
        for key in SidebarLayout.metroGenerationKeys { inputs[key] = options[key] ?? NSNull() }
        let schematic = object(options["schematic_options"])
        inputs["schematic_options"] = ["adaptive_scale": schematic["adaptive_scale"] ?? true, "adaptive_strength": schematic["adaptive_strength"] ?? 0.85]
        return (try? jsonString(inputs)) ?? ""
    }
    func canGenerateMetro() -> Bool {
        let summary = object(object(snapshot["lineSelection"])["metro"])
        return engineReady && !busy && !fieldWriting && !rmp.busy && !rmp.placing && fieldQueue.isEmpty && (summary["selected"] as? Int ?? 0) > 0
    }
    func refreshMetroSettingsStatus() {
        guard context == "metro" else { return }
        let text: String
        if rmp.busy { text = "正在生成地铁项目…" }
        else if (object(object(snapshot["lineSelection"])["metro"])["selected"] as? Int ?? 0) == 0 { text = "请选择至少一种交通类型和一条可用线路。" }
        else if let fingerprint = metroAppliedFingerprint, fingerprint != metroInputFingerprint() { text = "设置已修改，当前工程尚未重新生成。" }
        else if rmp.hasProject { text = "生成设置只影响重新生成；外观可在地图编辑器中调整。" }
        else { text = "调整生成输入后，应用到示意图。" }
        metroSettingsStatus?.stringValue = localized(text)
        metroGenerateButton?.title = localized(metroAppliedFingerprint != nil && metroAppliedFingerprint != metroInputFingerprint() ? "应用设置并重新生成" : rmp.hasProject ? "重新生成示意图" : "生成示意图")
        metroGenerateButton?.isEnabled = canGenerateMetro()
    }
}
