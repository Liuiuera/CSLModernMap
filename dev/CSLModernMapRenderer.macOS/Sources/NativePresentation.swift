import Foundation

enum NativePresentation {
    static let languages = ["zh-CN": "简体中文", "zh-TW": "繁體中文", "en": "English", "fr": "Français", "de": "Deutsch",
                            "es": "Español", "pl": "Polski", "pt-BR": "Português (Brasil)", "ru": "Русский", "ko": "한국어", "ja": "日本語", "it": "Italiano"]
    static let names = [
        "game": "游戏原色", "classic": "经典配色", "soft": "柔和配色",
        "modern": "浅色", "modern-dark": "深色", "all": "全部", "custom": "仅自定义名称", "none": "隐藏",
        "hidden": "隐藏", "station": "站点旁", "icon": "跟随图标", "auto": "自动",
        "left_top": "左上角", "right_top": "右上角", "left_bottom": "左下角", "right_bottom": "右下角",
        "br": "右下角", "bl": "左下角", "left": "左侧", "right": "右侧", "standard": "标准",
        "Metro": "地铁", "Train": "火车", "Monorail": "单轨", "Tram": "电车", "Bus": "公交",
        "Trolleybus": "无轨电车", "CableCar": "缆车", "Ship": "渡轮", "Ferry": "渡轮", "Airplane": "飞机",
        "Helicopter": "直升机", "Blimp": "飞艇", "train": "火车", "tram": "电车", "metro": "地铁",
        "monorail": "单轨", "cablecar": "缆车", "cn_icons": "现代地图图标库",
        "palette": "线路配色", "types": "交通类型", "schematic_water": "示意水域",
        "light_outline": "浅色线路描边", "title": "地图标题", "legend": "图例",
        "facilities": "接驳设施", "facility_hierarchy": "设施层级", "annotations": "线路注记",
        "road": "道路", "district": "行政区划", "building": "建筑", "park": "功能区", "stop": "站点",
        "highway": "高速公路", "main": "主干道路", "branch": "支路", "special": "特殊道路", "beautification": "景观道路"
    ]
    static func identifier(_ key: String) -> String {
        if let name = names[key] { return localized(name) }
        for (suffix, title) in [("_elevated", "高架"), ("_tunnel", "隧道")] where key.hasSuffix(suffix) {
            return identifier(String(key.dropLast(suffix.count))) + " · " + localized(title)
        }
        return key.replacingOccurrences(of: "([a-z])([A-Z])", with: "$1 $2", options: .regularExpression)
            .replacingOccurrences(of: "_", with: " ").capitalized
    }
    static func choice(_ item: JSON) -> String {
        let value = textValue(item["value"]), title = textValue(item["label"])
        if title.isEmpty || title == value {
            return names[value].map { localized($0) } ?? value
        }
        return localized(title)
    }
    static func operation(_ method: String) -> String {
        if method.contains("Theme") { return localized("主题编辑") }
        if method.contains("TextIcon") { return localized("文字与图标设置") }
        if method.contains("Poi") { return localized("图标映射设置") }
        if method.contains("Transit") { return localized("交通设置读取") }
        if method.contains("Preview") { return localized("地图预览") }
        return localized("地图处理")
    }
    static func errorMessage(_ text: String) -> String {
        if text == "Built-in theme files cannot be overwritten. Choose another location." {
            return localized("不能覆盖内置主题文件，请选择其他位置。")
        }
        let pattern = #"\b(?:gui|style)\.[A-Za-z0-9_.]*[A-Za-z0-9_]"#
        guard let expression = try? NSRegularExpression(pattern: pattern) else { return localized(text) }
        var result = text
        for match in expression.matches(in: text, range: NSRange(text.startIndex..., in: text)).reversed() {
            if let range = Range(match.range, in: result) { result.replaceSubrange(range, with: localized(String(result[range]))) }
        }
        return localized(result)
    }
    static func themeSpec(_ key: String, _ spec: JSON) -> JSON {
        var spec = spec
        let values: [String]
        if key == "transports.station.names.mode" { values = ["all", "custom", "none"] }
        else if ["transports.legend.position", "transports.label.position", "transports.annotations.position"].contains(key) {
            values = ["left_top", "right_top", "left_bottom", "right_bottom"]
        } else { return spec }
        spec["kind"] = "choice"
        spec["choices"] = values.map { ["value": $0, "label": identifier($0)] }
        return spec
    }
    static func themeTitle(_ key: String, fields: JSON) -> String {
        let parts = key.components(separatedBy: ".")
        func name(_ path: String) -> String {
            if path == "transports.station.names.mode" { return localized("站名显示范围") }
            if path == "procedural_objects.border" { return localized("对象轮廓颜色") }
            if path == "procedural_objects.border_width" { return localized("对象轮廓宽度") }
            let leaf = path.components(separatedBy: ".").last!
            if let index = Int(leaf) { return String(format: localized("第 %d 项"), index + 1) }
            let title = textValue(object(fields[path])["label"])
            return title.isEmpty || title == leaf ? identifier(leaf) : localized(title)
        }
        var titles = parts.count > 2 ? (2..<parts.count).map { name(parts.prefix($0).joined(separator: ".")) } : []
        titles.append(name(key))
        let spec = object(fields[key]), unit = textValue(spec["unit"])
        let units = ["px": "参考像素", "meter": "米", "degree": "度", "ratio": "比例", "alpha255": "0–255",
                     "count": "数量", "samples": "采样点", "priority": "优先级", "meter_to_pixel": "米→像素"]
        var title = titles.joined(separator: " · ")
        let hasPixels = unit == "px" && (title.contains("像素") || title.lowercased().contains("px"))
        if let unitTitle = units[unit], !hasPixels, !title.contains(localized(unitTitle)), !title.contains("(m)") {
            title += "（" + localized(unitTitle) + "）"
        }
        return title
    }
}
