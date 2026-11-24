import AppKit

enum TransitTypePresentation {
    // Match the WinUI transit type order.
    static let order = ["Metro", "Train", "Monorail", "Tram", "Bus", "Trolleybus", "CableCar", "Ship", "Ferry", "Airplane", "Helicopter", "Blimp"]
    static func precedes(_ a: String, _ b: String) -> Bool {
        let left = order.firstIndex(of: a) ?? order.count, right = order.firstIndex(of: b) ?? order.count
        return left == right ? a.localizedStandardCompare(b) == .orderedAscending : left < right
    }
    static func title(_ key: String, types: [JSON]) -> String {
        let title = types.first { textValue($0["key"]) == key }.map { textValue($0["label"]) } ?? ""
        if title.isEmpty || title == key || title.hasPrefix("gui.") { return NativePresentation.identifier(key) }
        return localized(title)
    }
}

struct TransitLineSelection {
    struct Choice {
        let id: Int
        let name: String
        let type: String
        let color: String
        let stops: Int?
        let length: Double?
        let available: Bool

        func details(typeTitle: String) -> [String] {
            var details = [typeTitle, "\(localized("编号")) \(id)"]
            if let stops { details.append("\(localized("站点数")) \(stops)") }
            if let length { details.append(String(format: "%.2f km", length / 1000)) }
            return details
        }
    }
    enum Sort: String {
        case name, id, stops, length
    }
    let choices: [Choice]
    private(set) var selected: Set<Int>?
    private let original: Set<Int>?
    var dirty: Bool { selected != original }
    var ids: Set<Int> { selected ?? Set(choices.map(\.id)) }
    var payload: Any {
        guard let selected, selected.isEmpty || selected.count != choices.count else { return NSNull() }
        return selected.sorted()
    }
    var sorts: [Sort] {
        [.name, .id] + (choices.contains { $0.stops != nil } ? [.stops] : []) + (choices.contains { $0.length != nil } ? [.length] : [])
    }
    init(items: [JSON], selected: Set<Int>?) {
        var seen = Set<Int>()
        choices = items.compactMap { item in
            guard let id = item["id"] as? Int, seen.insert(id).inserted else { return nil }
            let name = textValue(item["name"]).trimmingCharacters(in: .whitespacesAndNewlines)
            let stops = item["stops"] as? Int
            let length = (item["length_m"] as? NSNumber)?.doubleValue
            return Choice(id: id, name: name.isEmpty ? String(id) : name, type: textValue(item["type"]),
                color: textValue(item["color"]).isEmpty ? "#8A8F99" : textValue(item["color"]),
                stops: stops.flatMap { $0 >= 0 ? $0 : nil },
                length: length.flatMap { $0.isFinite && $0 >= 0 ? $0 : nil }, available: item["available"] as? Bool ?? true)
        }
        let known = Set(choices.map(\.id))
        self.selected = selected.map { $0.intersection(known) }
        original = self.selected
    }
    func contains(_ id: Int) -> Bool { selected?.contains(id) ?? true }
    mutating func selectAll(_ enabled: Bool) { selected = enabled ? nil : [] }
    mutating func set(_ ids: Set<Int>, enabled: Bool) {
        let editable = ids.intersection(Set(choices.filter(\.available).map(\.id)))
        guard !editable.isEmpty else { return }
        if selected == nil {
            if enabled { return }
            selected = Set(choices.map(\.id))
        }
        if enabled { selected?.formUnion(editable) } else { selected?.subtract(editable) }
    }
    func filtered(search: String, type: String?, state: Int, sort: Sort, descending: Bool, title: (String) -> String) -> [Choice] {
        let terms = search.split(whereSeparator: { $0.isWhitespace }).map(String.init)
        return choices.enumerated().filter { _, choice in
            guard type == nil || type == choice.type, state == 0 || contains(choice.id) == (state == 1) else { return false }
            if terms.isEmpty { return true }
            let text = ([choice.name, choice.type] + choice.details(typeTitle: title(choice.type))).joined(separator: " ")
            return terms.allSatisfy { text.localizedCaseInsensitiveContains($0) }
        }.sorted { left, right in
            let a = left.element, b = right.element
            let order: ComparisonResult
            switch sort {
            case .name: order = a.name.localizedStandardCompare(b.name)
            case .id: order = a.id == b.id ? .orderedSame : a.id < b.id ? .orderedAscending : .orderedDescending
            case .stops, .length:
                let x = sort == .stops ? a.stops.map(Double.init) : a.length
                let y = sort == .stops ? b.stops.map(Double.init) : b.length
                // Sort missing values last in both directions.
                if x == nil || y == nil {
                    if x != nil { return true }
                    if y != nil { return false }
                    order = .orderedSame
                } else { order = x == y ? .orderedSame : x! < y! ? .orderedAscending : .orderedDescending }
            }
            if order != .orderedSame { return order == (descending ? .orderedDescending : .orderedAscending) }
            return a.id == b.id ? left.offset < right.offset : a.id < b.id
        }.map(\.element)
    }
}

@MainActor final class TransitLineSelector: NSObject, NSSearchFieldDelegate {
    let window: EditorWindow
    private(set) var selection: TransitLineSelection
    let metro: Bool
    private let types: [JSON]
    private var enabledTypes: Set<String>
    private let categories: [String]
    let search = NSSearchField()
    let category = NSPopUpButton(frame: .zero, pullsDown: false)
    let state = NSPopUpButton(frame: .zero, pullsDown: false)
    let sort = NSPopUpButton(frame: .zero, pullsDown: false)
    let results = FormView()
    let summary = NSTextField(wrappingLabelWithString: "")
    let notice = NSTextField(wrappingLabelWithString: "")
    private let allButton = ActionButton("全部线路") {}
    private let noneButton = ActionButton("全部不选") {}
    private let selectResults = ActionButton("选择筛选结果") {}
    private let clearResults = ActionButton("取消选择筛选结果") {}
    private let commitButton = ActionButton("应用") {}
    private let cancelButton = ActionButton("取消") {}
    private(set) var visible: [TransitLineSelection.Choice] = []
    var unavailableReason: (() -> String?)?
    var apply: ((TransitLineSelector) -> Void)?

    init(metro: Bool, items: [JSON], selected: Set<Int>?, types: [JSON], enabledTypes: Set<String>) {
        self.metro = metro; self.types = types; self.enabledTypes = enabledTypes
        selection = TransitLineSelection(items: items, selected: selected)
        categories = Set(selection.choices.map(\.type)).sorted(by: TransitTypePresentation.precedes)
        window = EditorWindow(metro ? "地铁线路筛选" : "公共交通线路筛选", width: 660, height: 700)
        super.init()
        window.minSize = NSSize(width: 520, height: 490)
        search.placeholderString = localized("搜索线路名称、编号、类型或详情")
        search.delegate = self; search.sendsSearchStringImmediately = true
        category.addItem(withTitle: localized("全部交通类型"))
        for type in categories { category.menu?.addItem(NSMenuItem(title: typeTitle(type), action: nil, keyEquivalent: "")) }
        state.addItems(withTitles: ["全部", "已选", "未选"].map(localized))
        for key in selection.sorts {
            let titles: [String]
            switch key {
            case .name: titles = ["名称升序", "名称降序"]
            case .id: titles = ["编号升序", "编号降序"]
            case .stops: titles = ["站点数升序", "站点数降序"]
            case .length: titles = ["长度升序", "长度降序"]
            }
            sort.addItems(withTitles: titles.map(localized))
        }
        for popup in [category, state, sort] { popup.target = self; popup.action = #selector(filtersChanged) }
        let filters = row([category, state, sort], gap: 8)
        for popup in [category, state, sort] {
            popup.widthAnchor.constraint(equalTo: filters.widthAnchor, multiplier: 1 / 3, constant: -16 / 3).isActive = true
        }
        allButton.perform = { [weak self] in self?.selectAll(true) }
        noneButton.perform = { [weak self] in self?.selectAll(false) }
        allButton.toolTip = localized("选择所有线路，包括筛选中隐藏的线路。")
        noneButton.toolTip = localized("取消所有线路，包括筛选中隐藏的线路。")
        selectResults.perform = { [weak self] in self?.selectVisible(true) }
        clearResults.perform = { [weak self] in self?.selectVisible(false) }
        for button in [selectResults, clearResults] { button.toolTip = localized("只修改当前筛选结果，隐藏线路保持不变。") }
        for (control, title) in [(search, "搜索线路"), (category, "交通类型筛选"), (state, "选择状态筛选"), (sort, "线路排序")] as [(NSControl, String)] {
            control.setAccessibilityLabel(localized(title))
        }
        summary.font = .systemFont(ofSize: 12); summary.textColor = .secondaryLabelColor
        notice.font = .systemFont(ofSize: 11); notice.textColor = .secondaryLabelColor
        let controls = column([search, filters, row([selectResults, clearResults]), row([allButton, noneButton]), summary, notice], gap: 8)
        for view in [search, filters, summary, notice] { view.widthAnchor.constraint(equalTo: controls.widthAnchor).isActive = true }
        commitButton.perform = { [weak self] in self?.commit() }
        cancelButton.perform = { [weak self] in self?.cancel() }
        cancelButton.keyEquivalent = "\u{1b}"
        let actions = row([commitButton, cancelButton])
        let layout = column([controls, results, actions], gap: 12)
        let host = window.surface; pin(layout, in: host, inset: 18)
        for view in [controls, results, actions] { view.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true }
        results.setContentHuggingPriority(.defaultLow, for: .vertical)
        results.heightAnchor.constraint(greaterThanOrEqualToConstant: 100).isActive = true
        rebuild()
    }
    func typeTitle(_ type: String) -> String { TransitTypePresentation.title(type, types: types) }
    func synchronizeEnabledTypes(_ value: Set<String>) {
        guard value != enabledTypes else { return }
        enabledTypes = value; rebuild(resetScroll: false)
    }
    func show() { window.center(); window.makeKeyAndOrderFront(nil); window.makeFirstResponder(search) }
    func controlTextDidChange(_ obj: Notification) { rebuild() }
    @objc func filtersChanged() { rebuild() }
    private var editable: Bool { !window.saving && unavailableReason?() == nil }
    private var hasApplicableSelection: Bool { !selection.choices.isEmpty && (!metro || !selection.ids.isEmpty) }
    func selectAll(_ enabled: Bool) {
        guard editable else { return }
        selection.selectAll(enabled); window.dirty = selection.dirty; rebuild(resetScroll: false)
    }
    func selectVisible(_ enabled: Bool) {
        guard editable else { return }
        selection.set(Set(visible.map(\.id)), enabled: enabled)
        window.dirty = selection.dirty; rebuild(resetScroll: false)
    }
    func cancel() {
        guard !window.saving else { return }
        window.dirty = false; window.close()
    }
    func commit() {
        guard editable, hasApplicableSelection, let apply = apply else { return }
        window.saving = true; refreshAvailability(); apply(self)
    }
    func refreshAvailability() {
        let reason = unavailableReason?()
        let editable = !window.saving && reason == nil
        commitButton.isEnabled = editable && hasApplicableSelection
        allButton.isEnabled = editable && selection.selected != nil
        noneButton.isEnabled = editable && (selection.selected == nil || !selection.ids.isEmpty)
        selectResults.isEnabled = editable && visible.contains { $0.available && !selection.contains($0.id) }
        clearResults.isEnabled = editable && visible.contains { $0.available && selection.contains($0.id) }
        cancelButton.isEnabled = !window.saving
        for control in [search, category, state, sort] as [NSControl] { control.isEnabled = !window.saving }
        for (field, choice) in zip(results.fields, visible) { (field.view as? NSControl)?.isEnabled = editable && choice.available }
        let eligible = selection.choices.filter { $0.available && enabledTypes.contains($0.type) && selection.contains($0.id) }.count
        let hint: String
        if metro && selection.ids.isEmpty { hint = "请至少选择一条地铁示意图线路" }
        else if metro && eligible == 0 { hint = "当前选择没有可用线路；应用后无法生成示意图。" }
        else { hint = "批量操作只影响筛选结果。交通类型开关在设置页中独立保留。" }
        notice.stringValue = window.saving ? localized("正在应用线路选择…") : reason ?? localized(hint)
    }
    private func refreshSummary() {
        let ids = selection.ids, hidden = ids.count - visible.filter { ids.contains($0.id) }.count
        summary.stringValue = "\(localized("结果")) \(visible.count) / \(selection.choices.count) · \(localized("已选")) \(ids.count) · \(localized("隐藏已选")) \(hidden)" +
            (selection.selected == nil ? " · " + localized("全部线路") : "")
        refreshAvailability()
    }
    func rebuild(resetScroll: Bool = true) {
        let scroll = resetScroll ? 0 : results.contentView.bounds.minY
        let categoryIndex = category.indexOfSelectedItem - 1
        let type = categories.indices.contains(categoryIndex) ? categories[categoryIndex] : nil
        let sortIndex = max(0, sort.indexOfSelectedItem)
        visible = selection.filtered(search: search.stringValue, type: type, state: state.indexOfSelectedItem,
            sort: selection.sorts[min(sortIndex / 2, selection.sorts.count - 1)], descending: sortIndex % 2 == 1, title: typeTitle)
        results.reset()
        if visible.isEmpty {
            results.add(NSTextField(wrappingLabelWithString: localized(selection.choices.isEmpty ? "当前城市没有可用线路。" : "没有匹配的线路，请调整搜索或筛选。")))
        }
        for choice in visible {
            let toggle = FieldControl(choice.name, schema: ["kind": "toggle"], value: selection.contains(choice.id)) { [weak self] value in
                guard let self = self, self.editable, choice.available else { return }
                self.selection.set([choice.id], enabled: value as? Bool == true)
                self.window.dirty = self.selection.dirty; self.refreshSummary()
                if self.state.indexOfSelectedItem != 0 { DispatchQueue.main.async { [weak self] in self?.rebuild(resetScroll: false) } }
            }
            toggle.view.identifier = NSUserInterfaceItemIdentifier("line.\(choice.id)")
            (toggle.view as? NSButton)?.cell?.wraps = true
            toggle.view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            let swatch = NSView(); swatch.wantsLayer = true; swatch.layer?.backgroundColor = FieldControl.color(choice.color).cgColor
            swatch.layer?.cornerRadius = 3; swatch.layer?.borderWidth = 0.5; swatch.layer?.borderColor = NSColor.separatorColor.cgColor
            swatch.widthAnchor.constraint(equalToConstant: 18).isActive = true; swatch.heightAnchor.constraint(equalToConstant: 18).isActive = true
            swatch.toolTip = choice.color; swatch.setAccessibilityLabel(choice.color)
            let heading = row([swatch, toggle.view], gap: 8)
            var details = choice.details(typeTitle: typeTitle(choice.type))
            if !choice.available { details.append(localized("不可用")) }
            if !enabledTypes.contains(choice.type) { details.append(localized("此交通类型未启用")) }
            let detail = NSTextField(wrappingLabelWithString: details.joined(separator: " · "))
            detail.font = .systemFont(ofSize: 11); detail.textColor = .secondaryLabelColor
            let contents = column([heading, detail], gap: 4)
            heading.widthAnchor.constraint(equalTo: contents.widthAnchor).isActive = true
            detail.widthAnchor.constraint(equalTo: contents.widthAnchor).isActive = true
            results.fields.append(toggle); results.add(contents)
        }
        refreshSummary(); results.restoreScroll(scroll)
    }
}
