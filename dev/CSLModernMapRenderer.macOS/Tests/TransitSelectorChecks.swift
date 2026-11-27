import AppKit

// Compile with Sources/*.swift excluding AppMain.swift; this entry point never starts the engine.
@main struct TransitSelectorChecks {
    @MainActor static func main() throws {
        _ = NSApplication.shared
        func check(_ condition: @autoclosure () -> Bool, _ message: String) throws {
            if !condition() { throw AppFailure(message: message) }
        }
        let items: [JSON] = [
            ["id": 10, "name": "Central", "type": "Metro", "color": "#FF0000", "stops": 4, "length_m": 1200],
            ["id": -2, "name": "Central", "type": "Train", "color": "#0044FF", "stops": 2, "length_m": 2300],
            ["id": 3, "name": "Alpha", "type": "Bus", "available": false],
            ["id": 21, "name": "Route 10", "type": "Bus", "stops": 0, "length_m": 0],
            ["id": 4, "name": "Route 2", "type": "Tram", "stops": 8, "length_m": 1000]
        ]
        func filtered(_ model: TransitLineSelection, _ sort: TransitLineSelection.Sort, _ descending: Bool = false,
                      search: String = "", type: String? = nil, state: Int = 0) -> [Int] {
            model.filtered(search: search, type: type, state: state, sort: sort, descending: descending,
                title: NativePresentation.identifier).map(\.id)
        }
        var all = TransitLineSelection(items: items, selected: nil)
        try check(all.payload is NSNull && all.ids.count == 5 && !all.dirty, "null selects every catalog line")
        all.set([], enabled: false)
        try check(all.payload is NSNull, "empty result bulk does not convert all to a subset")
        all.set([10], enabled: true)
        try check(all.payload is NSNull, "selecting an already selected result preserves all")
        all.set([10], enabled: false)
        try check(all.selected == [-2, 3, 4, 21], "deselect results preserves all hidden selections")
        all.set([10], enabled: true)
        try check(all.payload is NSNull, "selecting the entire catalog submits null")
        all.selectAll(false)
        try check(all.payload as? [Int] == [] && all.ids.isEmpty, "[] selects no lines")
        all.selectAll(true)
        try check(all.payload is NSNull && !all.dirty, "global all returns to the original null state")
        let empty = TransitLineSelection(items: [], selected: [])
        try check(empty.payload as? [Int] == [] && empty.sorts == [.name, .id], "empty catalog preserves explicit none and omits unsupported sorts")
        let subset = TransitLineSelection(items: items, selected: [-2, 3])
        try check(filtered(subset, .name) == [3, -2, 10, 4, 21], "natural name order and stable signed ID ties")
        try check(filtered(subset, .name, true) == [21, 4, -2, 10, 3], "descending name order retains stable ties")
        try check(filtered(subset, .id) == [-2, 3, 4, 10, 21], "numeric ID ascending")
        try check(filtered(subset, .id, true) == [21, 10, 4, 3, -2], "numeric ID descending")
        try check(filtered(subset, .stops) == [21, -2, 10, 4, 3], "zero stops is real data and missing counts sort last")
        try check(filtered(subset, .stops, true) == [4, 10, -2, 21, 3], "missing counts stay last in descending order")
        try check(filtered(subset, .length) == [21, 4, 10, -2, 3], "length ascending")
        try check(filtered(subset, .length, true) == [-2, 10, 4, 21, 3], "length descending with absent metadata last")
        try check(filtered(subset, .name, search: "CENTRAL -2") == [-2], "case-insensitive multi-term name and signed ID search")
        try check(filtered(subset, .name, search: "Central 2.30 km") == [-2], "search includes displayed route length")
        try check(filtered(subset, .name, search: "\(localized("站点数")) 8") == [4], "search includes displayed stop count")
        try check(filtered(subset, .name, type: "Metro") == [10], "transport type filter")
        try check(filtered(subset, .name, state: 1) == [3, -2], "selected state includes unavailable rows")
        try check(filtered(subset, .name, state: 2) == [10, 4, 21], "unselected state filter")
        try check(subset.selected == [-2, 3] && !subset.dirty, "search, filters, and sorting never mutate the draft")
        try check(TransitSettingsLayout.allows("line_width", child: nil, metro: false), "public appearance is allowed")
        for key in ["station_diameter", "line_label_style"] {
            try check(TransitSettingsLayout.allows(key, child: nil, metro: false)
                && !TransitSettingsLayout.allows(key, child: nil, metro: true), "public-only field: \(key)")
        }
        try check(!TransitSettingsLayout.allows("spacing", child: nil, metro: false), "metro layout cannot leak onto public settings")
        try check(!TransitSettingsLayout.allows("line_width", child: nil, metro: true), "appearance cannot leak onto metro generation")
        try check(TransitSettingsLayout.allows("schematic_options", child: "adaptive_strength", metro: true), "metro adaptive strength is allowed")
        try check(!TransitSettingsLayout.allows("schematic_options", child: "scale_debug", metro: true), "hidden schema children stay hidden")
        try check(!TransitSettingsLayout.allows("layers", child: nil, metro: false), "whole layer objects cannot leak through the allowlist")
        try check(TransitTypePresentation.order.reversed().sorted(by: TransitTypePresentation.precedes) == TransitTypePresentation.order, "transport type order matches WinUI")

        let selector = TransitLineSelector(metro: true, items: items, selected: nil,
            types: [["key": "Metro", "label": "地铁"]], enabledTypes: ["Metro"])
        defer { selector.window.dirty = false; selector.window.close() }
        selector.show()
        selector.search.stringValue = "Central"; selector.rebuild()
        selector.selectVisible(false)
        try check(selector.selection.selected == [3, 4, 21], "UI bulk scope preserves hidden entries")
        selector.search.stringValue = ""; selector.state.selectItem(at: 1); selector.filtersChanged()
        try check(Set(selector.visible.map(\.id)) == [3, 4, 21], "selected filter updates after bulk")
        selector.selectAll(false)
        try check(selector.selection.payload as? [Int] == []
            && selector.notice.stringValue == localized("请至少选择一条地铁示意图线路"), "empty metro draft explains why Apply is unavailable")
        selector.unavailableReason = { "stale city" }; selector.refreshAvailability()
        selector.selectAll(true)
        try check(selector.selection.payload as? [Int] == [], "stale selectors cannot change drafts")
        var commits = 0
        selector.apply = { _ in commits += 1 }
        selector.commit()
        try check(commits == 0, "stale selectors cannot submit")
        selector.unavailableReason = nil
        selector.commit()
        try check(commits == 0 && !selector.window.saving, "empty metro draft cannot submit")
        selector.selectAll(true)
        selector.commit(); selector.commit()
        try check(commits == 1 && selector.window.saving, "commit locks synchronously against duplicate requests")
        selector.cancel()
        try check(selector.window.isVisible, "cancel cannot close an in-flight commit")
        selector.window.saving = false
        selector.search.stringValue = "no matches"; selector.state.selectItem(at: 0); selector.rebuild()
        try check(selector.visible.isEmpty && !selector.results.stack.arrangedSubviews.isEmpty, "no results explains how to recover")
        selector.window.setContentSize(NSSize(width: 520, height: 490)); selector.window.contentView?.layoutSubtreeIfNeeded()
        try check(selector.results.bounds.height >= 100 && selector.search.bounds.width > 400, "selector remains usable at minimum size")
        selector.cancel()
        try check(!selector.window.dirty && !selector.window.isVisible, "cancel discards draft without applying")

        let grid = TransitTypeChoices(); grid.orientation = .vertical; grid.alignment = .leading; grid.spacing = 8
        let gridWindow = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 320, height: 200), styleMask: [.titled], backing: .buffered, defer: false)
        gridWindow.isReleasedWhenClosed = false
        let gridHost = NSView(); gridWindow.contentView = gridHost; pin(grid, in: gridHost)
        defer { gridWindow.close() }
        let first = NSButton(checkboxWithTitle: "Metro", target: nil, action: nil)
        let second = NSButton(checkboxWithTitle: "Train", target: nil, action: nil)
        grid.addPair([first, second])
        gridHost.layoutSubtreeIfNeeded(); gridHost.layoutSubtreeIfNeeded()
        try check((grid.arrangedSubviews.first as? NSStackView)?.orientation == .horizontal, "types use two columns when they fit")
        try check(abs(first.alignmentRect(forFrame: first.frame).width - 156) < 1 && abs(second.frame.width - first.frame.width) < 1, "initial type columns have equal usable click areas")
        gridWindow.setContentSize(NSSize(width: 200, height: 200)); grid.needsLayout = true; gridHost.layoutSubtreeIfNeeded()
        try check((grid.arrangedSubviews.first as? NSStackView)?.orientation == .vertical, "types reflow in narrow sidebars")
        first.title = "A transport type with a much longer translated label"
        gridWindow.setContentSize(NSSize(width: 320, height: 200)); grid.needsLayout = true; gridHost.layoutSubtreeIfNeeded()
        try check((grid.arrangedSubviews.first as? NSStackView)?.orientation == .vertical && first.frame.height >= 30, "long type labels wrap without truncating")
        print("PASS: transit null/empty/subset drafts; eight stable sorts; search/type/state/bulk; allowlists; stale/duplicate/cancel guards; minimum selector and responsive type layouts")
    }
}
