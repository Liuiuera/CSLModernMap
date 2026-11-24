import AppKit

@MainActor extension ThemeEditor {
    static let roadStyleLevels = ["highway", "main", "branch", "special", "beautification", "elevated", "tunnel", "quay"]
    func spec(_ key: String) -> JSON {
        let fields = object(draft["fields"])
        let wildcard = key.components(separatedBy: ".").map { Int($0) == nil ? $0 : "*" }.joined(separator: ".")
        var result = NativePresentation.themeSpec(key, object(fields[key] ?? fields[wildcard]))
        if key == "name" || key == "description" { result["supported"] = true }
        return result
    }
    func conditionAllows(_ key: String) -> Bool {
        guard let condition = spec(key)["condition"] as? String else { return true }
        let tokens = condition.split(separator: " ").map(String.init)
        guard tokens.count == 3 else { return true }
        let actual = textValue(pathValue(data, tokens[0].components(separatedBy: ".")))
        let equal = actual == tokens[2]
        return tokens[1] == "==" ? equal : !equal
    }
    func entries() -> [String] {
        var keys: [String] = []
        func walk(_ value: Any, _ key: String) {
            let field = spec(key)
            guard field["hidden"] as? Bool != true else { return }
            if let children = value as? JSON {
                for child in children.keys.sorted() { walk(children[child]!, key + "." + child) }
            } else {
                if field["supported"] as? Bool != false { keys.append(key) }
            }
        }
        for key in data.keys.sorted() { walk(data[key]!, key) }
        return keys
    }
    func matchesSearch(_ key: String) -> Bool {
        let terms = search.stringValue.split(whereSeparator: { $0.isWhitespace })
        let text = NativePresentation.themeTitle(key, fields: object(draft["fields"])) + " " + key
        return terms.allSatisfy { text.localizedCaseInsensitiveContains(String($0)) }
    }
    func rebuild() {
        rebuilding = true; defer { rebuilding = false }
        if !renderedSection.isEmpty { scrollPositions[renderedSection] = form.contentView.bounds.minY }
        if sections.indices.contains(section.indexOfSelectedItem) { selectedSection = sections[section.indexOfSelectedItem] }
        let offset = scrollPositions[selectedSection] ?? 0
        renderedSection = selectedSection
        form.reset(); extraControls.removeAll(); rampBands.removeAll()
        let all = entries().filter {
            let prefix = selectedSection + (selectedStyleDetail.map { "." + $0 } ?? "")
            return (selectedSection == "__basic__" ? !$0.contains(".") : $0.hasPrefix(prefix + ".")) && matchesSearch($0)
        }
        var remaining = Set(all)
        func addGroup(_ title: String, _ prefixes: [String], expanded: Bool = true) {
            let keys = prefixes.flatMap { prefix in all.filter { remaining.contains($0) && ($0 == prefix || $0.hasPrefix(prefix + ".")) } }
            guard !keys.isEmpty else { return }
            form.section(title, key: "theme." + selectedSection + "." + prefixes.joined(separator: "+"), expanded: expanded || !search.stringValue.isEmpty)
            for key in keys { remaining.remove(key); appendField(key) }
        }
        let common: [String: [String]] = [
            "__basic__": ["name", "description"], "canvas": ["background"],
            "terrain": ["mode", "elevation_colors", "hillshade.intensity"],
            "water": ["color", "border", "border_width"], "forest": ["color"],
            "built_area": ["enabled", "color"], "zone": ["enabled", "border", "border_width", "fill", "fill_alpha"],
            "district": ["enabled", "border", "border_width", "fill_alpha", "fill_colors"],
            "land_use": ["enabled", "detail", "alpha", "colors"],
            "buildings": ["color_mode", "default", "border", "border_width"],
            "procedural_objects": ["colors", "border", "border_width"], "railway": ["line", "dash"],
            "traffic": ["free", "slow", "busy"], "transports": ["enabled", "auto_color", "width"],
            "poi_icons": ["icon_size", "name_size", "bg_circle", "bg_circle_size", "font"],
            "watermark": ["enabled", "position", "content"]
        ]
        if selectedSection == "roads" {
            addGroup("整体外观", ["roads.width_scale", "roads.opacity"])
            for level in Self.roadStyleLevels {
                addGroup(NativePresentation.identifier(level), ["roads." + level],
                         expanded: selectedStyleDetail == level || !["elevated", "tunnel", "quay"].contains(level))
            }
        } else if selectedSection == "labels" {
            for type in ["road", "district", "park", "building", "stop"] {
                let prefix = "labels." + type
                addGroup(NativePresentation.identifier(type), ["font", "size", "color", "stroke", "stroke_width"].map { prefix + "." + $0 })
                addGroup(NativePresentation.identifier(type) + " · " + localized("高级设置"), [prefix], expanded: false)
            }
        } else {
            let prefixes = (common[selectedSection] ?? []).map { selectedSection == "__basic__" ? $0 : selectedSection + "." + $0 }
            addGroup("常用设置", prefixes)
            if selectedSection == "terrain" { addGroup("山体光影细节", ["terrain.hillshade"], expanded: false) }
        }
        if !remaining.isEmpty {
            form.section("高级设置", key: "theme." + selectedSection + ".advanced", expanded: !search.stringValue.isEmpty)
            for key in all where remaining.contains(key) { appendField(key) }
        }
        if all.isEmpty { form.add(label("没有匹配的参数。", 12, .regular, .secondaryLabelColor)) }
        restoreButton.title = localized(search.stringValue.isEmpty ? "恢复本分类默认设置" : "恢复筛选参数默认值")
        form.restoreScroll(offset); updateDirty()
    }
    func makeField(_ key: String, _ value: Any, title: String? = nil) -> FieldControl {
        var fieldSpec = spec(key)
        if textValue(fieldSpec["kind"]) == "font" { fieldSpec["choices"] = owner.snapshot["fonts"] }
        let fields = object(draft["fields"])
        let name = title ?? NativePresentation.themeTitle(key, fields: fields)
        let control = FieldControl(name, schema: fieldSpec, value: value) { [weak self] value in
            guard let self = self, self.editable, !self.rebuilding else { return }
            self.change(key, value)
            if ["toggle", "choice", "enum"].contains(textValue(fieldSpec["kind"])) {
                self.refreshAvailability(); self.refreshBands()
            } else if value is [Any] {
                DispatchQueue.main.async { [weak self] in guard let self = self, !self.closed else { return }; self.rebuild() }
            }
        }
        control.view.identifier = NSUserInterfaceItemIdentifier(key)
        let description = textValue(fieldSpec["description"])
        control.view.toolTip = name + (description.isEmpty ? "" : "\n" + localized(description))
        control.readValue = { [weak self] in pathValue(self?.data ?? [:], key.components(separatedBy: ".")) }
        control.enabledWhen = { [weak self] in self?.conditionAllows(key) ?? false }
        control.onTextEdit = { [weak self] in
            guard let self = self, !self.flushing, !self.rebuilding else { return }
            self.inputDirty = true; self.updateDirty(); self.schedulePreview()
        }
        form.fields.append(control)
        return control
    }
    func appendField(_ key: String) {
        guard let value = pathValue(data, key.components(separatedBy: ".")) else { return }
        if textValue(spec(key)["kind"]) == "ramp" { appendRamp(key, array(value)); return }
        if textValue(spec(key)["kind"]) == "types", let values = value as? [String] { appendTypes(key, values); return }
        let control = makeField(key, value)
        if control.view is NSButton && !(control.view is NSPopUpButton) { form.add(control.view) }
        else {
            let title = label(NativePresentation.themeTitle(key, fields: object(draft["fields"])), 12)
            let content = column([title, control.view], gap: 5)
            title.widthAnchor.constraint(equalTo: content.widthAnchor).isActive = true
            if control.view is NSTextField || control.view is NSPopUpButton { control.view.widthAnchor.constraint(equalTo: content.widthAnchor).isActive = true }
            form.add(content)
        }
    }
    func appendTypes(_ key: String, _ selected: [String]) {
        form.add(label(NativePresentation.themeTitle(key, fields: object(draft["fields"])), 12))
        let choices = array(spec(key)["choices"])
        let known = choices.map { textValue($0["value"]) }
        for type in known + selected.filter({ !known.contains($0) }) {
            let title = choices.first { textValue($0["value"]) == type }.map(NativePresentation.choice) ?? NativePresentation.identifier(type)
            let control = FieldControl(title, schema: ["kind": "toggle"], value: selected.contains(type)) { [weak self] value in
                guard let self = self, self.editable else { return }
                var values = pathValue(self.data, key.components(separatedBy: ".")) as? [String] ?? []
                if value as? Bool == true { if !values.contains(type) { values.append(type) } }
                else { values.removeAll { $0 == type } }
                self.change(key, values)
            }
            control.view.identifier = NSUserInterfaceItemIdentifier(key + "." + type)
            control.enabledWhen = { [weak self] in self?.conditionAllows(key) ?? false }
            form.fields.append(control); form.add(control.view)
        }
    }
    func appendRamp(_ key: String, _ stops: [JSON]) {
        form.add(label(NativePresentation.themeTitle(key, fields: object(draft["fields"])), 12, .semibold))
        let band = ThemeRampBand(); band.heightAnchor.constraint(equalToConstant: 22).isActive = true
        rampBands.append((band, key)); form.add(band)
        form.add(label("海拔（米） · 颜色", 11, .regular, .secondaryLabelColor))
        for (index, stop) in stops.enumerated() {
            let prefix = key + "." + String(index)
            let threshold = makeField(prefix + ".threshold", stop["threshold"] ?? 0, title: localized("海拔（米）"))
            let color = makeField(prefix + ".color", stop["color"] ?? "#FFFFFF", title: localized("颜色"))
            threshold.view.widthAnchor.constraint(equalToConstant: 100).isActive = true
            let remove = ActionButton("移除") { [weak self] in
                guard let self = self, self.editable, self.flushInputs() else { return }
                var current = array(pathValue(self.data, key.components(separatedBy: ".")))
                current.remove(at: index); self.change(key, current); self.rebuild()
            }
            remove.toolTip = localized("移除此色阶"); extraControls.append((remove, key))
            form.add(row([threshold.view, color.view, remove], gap: 8))
        }
        let add = ActionButton("添加色阶") { [weak self] in
            guard let self = self, self.editable, self.flushInputs() else { return }
            var current = array(pathValue(self.data, key.components(separatedBy: ".")))
            let range = self.spec(key + ".*.threshold")
            let high = (range["high"] as? NSNumber)?.doubleValue ?? 12000
            let threshold = current.compactMap { ($0["threshold"] as? NSNumber)?.doubleValue }.max() ?? -100
            current.append(["threshold": min(high, threshold + 100), "color": current.last?["color"] ?? "#FFFFFF"])
            self.change(key, current); self.rebuild()
        }
        extraControls.append((add, key)); form.add(add); refreshBands()
    }
    func refreshBands() {
        for (band, key) in rampBands {
            band.stops = array(pathValue(data, key.components(separatedBy: ".")))
            band.continuous = textValue(object(data["terrain"])["mode"]) != "simple"
            band.needsDisplay = true
        }
    }
    func change(_ key: String, _ value: Any) {
        let path = key.components(separatedBy: ".")
        guard (try? jsonString(pathValue(data, path) ?? NSNull())) != (try? jsonString(value)) else { return }
        undoStack.append(data); redoStack.removeAll()
        draft["data"] = replacingPath(data, path, value)
        inputError = false; operationStatus.stringValue = ""; updateDirty(); refreshBands()
        for field in form.fields { field.refresh(enabled: editable, synchronize: false) }
        schedulePreview()
    }
    @discardableResult func flushInputs() -> Bool {
        guard !flushing else { return true }
        guard editable else { return !inputDirty }
        flushing = true; defer { flushing = false }
        do {
            // Validate all fields before updating the draft.
            var updates: [(String, Any)] = []
            for field in form.fields where (field.view as? NSControl)?.isEnabled == true {
                if let value = try field.pendingInputValue(), let key = field.view.identifier?.rawValue { updates.append((key, value)) }
            }
            var next: Any = data
            for (key, value) in updates { next = replacingPath(next, key.components(separatedBy: "."), value) }
            if try jsonString(next) != jsonString(data) {
                undoStack.append(data); redoStack.removeAll(); draft["data"] = next
            }
            inputDirty = false
            if inputError { operationStatus.stringValue = ""; inputError = false }
            updateDirty(); refreshBands()
            return true
        } catch {
            inputDirty = true; inputError = true; updateDirty()
            operationStatus.stringValue = NativePresentation.errorMessage(error.localizedDescription)
            return false
        }
    }
    func restore(all: Bool) {
        guard editable, flushInputs() else { return }
        let keys = entries().filter { key in
            all || ((selectedSection == "__basic__" ? !key.contains(".") : key.hasPrefix(selectedSection + ".")) && matchesSearch(key))
        }
        var next: Any = data
        for key in keys {
            if let value = pathValue(draft["defaults"] ?? [:], key.components(separatedBy: ".")) {
                next = replacingPath(next, key.components(separatedBy: "."), value)
            }
        }
        guard (try? jsonString(next)) != (try? jsonString(data)) else { return }
        undoStack.append(data); redoStack.removeAll(); draft["data"] = next
        inputError = false; operationStatus.stringValue = ""
        rebuild(); schedulePreview()
    }
    func restoreHistory(undo: Bool) {
        guard editable, flushInputs() else { return }
        let next = undo ? undoStack.popLast() : redoStack.popLast()
        guard let next = next else { return }
        if undo { redoStack.append(data) } else { undoStack.append(data) }
        draft["data"] = next; inputError = false; operationStatus.stringValue = ""; rebuild(); schedulePreview()
    }
}

@MainActor final class ThemeRampBand: NSView {
    var stops: [JSON] = []
    var continuous = true
    override func draw(_ dirtyRect: NSRect) {
        let colors = stops.sorted { ($0["threshold"] as? Double ?? 0) < ($1["threshold"] as? Double ?? 0) }
        guard let first = colors.first else { NSColor.controlBackgroundColor.setFill(); bounds.fill(); return }
        let low = first["threshold"] as? Double ?? 0, high = colors.last?["threshold"] as? Double ?? low
        if colors.count == 1 || high <= low { FieldControl.color(textValue(first["color"])).setFill(); bounds.fill(); return }
        let locations = colors.map { CGFloat((($0["threshold"] as? Double ?? 0) - low) / (high - low)) }
        if continuous {
            locations.withUnsafeBufferPointer { positions in
                NSGradient(colors: colors.map { FieldControl.color(textValue($0["color"])) }, atLocations: positions.baseAddress, colorSpace: .sRGB)?.draw(in: bounds, angle: 0)
            }
        } else {
            for (index, stop) in colors.enumerated() {
                let start = locations[index], end = index + 1 < locations.count ? locations[index + 1] : 1
                FieldControl.color(textValue(stop["color"])).setFill()
                NSRect(x: bounds.minX + bounds.width * start, y: bounds.minY, width: bounds.width * (end - start), height: bounds.height).fill()
            }
        }
    }
}
