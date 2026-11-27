import AppKit

@MainActor final class RegionSelector: NSObject, NSSearchFieldDelegate {
    struct Choice {
        let id: Int
        let name: String
        let category: String
        let available: Bool
    }
    let window: EditorWindow
    let choices: [Choice]
    private(set) var selected: Set<Int>
    private let originalSelection: Set<Int>
    let search = NSSearchField()
    let category = NSPopUpButton(frame: .zero, pullsDown: false)
    let state = NSPopUpButton(frame: .zero, pullsDown: false)
    let sort = NSPopUpButton(frame: .zero, pullsDown: false)
    let results = FormView()
    let summary = NSTextField(wrappingLabelWithString: "")
    let notice = NSTextField(wrappingLabelWithString: "")
    private let selectResults = ActionButton("选择筛选结果") {}
    private let clearResults = ActionButton("取消选择筛选结果") {}
    private let commitButton = ActionButton("应用") {}
    private let cancelButton = ActionButton("取消") {}
    private(set) var visible: [Choice] = []
    private let categories: [String]
    var unavailableReason: (() -> String?)?
    var apply: ((RegionSelector) -> Void)?

    init(title: String, items: [JSON], selected: Set<Int>) {
        window = EditorWindow(title, width: 580, height: 650)
        choices = items.map { item in
            Choice(id: item["id"] as! Int, name: textValue(item["name"]),
                   category: textValue(item["type"]).isEmpty ? localized(title) : textValue(item["type"]),
                   available: item["available"] as? Bool ?? false)
        }
        let knownSelection = selected.intersection(Set(choices.map(\.id)))
        self.selected = knownSelection
        originalSelection = knownSelection
        categories = Set(choices.map(\.category)).sorted { $0.localizedStandardCompare($1) == .orderedAscending }
        super.init()
        window.minSize.height = 450
        search.placeholderString = localized("搜索名称或编号")
        search.delegate = self; search.sendsSearchStringImmediately = true
        category.addItem(withTitle: localized("全部类别"))
        // Keep categories distinct from the prompt.
        for title in categories { category.menu?.addItem(NSMenuItem(title: title, action: nil, keyEquivalent: "")) }
        state.addItems(withTitles: ["全部", "已选", "未选"].map(localized))
        sort.addItems(withTitles: ["名称升序", "名称降序", "编号升序", "编号降序"].map(localized))
        for popup in [category, state, sort] { popup.target = self; popup.action = #selector(filtersChanged) }
        let filters = row([category, state, sort], gap: 8)
        for popup in [category, state, sort] {
            popup.widthAnchor.constraint(equalTo: filters.widthAnchor, multiplier: 1 / 3, constant: -16 / 3).isActive = true
        }
        selectResults.perform = { [weak self] in self?.selectVisible(true) }
        clearResults.perform = { [weak self] in self?.selectVisible(false) }
        selectResults.toolTip = localized("只选择筛选结果中的可用区域。")
        clearResults.toolTip = localized("只取消选择筛选结果中的可用区域。")
        let bulk = row([selectResults, clearResults], gap: 8)
        summary.font = .systemFont(ofSize: 12); summary.textColor = .secondaryLabelColor
        notice.font = .systemFont(ofSize: 11); notice.textColor = .secondaryLabelColor
        let controls = column([search, filters, bulk, summary, notice], gap: 8)
        for view in [search, filters, summary, notice] {
            view.widthAnchor.constraint(equalTo: controls.widthAnchor).isActive = true
        }
        commitButton.perform = { [weak self] in self?.commit() }
        cancelButton.perform = { [weak self] in self?.cancel() }
        cancelButton.keyEquivalent = "\u{1b}"
        let actions = row([commitButton, cancelButton])
        let layout = column([controls, results, actions], gap: 12)
        let host = window.surface; pin(layout, in: host, inset: 18)
        for view in [controls, results, actions] { view.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true }
        results.setContentHuggingPriority(.defaultLow, for: .vertical)
        results.stack.edgeInsets = NSEdgeInsets(top: 12, left: 18, bottom: 12, right: 18)
        rebuild()
    }
    func show() { window.center(); window.makeKeyAndOrderFront(nil) }
    func controlTextDidChange(_ obj: Notification) { rebuild() }
    @objc func filtersChanged() { rebuild() }
    func selectVisible(_ enabled: Bool) {
        guard !window.saving, unavailableReason?() == nil else { return }
        let ids = Set(visible.filter(\.available).map(\.id))
        if enabled { selected.formUnion(ids) } else { selected.subtract(ids) }
        window.dirty = selected != originalSelection
        rebuild(resetScroll: false)
    }
    func cancel() {
        guard !window.saving else { return }
        window.dirty = false
        window.close()
    }
    func commit() {
        guard !window.saving, !choices.isEmpty, unavailableReason?() == nil, let apply = apply else { return }
        // Lock before scheduling to prevent duplicate submissions.
        window.saving = true
        refreshAvailability()
        apply(self)
    }
    func refreshAvailability() {
        let reason = unavailableReason?()
        let editable = !window.saving && reason == nil
        commitButton.isEnabled = editable && !choices.isEmpty
        selectResults.isEnabled = editable && visible.contains { $0.available && !selected.contains($0.id) }
        clearResults.isEnabled = editable && visible.contains { $0.available && selected.contains($0.id) }
        cancelButton.isEnabled = !window.saving
        for control in [search, category, state, sort] as [NSControl] { control.isEnabled = !window.saving }
        for (field, choice) in zip(results.fields, visible) {
            (field.view as? NSControl)?.isEnabled = editable && choice.available
        }
        notice.stringValue = window.saving ? localized("正在应用区域选择…") :
            reason ?? localized("批量操作只影响筛选结果中的可用区域，隐藏条目保持不变。")
    }
    private func refreshSummary() {
        let hidden = selected.count - visible.filter { selected.contains($0.id) }.count
        summary.stringValue = "\(localized("结果")) \(visible.count) / \(choices.count) · \(localized("已选")) \(selected.count) · \(localized("隐藏已选")) \(hidden)"
        refreshAvailability()
    }
    func rebuild(resetScroll: Bool = true) {
        let scroll = resetScroll ? 0 : results.contentView.bounds.minY
        let terms = search.stringValue.split(whereSeparator: { $0.isWhitespace }).map(String.init)
        let categoryIndex = category.indexOfSelectedItem - 1
        let categoryValue = categories.indices.contains(categoryIndex) ? categories[categoryIndex] : nil
        visible = choices.filter { choice in
            let text = "\(choice.name) \(choice.category) \(choice.id)"
            return (categoryValue == nil || choice.category == categoryValue) &&
                (state.indexOfSelectedItem == 0 || selected.contains(choice.id) == (state.indexOfSelectedItem == 1)) &&
                terms.allSatisfy { text.localizedCaseInsensitiveContains($0) }
        }.enumerated().sorted { left, right in
            let a = left.element, b = right.element
            if sort.indexOfSelectedItem < 2 {
                let order = a.name.localizedStandardCompare(b.name)
                if order != .orderedSame { return order == (sort.indexOfSelectedItem == 0 ? .orderedAscending : .orderedDescending) }
                if a.id != b.id { return a.id < b.id }
            } else if a.id != b.id { return sort.indexOfSelectedItem == 2 ? a.id < b.id : a.id > b.id }
            return left.offset < right.offset
        }.map(\.element)
        results.reset()
        if visible.isEmpty {
            results.add(label(choices.isEmpty ? "当前城市没有此类区域。" : "没有匹配的区域，请调整搜索或筛选。", 13, .regular, .secondaryLabelColor))
        }
        for choice in visible {
            let toggle = results.field(choice.name, ["kind": "toggle"], selected.contains(choice.id)) { [weak self] value in
                guard let self = self, !self.window.saving, self.unavailableReason?() == nil, choice.available else { return }
                if value as? Bool == true { self.selected.insert(choice.id) } else { self.selected.remove(choice.id) }
                self.window.dirty = self.selected != self.originalSelection
                self.refreshSummary()
                // Defer filtering until the checkbox action returns.
                if self.state.indexOfSelectedItem != 0 {
                    DispatchQueue.main.async { [weak self] in self?.rebuild(resetScroll: false) }
                }
            }
            (toggle.view as? NSControl)?.isEnabled = choice.available
            let detail = "\(choice.category) · \(localized("编号")) \(choice.id)" + (choice.available ? "" : " · " + localized("不可用"))
            toggle.view.toolTip = choice.name + "\n" + detail
            let caption = NSTextField(wrappingLabelWithString: detail)
            caption.font = .systemFont(ofSize: 11); caption.textColor = .secondaryLabelColor
            results.add(caption)
        }
        results.restoreScroll(scroll)
        refreshSummary()
    }
}
