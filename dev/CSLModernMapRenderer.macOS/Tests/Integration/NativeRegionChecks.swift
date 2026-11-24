import AppKit

@MainActor extension NativeSmoke {
    func inspectRegions() async throws {
        let items: [JSON] = [
            ["id": 10, "name": "Central", "type": "公园", "available": true],
            ["id": -2, "name": "Central", "type": "公园", "available": true],
            ["id": 3, "name": "Alpha", "type": "工业", "available": false],
            ["id": 21, "name": "Beta", "type": "工业", "available": true]
        ]
        let selector = RegionSelector(title: "功能区划", items: items, selected: [-2, 3])
        selector.show(); defer { selector.window.dirty = false; selector.window.close() }
        func ids() -> [Int] { selector.visible.map(\.id) }
        selector.search.stringValue = "Central -2"; selector.controlTextDidChange(Notification(name: NSControl.textDidChangeNotification))
        try requireLayout(ids() == [-2], "live multi-term name and signed ID search")
        selector.search.stringValue = "ALPHA"; selector.rebuild()
        try requireLayout(ids() == [3] && !(selector.results.fields[0].view as! NSControl).isEnabled, "case-insensitive search retains disabled unavailable row")
        selector.search.stringValue = ""; selector.category.selectItem(withTitle: "公园"); selector.filtersChanged()
        try requireLayout(ids() == [-2, 10], "category filtering disambiguates equal names by signed ID")
        selector.category.selectItem(at: 0); selector.state.selectItem(at: 1); selector.filtersChanged()
        try requireLayout(Set(ids()) == [-2, 3], "selected state filter")
        selector.state.selectItem(at: 2); selector.filtersChanged()
        try requireLayout(Set(ids()) == [10, 21], "unselected state filter")
        selector.state.selectItem(at: 0)
        for (index, expected) in [[3, 21, -2, 10], [-2, 10, 21, 3], [-2, 3, 10, 21], [21, 10, 3, -2]].enumerated() {
            selector.sort.selectItem(at: index); selector.filtersChanged()
            try requireLayout(ids() == expected, "stable name or numeric ordering: \(index)")
        }
        try requireLayout(selector.selected == [-2, 3] && !selector.window.dirty, "search filters and sort leave draft intact")
        selector.search.stringValue = "no match"; selector.rebuild()
        try requireLayout(selector.visible.isEmpty && selector.results.fields.isEmpty && !selector.results.stack.arrangedSubviews.isEmpty, "no matches has a visible explanation")
        let empty = RegionSelector(title: "行政区划", items: [], selected: [])
        try requireLayout(empty.visible.isEmpty && !empty.results.stack.arrangedSubviews.isEmpty, "empty catalog has a visible explanation")
        selector.search.stringValue = ""; selector.sort.selectItem(at: 0); selector.rebuild()
        selector.window.contentView?.layoutSubtreeIfNeeded()
        try await Task.sleep(nanoseconds: 150_000_000); selector.window.displayIfNeeded()
        try requireLayout(selector.search.bounds.width > 100 && selector.results.bounds.height > 200, "fixed search and scrolling results have usable space")
        if let image = CGWindowListCreateImage(.null, .optionIncludingWindow, CGWindowID(selector.window.windowNumber), [.boundsIgnoreFraming, .bestResolution]) {
            try NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])?.write(to: output.appendingPathComponent("region-search-filters.png"))
        }
        selector.window.setContentSize(NSSize(width: 450, height: 450))
        selector.window.contentView?.layoutSubtreeIfNeeded()
        try requireLayout(selector.search.bounds.width >= 400 && selector.results.bounds.height > 100, "region selector fits minimum window size")
        for group in ["district", "park"] {
            let count = owner.panels.count
            owner.showRegions(group)
            for _ in 0..<200 {
                if owner.panels.count > count { break }
                try await Task.sleep(nanoseconds: 25_000_000)
            }
            try requireLayout(owner.panels.count == count + 1, "region entry opens: " + group)
            let window = owner.panels.last!
            try requireLayout((window as? EditorWindow)?.retained.first is RegionSelector, "region entry uses searchable selector: " + group)
            window.close()
        }
        checks.append("regions-live-search-category-state-four-stable-sorts-empty-unavailable-and-both-entry-points")
    }
}
