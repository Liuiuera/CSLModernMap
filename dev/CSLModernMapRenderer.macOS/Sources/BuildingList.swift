import AppKit

@MainActor final class BuildingList: NSObject, NSTableViewDataSource, NSTableViewDelegate, NSSearchFieldDelegate {
    unowned let owner: InterfaceController
    let epoch: Int
    let window = EditorWindow("城市概览与建筑列表", width:720, height:600)
    let search = NSSearchField()
    let category = NSPopUpButton()
    let gameNames = NSButton(checkboxWithTitle:localized("使用游戏内名称"),target:nil,action:nil)
    let table = NSTableView()
    let icons = NSCache<NSURL, NSImage>()
    var rows: [JSON] = []
    var visible: [JSON] = []
    private var selectionGeneration = 0
    private var categoryGeneration = 0
    private var catalogGeneration = 0
    private var syncing = false

    init(owner: InterfaceController) {
        self.owner = owner; epoch = owner.epoch
        super.init()
        let stats = object(owner.snapshot["stats"])
        let overview = String(format: localized("建筑 %@ 栋　道路 %.1f km　公交线路 %.1f km　建成区 %.2f km²"),
            textValue(stats["buildings"]), (stats["roadMeters"] as? Double ?? 0)/1000,
            (stats["transitMeters"] as? Double ?? 0)/1000, (stats["builtArea"] as? Double ?? 0)/1_000_000)
        search.placeholderString = localized("搜索建筑名称"); search.delegate = self
        category.target = self; category.action = #selector(categoryChanged)
        gameNames.state = object(owner.snapshot["fields"])["poi.game_names"] as? Bool == true ? .on:.off
        gameNames.target = self; gameNames.action = #selector(reload)
        for (key,title,width) in [("name","建筑",400),("categoryName","类别",180)] {
            let col = NSTableColumn(identifier:NSUserInterfaceItemIdentifier(key))
            col.title = localized(title); col.width = CGFloat(width); table.addTableColumn(col)
        }
        table.dataSource = self; table.delegate = self; table.usesAlternatingRowBackgroundColors = true
        let scroll = NSScrollView(); scroll.hasVerticalScroller = true; scroll.documentView = table
        let layout = column([label(overview),row([search,category,gameNames]),scroll])
        for view in layout.arrangedSubviews { view.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true }
        let base = window.surface; pin(layout,in:base,inset:12)
        window.onClose = { [weak self] in
            guard let self = self else { return }
            self.owner.viewer.send(["type":"setBuildingHighlight"])
            self.owner.panels.removeAll { $0 === self.window }
            self.owner.buildingList = nil
        }
    }

    @objc func reload() {
        catalogGeneration += 1
        let generation = catalogGeneration
        owner.run { [self] in
            let catalog = try await owner.engine.request("getBuildingCatalog", ["documentEpoch":epoch,"useGameNames":gameNames.state == .on])
            guard owner.epoch == epoch, owner.buildingList === self, generation == catalogGeneration else { return }
            rows = array(catalog["items"])
            category.removeAllItems(); category.addItem(withTitle:localized("全部类别"))
            var names: [String:String] = [:]
            for item in rows { names[textValue(item["category"])] = textValue(item["categoryName"]) }
            for key in names.keys.sorted() { category.addItem(withTitle:names[key]!); category.lastItem?.representedObject = key }
            categoryChanged()
        }
    }
    func filter() {
        let key = category.selectedItem?.representedObject as? String
        let query = search.stringValue
        visible = rows.filter { row in
            (key == nil || row["category"] as? String == key) &&
            (query.isEmpty || textValue(row["name"]).localizedCaseInsensitiveContains(query) ||
                (row["memberNames"] as? [String:String])?.values.contains { $0.localizedCaseInsensitiveContains(query) } == true)
        }
        table.reloadData()
    }
    @objc func categoryChanged() {
        filter(); categoryGeneration += 1
        let generation = categoryGeneration
        owner.viewer.send(["type":"setBuildingHighlight"])
        guard let key = category.selectedItem?.representedObject as? String else { return }
        owner.run { [self] in
            let geo = try await owner.engine.request("getBuildingHighlight", ["documentEpoch":epoch,"category":key])
            guard generation == categoryGeneration, owner.buildingList === self, epoch == owner.epoch else { return }
            owner.viewer.send(["type":"setBuildingHighlight","data":geo,"sceneId":owner.sceneID,"revision":owner.revision])
        }
    }
    func controlTextDidChange(_ obj: Notification) { filter() }
    func numberOfRows(in tableView: NSTableView) -> Int { visible.count }
    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        guard let key = tableColumn?.identifier, visible.indices.contains(row) else { return nil }
        let field = tableView.makeView(withIdentifier:key, owner:self) as? NSTextField ?? NSTextField(labelWithString:"")
        field.identifier = key
        let item = visible[row]
        let title = textValue(item[key.rawValue])
        field.stringValue = title
        if key.rawValue == "name", let url = URL(string:textValue(item["iconPath"])), url.isFileURL {
            let icon = icons.object(forKey:url as NSURL) ?? NSImage(contentsOf:url)
            if let icon = icon {
                icons.setObject(icon, forKey:url as NSURL)
                let attachment = NSTextAttachment()
                attachment.image = icon
                attachment.bounds = NSRect(x:0,y:-2,width:16,height:16)
                let text = NSMutableAttributedString(attachment:attachment)
                text.append(NSAttributedString(string:" " + title))
                field.attributedStringValue = text
            }
        }
        return field
    }
    func synchronize(_ selection: JSON) {
        guard let id = object(selection["properties"])["buildingId"] as? Int,
              let index = visible.firstIndex(where: { ($0["memberIds"] as? [Int])?.contains(id) == true }) else { return }
        syncing = true; table.selectRowIndexes(IndexSet(integer:index), byExtendingSelection:false)
        table.scrollRowToVisible(index); syncing = false
    }
    func tableViewSelectionDidChange(_ notification: Notification) {
        guard !syncing, visible.indices.contains(table.selectedRow), let id = visible[table.selectedRow]["id"] else { return }
        selectionGeneration += 1
        let generation = selectionGeneration
        owner.run { [self] in
            let selection = try await owner.engine.request("getBuildingSelection", ["documentEpoch":epoch,"buildingId":id,"useGameNames":gameNames.state == .on])
            guard owner.epoch == epoch, owner.buildingList === self, generation == selectionGeneration else { return }
            owner.selectedObject = selection
            owner.viewer.send(["type":"selectCatalogBuilding","selection":selection,"locate":true,
                "sceneId":owner.sceneID,"revision":owner.revision])
            try await owner.showObject(selection)
        }
    }
}
