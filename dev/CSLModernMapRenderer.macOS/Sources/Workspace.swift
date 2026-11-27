import AppKit
import WebKit

final class WorkspaceRootView: NSView {
    var appearanceChanged: (() -> Void)?
    override func viewDidChangeEffectiveAppearance() { super.viewDidChangeEffectiveAppearance();appearanceChanged?() }
}

func label(_ title: String, _ size: CGFloat = 13, _ weight: NSFont.Weight = .regular, _ color: NSColor = .labelColor) -> NSTextField {
    let v = NSTextField(labelWithString: localized(title))
    v.font = .systemFont(ofSize: size, weight: weight)
    v.textColor = color
    v.lineBreakMode = .byTruncatingTail
    return v
}
func column(_ views: [NSView], gap: CGFloat = 10) -> NSStackView {
    let v = NSStackView(views: views)
    v.orientation = .vertical; v.alignment = .leading; v.spacing = gap
    v.setHuggingPriority(.required, for: .vertical)
    v.translatesAutoresizingMaskIntoConstraints = false
    return v
}
func row(_ views: [NSView], gap: CGFloat = 10) -> NSStackView {
    let v = NSStackView(views: views)
    v.orientation = .horizontal; v.alignment = .centerY; v.spacing = gap
    v.translatesAutoresizingMaskIntoConstraints = false
    return v
}
func pin(_ child: NSView, in parent: NSView, inset: CGFloat = 0) {
    child.translatesAutoresizingMaskIntoConstraints = false
    parent.addSubview(child)
    NSLayoutConstraint.activate([
        child.leadingAnchor.constraint(equalTo: parent.leadingAnchor, constant: inset),
        child.trailingAnchor.constraint(equalTo: parent.trailingAnchor, constant: -inset),
        child.topAnchor.constraint(equalTo: parent.topAnchor, constant: inset),
        child.bottomAnchor.constraint(equalTo: parent.bottomAnchor, constant: -inset)
    ])
}
func rule() -> NSBox { let v = NSBox(); v.boxType = .separator; return v }
func material(_ kind: NSVisualEffectView.Material, blending: NSVisualEffectView.BlendingMode = .withinWindow) -> NSVisualEffectView {
    let v = NSVisualEffectView()
    v.material = kind; v.blendingMode = blending; v.state = .followsWindowActiveState
    return v
}
func symbol(_ name: String, size: CGFloat = 18) -> NSImage? {
    NSImage(systemSymbolName: name, accessibilityDescription: nil)?.withSymbolConfiguration(.init(pointSize: size, weight: .regular))
}
func resourceImage(_ name: String, folder: String? = nil) -> NSImage? {
    Bundle.main.url(forResource: name, withExtension: "png", subdirectory: folder).flatMap { NSImage(contentsOf: $0) }
}
let layerGroups: [(String, [(String, String)])] = [
    ("底图", [("卫星影像", "globe.americas"), ("地形", "mountain.2"), ("水域", "water.waves")]),
    ("城市", [("道路", "road.lanes"), ("建筑", "building.2"), ("铁路", "tram"), ("建成区", "square.dashed"), ("区域上色", "paintpalette"), ("路况", "car")]),
    ("交通", [("公共交通", "bus")]),
    ("文字与图标", MapSymbolCategory.allCases.map { ($0.title, $0.symbolName) }),
    ("区域与区划", [("行政区划", "map"), ("功能区划", "square.grid.2x2"), ("规划网格", "grid")]),
    ("其他", [("PO对象", "cube"), ("水印", "signature")])
]

// Keep the split view background transparent.
@MainActor final class MapSplitView: NSSplitView {
    override var isOpaque: Bool { false }
    override func draw(_ dirtyRect: NSRect) {
        let panes = arrangedSubviews.filter { !$0.isHidden }
        for pane in panes.dropLast() {
            drawDivider(in: NSRect(x: pane.frame.maxX, y: bounds.minY, width: dividerThickness, height: bounds.height))
        }
    }
}

@MainActor final class InterfaceController: NSObject, NSApplicationDelegate, NSToolbarDelegate, NSOutlineViewDataSource, NSOutlineViewDelegate, NSWindowDelegate, NSMenuItemValidation {
    let engine = EngineClient()
    let viewer = WebHost(channel: "cslPreview")
    lazy var rmp = RmpController(owner: self)
    var snapshot: JSON = [:]
    var metadata: JSON = [:]
    var epoch = 0
    var sceneID = ""
    var revision = 0
    var context = "map"
    var engineReady = false
    var engineBusy = false
    var pendingCityEditors: [EditorWindow]?
    var busy: Bool { engineBusy || pendingCityEditors != nil }
    var fieldWriting = false
    var fieldQueue: [FieldUpdate] = []
    var fieldWriteTask: Task<Void, Error>?
    var previewLayerWrites: [String: PreviewSceneIdentity] = [:]
    var toolTasks: [String: (epoch: Int, target: String)] = [:]
    var viewerURL: URL?
    var selectedBounds: [Double]?
    var randomSpot: RandomSpotController?
    var selectedObject: JSON?
    var selectedLayer: String?
    var activeExport: ExportController?
    var pendingQuality: (String, PreviewQuality)?
    var staticMode = false
    var staticRefreshPending = false
    var staticPreviewID: String?
    var browserPreviewID: String?
    let staticPreview = NSImageView()
    var buildingList: BuildingList?
    var cityDraft: CityDraft?
    var draftSettingsOpen = false
    var draftWorkflowOpen = false
    var presentedScene: PreviewSceneIdentity?
    var themeEditor: ThemeEditor?
    var openingThemeEditor = false
    var showingSatelliteGuidance = false
    var presentedThemeUpdate = ""
    var poiEditor: PoiEditor?
    var textEditor: TextIconEditor?
    var sidebarForm: FormView?
    var inspectorForm: FormView?
    var transitForm: FormView?
    let sidebarNavigation = SidebarNavigation()
    var transitSchema: JSON = [:]
    var metroAppliedFingerprint: String?
    var metroSettingsStatus: NSTextField?
    var metroGenerateButton: ActionButton?
    var transitSelectionSummary: NSTextField?
    var textEditorPages: [String: String] = [:]
    let status = NSTextField(labelWithString: localized("正在启动渲染引擎…"))
    var commandButtons: [NSButton] = []
    var openCityMenuItem: NSMenuItem?
    var openProjectMenuItem: NSMenuItem?
    var rmpMenuItem: NSMenuItem?
    var rmpKeyMonitor: Any?
    var rmpFacilitiesPopover: NSPopover?
    private let rmpPlacementLabel = NSTextField(wrappingLabelWithString: "")
    var terminating = false
    var terminationPending = false
    #if CMM_INTEGRATION_CHECKS
    var smoke: NativeSmoke?
    #endif
    var window: NSWindow!
    let root = WorkspaceRootView()
    let split = NSSplitViewController()
    let contentSplit = NSSplitViewController()
    let workspace = NSView()
    let leftContent = NSView()
    let rightContent = NSView()
    let leftHeader = row([], gap: 8)
    let rightHeader = row([], gap: 8)
    let sectionState = SectionState()
    let restoreSidebar = NSButton()
    let restoreInspector = NSButton()
    var restoringOutline = false
    var mapInspectorCollapsed: Bool?
    var lastFailureDetails: String?
    var recentErrors: [String] = []
    var diagnosticsPanel: DiagnosticsPanel?
    private lazy var statusDetails = ActionButton("查看报错") { [weak self] in
        guard let self, let details = self.lastFailureDetails else { return }
        self.showTextWindow("报错详情", details)
    }
    private let statusIcon = NSImageView()
    let statusSpinner = NSProgressIndicator()
    private lazy var statusCancel = ActionButton("取消任务") { [weak self] in self?.cancelTask() }
    private var statusExpiry: Task<Void, Never>?
    enum CanvasStatusKind { case message, progress, ready }
    private var statusKind: CanvasStatusKind = .message
    private var statusDismissed = true
    var sidebarObservation: NSKeyValueObservation?
    var inspectorObservation: NSKeyValueObservation?
    lazy var layerNodes: [LayerNode] = zip(["base", "city", "transport", "labels", "regions", "other"], layerGroups).map { id, group in
        LayerNode(id: id, title: group.0, children: group.1.map { LayerNode(id: layerKeys[$0.0]!, title: $0.0, icon: $0.1) })
    }
    lazy var mapPlaceholder = PreviewPlaceholder(open: { [weak self] in self?.execute("打开城市…") },
        cancel: { [weak self] in self?.cancelTask() },
        retry: { [weak self] in self?.execute("重新启动交互预览") })
    private weak var canvasTools: NSView?
    let left = material(.sidebar, blending: .behindWindow)
    let right = material(.sidebar, blending: .behindWindow)
    let center = NSView()
    let band = material(.headerView, blending: .behindWindow)
    var bandHeight: NSLayoutConstraint!
    let tabs = NSSegmentedControl(labels: ["开始", "样式", "创建与工具"], trackingMode: .selectOne, target: nil, action: nil)
    let metroToolbarTitle = label("地铁示意图", 13, .semibold)
    private weak var workspaceToolbarItem: NSToolbarItem?
    let table = NSOutlineView()
    let collapse = NSButton()
    var leftItem: NSSplitViewItem!
    var rightItem: NSSplitViewItem!
    var page = 0
    var themes: [MapTheme] = []
    var gallery: NSPopover?
    var panels: [NSWindow] = []
    var openingRegionSelector = false

    func button(_ title: String, _ icon: String? = nil, _ action: Selector? = nil) -> NSButton {
        let b = NSButton(title: localized(title), target: self, action: action ?? #selector(commandAction(_:)))
        b.bezelStyle = .rounded; b.font = .systemFont(ofSize: 12)
        if let icon = icon { b.image = symbol(icon, size: 15); b.imagePosition = .imageLeading }
        b.setContentHuggingPriority(.required, for: .horizontal)
        b.setContentCompressionResistancePriority(.required, for: .horizontal)
        b.identifier = NSUserInterfaceItemIdentifier(title); commandButtons.append(b)
        return b
    }
    func largeButton(_ title: String, _ icon: String, _ action: Selector? = nil) -> NSButton {
        let b = button(title, icon, action)
        b.bezelStyle = .recessed
        b.showsBorderOnlyWhileMouseInside = true
        b.image = symbol(icon, size: 18)
        b.imagePosition = .imageLeading
        b.imageHugsTitle = true
        b.font = .systemFont(ofSize: 13)
        b.attributedTitle = NSAttributedString(string: "\u{2002}" + localized(title), attributes: [.font: NSFont.systemFont(ofSize: 13)])
        b.contentTintColor = title == "导出…" ? .controlAccentColor : .labelColor
        b.setAccessibilityLabel(title)
        b.toolTip = title
        NSLayoutConstraint.activate([b.heightAnchor.constraint(equalToConstant: 32), b.widthAnchor.constraint(equalToConstant: ceil(b.fittingSize.width))])
        return b
    }
    func bandTextButton(_ title: String) -> NSButton {
        let b = button(title)
        b.bezelStyle = .regularSquare; b.isBordered = false; b.alignment = .left
        return b
    }
    func popup(_ values: [String]) -> NSPopUpButton {
        let v = NSPopUpButton(frame: .zero, pullsDown: false)
        v.addItems(withTitles: values); v.font = .systemFont(ofSize: 12)
        return v
    }
    func menuButton(_ title: String, entries: [(String, Selector?)]) -> NSPopUpButton {
        let b = NSPopUpButton(frame: .zero, pullsDown: true)
        b.font = .systemFont(ofSize: 12); b.bezelStyle = .recessed; b.addItem(withTitle: title)
        for (name, action) in entries {
            let item = NSMenuItem(title: localized(name), action: action ?? #selector(commandAction(_:)), keyEquivalent: "")
            item.identifier = NSUserInterfaceItemIdentifier(name)
            item.target = self

            b.menu?.addItem(item)
        }
        b.menu?.autoenablesItems = true
        return b
    }
    func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1340, height: 790), styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        window.title = localized("城市地图"); window.titleVisibility = .hidden
        window.minSize = NSSize(width: 1100, height: 700)
        window.isReleasedWhenClosed = false; window.toolbarStyle = .unified
        window.titlebarAppearsTransparent = true
        root.wantsLayer = true
        root.appearanceChanged = { [weak self] in self?.updateViewerAppearance() }
        window.isOpaque = false
        window.backgroundColor = .clear
        window.contentView = root
        pin(material(.underWindowBackground, blending: .behindWindow), in: root)
        tabs.selectedSegment = 0; tabs.segmentStyle = .texturedRounded
        tabs.target = self; tabs.action = #selector(changePage)
        let toolbar = NSToolbar(identifier: "WorkspaceToolbar")
        toolbar.delegate = self; toolbar.displayMode = .iconOnly
        toolbar.centeredItemIdentifiers = [NSToolbarItem.Identifier("tabs")]
        window.toolbar = toolbar
        let safeTop = (window.contentLayoutGuide as! NSLayoutGuide).topAnchor

        split.splitView = MapSplitView(); split.splitView.isVertical = true; split.splitView.dividerStyle = .thin
        contentSplit.splitView = MapSplitView(); contentSplit.splitView.isVertical = true; contentSplit.splitView.dividerStyle = .thin
        let lv = NSViewController(); lv.view = left
        let wv = NSViewController(); wv.view = workspace
        let cv = NSViewController(); cv.view = center
        let rv = NSViewController(); rv.view = right
        leftItem = NSSplitViewItem(viewController: lv)
        leftItem.minimumThickness = 220; leftItem.maximumThickness = 300; leftItem.canCollapse = true
        leftItem.holdingPriority = NSLayoutConstraint.Priority(rawValue: 260)
        let workItem = NSSplitViewItem(viewController: wv); workItem.minimumThickness = 720
        let middle = NSSplitViewItem(viewController: cv); middle.minimumThickness = 500
        rightItem = NSSplitViewItem(viewController: rv)
        rightItem.minimumThickness = 220; rightItem.maximumThickness = 320; rightItem.canCollapse = true
        rightItem.holdingPriority = NSLayoutConstraint.Priority(rawValue: 260)
        sidebarObservation = leftItem.observe(\.isCollapsed, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in self?.syncSidebarVisibility() }
        }
        inspectorObservation = rightItem.observe(\.isCollapsed, options: [.new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in self?.syncInspectorVisibility() }
        }
        split.addSplitViewItem(leftItem); split.addSplitViewItem(workItem)
        contentSplit.addSplitViewItem(middle); contentSplit.addSplitViewItem(rightItem)
        wv.addChild(contentSplit)
        pin(split.view, in: root)
        workspace.addSubview(band); band.translatesAutoresizingMaskIntoConstraints = false
        workspace.addSubview(contentSplit.view); contentSplit.view.translatesAutoresizingMaskIntoConstraints = false
        bandHeight = band.heightAnchor.constraint(equalToConstant: 60)
        NSLayoutConstraint.activate([
            band.leadingAnchor.constraint(equalTo: workspace.leadingAnchor), band.trailingAnchor.constraint(equalTo: workspace.trailingAnchor),
            band.topAnchor.constraint(equalTo: safeTop), bandHeight,
            contentSplit.view.topAnchor.constraint(equalTo: band.bottomAnchor),
            contentSplit.view.leadingAnchor.constraint(equalTo: workspace.leadingAnchor),
            contentSplit.view.trailingAnchor.constraint(equalTo: workspace.trailingAnchor),
            contentSplit.view.bottomAnchor.constraint(equalTo: workspace.bottomAnchor)
        ])
        installPanel(left, header: leftHeader, content: leftContent, top: safeTop)
        installPanel(right, header: rightHeader, content: rightContent, top: right.topAnchor)
        pin(viewer.view, in: center)
        viewer.view.wantsLayer = true
        pin(mapPlaceholder, in: center)
        restoreSidebar.image = symbol("sidebar.left", size: 16)
        restoreSidebar.title = ""; restoreSidebar.bezelStyle = .texturedRounded
        restoreSidebar.target = self; restoreSidebar.action = #selector(toggleSidebar)
        restoreSidebar.isHidden = true; restoreSidebar.translatesAutoresizingMaskIntoConstraints = false
        center.addSubview(restoreSidebar)
        NSLayoutConstraint.activate([restoreSidebar.topAnchor.constraint(equalTo: center.topAnchor, constant: 10), restoreSidebar.leadingAnchor.constraint(equalTo: center.leadingAnchor, constant: 12)])
        restoreInspector.image = symbol("sidebar.right", size: 16)
        restoreInspector.title = ""; restoreInspector.bezelStyle = .texturedRounded
        restoreInspector.target = self; restoreInspector.action = #selector(toggleInspector)
        restoreInspector.isHidden = true; restoreInspector.translatesAutoresizingMaskIntoConstraints = false
        center.addSubview(restoreInspector)
        NSLayoutConstraint.activate([restoreInspector.topAnchor.constraint(equalTo: center.topAnchor, constant: 10), restoreInspector.trailingAnchor.constraint(equalTo: center.trailingAnchor, constant: -12)])
        syncSidebarVisibility(); syncInspectorVisibility()
        buildSidebar(); buildInspector(); buildCanvasTools(); rebuildBand()
        showMapState(.empty("尚未打开城市"))
        window.delegate = self
        window.setContentSize(NSSize(width: 1340, height: 790)); window.center()
        root.layoutSubtreeIfNeeded()
        split.splitView.setPosition(240, ofDividerAt: 0)
        contentSplit.splitView.setPosition(contentSplit.view.bounds.width - 260, ofDividerAt: 0)
    }
    func installPanel(_ panel: NSView, header: NSStackView, content: NSView, top: NSLayoutYAxisAnchor) {
        panel.addSubview(header); panel.addSubview(content); content.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            header.topAnchor.constraint(equalTo: top), header.leadingAnchor.constraint(equalTo: panel.leadingAnchor, constant: 16),
            header.trailingAnchor.constraint(equalTo: panel.trailingAnchor, constant: -12), header.heightAnchor.constraint(equalToConstant: 48),
            content.topAnchor.constraint(equalTo: header.bottomAnchor), content.leadingAnchor.constraint(equalTo: panel.leadingAnchor),
            content.trailingAnchor.constraint(equalTo: panel.trailingAnchor), content.bottomAnchor.constraint(equalTo: panel.bottomAnchor)
        ])
    }
    func panelHeader(_ header: NSStackView, title: String, back: Bool = false, inspector: Bool = false) {
        header.arrangedSubviews.forEach { header.removeArrangedSubview($0); $0.removeFromSuperview() }
        if back {
            let b = NSButton(image: symbol("chevron.left", size: 13)!, target: self, action: #selector(backToLayers))
            b.bezelStyle = .recessed; b.toolTip = localized("返回图层列表"); b.setAccessibilityLabel(b.toolTip)
            header.addArrangedSubview(b)
        }
        let heading = NSTextField(wrappingLabelWithString: localized(title))
        heading.font = .systemFont(ofSize: 13, weight: .semibold)
        heading.maximumNumberOfLines = 2
        heading.lineBreakMode = .byWordWrapping
        heading.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        header.addArrangedSubview(heading); header.addArrangedSubview(NSView())
        if inspector || header === leftHeader {
            let b = NSButton(image: symbol(inspector ? "sidebar.right" : "sidebar.left", size: 16)!, target: self, action: inspector ? #selector(toggleInspector) : #selector(toggleSidebar))
            b.bezelStyle = .recessed; b.toolTip = localized(inspector ? "收起右栏" : "收起左栏"); b.setAccessibilityLabel(b.toolTip)
            header.addArrangedSubview(b)
        }
    }
    func toolbarDefaultItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        [.flexibleSpace, NSToolbarItem.Identifier("tabs"), .flexibleSpace, NSToolbarItem.Identifier("collapse")]
    }
    func toolbarAllowedItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] { toolbarDefaultItemIdentifiers(toolbar) }
    func toolbar(_ toolbar: NSToolbar, itemForItemIdentifier id: NSToolbarItem.Identifier, willBeInsertedIntoToolbar flag: Bool) -> NSToolbarItem? {
        let item = NSToolbarItem(itemIdentifier: id)
        switch id.rawValue {
        case "tabs":
            item.label = localized("功能区"); item.view = tabs
            item.visibilityPriority = .user
            if flag { workspaceToolbarItem = item }
        case "collapse":
            collapse.image = symbol("chevron.up", size: 14); collapse.title = ""; collapse.bezelStyle = .texturedRounded
            collapse.target = self; collapse.action = #selector(toggleBand); collapse.toolTip = localized("收起功能区")
            item.label = localized("功能区"); item.view = collapse
        default: return nil
        }
        return item
    }
    func group(_ title: String, _ controls: [NSView]) -> NSView {
        let body = row(controls, gap: 8)
        body.setAccessibilityLabel(localized(title))
        body.heightAnchor.constraint(equalToConstant: 40).isActive = true
        return body
    }
    func currentThemeButton() -> NSView {
        let thumbnail = NSImageView()
        thumbnail.image = currentTheme?.thumbnail
        thumbnail.imageScaling = .scaleAxesIndependently
        NSLayoutConstraint.activate([thumbnail.widthAnchor.constraint(equalToConstant: 44), thumbnail.heightAnchor.constraint(equalToConstant: 32)])
        let b = button(localized(currentTheme?.name ?? "地图主题") + "  ⌄", nil, #selector(showThemes(_:)))
        b.bezelStyle = .recessed
        return row([thumbnail, b], gap: 8)
    }
    func rebuildBand() {
        if let item = workspaceToolbarItem {
            let metro = context == "metro"
            let workspaceView: NSView = metro ? metroToolbarTitle : tabs
            item.label = localized(metro ? "地铁示意图" : "功能区")
            if metro { metroToolbarTitle.stringValue = item.label }
            if item.view !== workspaceView { item.view = workspaceView }
        }
        commandButtons.removeAll()
        band.subviews.forEach { $0.removeFromSuperview() }
        if let draft = cityDraft { bandContent(draft.toolbar()); return }
        if context == "metro" { rebuildRmpBand(); return }
        if context == "transit" { rebuildTransitBand(); return }
        var groups: [NSView] = []
        let createButtons = { [self] in [largeButton("公共交通图", "bus"), largeButton("地铁示意图", "tram"), largeButton("随机特写", "viewfinder")] }
        if page == 0 {
            groups = [
                group("城市", [largeButton("打开城市…", "folder"), menuButton(localized("城市"), entries: [("最近打开", nil), ("城市概览与建筑列表", nil)])]),
                group("快速创建", createButtons()),
                group("地图主题", [currentThemeButton(), largeButton("编辑主题", "paintpalette"), largeButton("创建地图草稿", "pencil.and.outline")]),
                group("输出", [largeButton("导出…", "square.and.arrow.up"), largeButton("导出网页…", "globe", #selector(showWebExport))])]
        } else if page == 1 {
            groups = [group("地图主题", [currentThemeButton()]), group("主题设置", [largeButton("编辑主题", "paintpalette")]),
                group("兴趣点设置", [largeButton("地点图标样式", "mappin.and.ellipse", #selector(showPoiStyles(_:))), largeButton("图标映射", "square.grid.3x3")]),
                group("文字与图标", MapSymbolCategory.allCases.map { largeButton($0.title, $0.symbolName) })]
        } else {
            groups = [group("创建", createButtons()), group("地图作品", [largeButton("立交合集", "point.topleft.down.curvedto.point.bottomright.up")]),
                group("外部编辑", [bandTextButton("从城市数据生成 RMG…")]),
                group("维护", [largeButton("图标映射…", "square.grid.3x3"), largeButton("诊断信息", "stethoscope"), largeButton("清理缓存", "trash")])]
        }
        var content: [NSView] = []
        for (i, group) in groups.enumerated() {
            if i > 0 {
                let divider = rule()
                divider.translatesAutoresizingMaskIntoConstraints = false
                NSLayoutConstraint.activate([divider.widthAnchor.constraint(equalToConstant: 1), divider.heightAnchor.constraint(equalToConstant: 40)])
                content.append(divider)
            }
            content.append(group)
        }
        bandContent(content)
        updateCommands()
    }
    func buildSidebar() {
        prepareSidebar("map.layers")
        sidebarNavigation.mapLayer = nil
        selectedLayer = nil; sidebarForm = nil; transitForm = nil
        leftContent.subviews.forEach { $0.removeFromSuperview() }
        panelHeader(leftHeader, title: "图层设置")
        if table.tableColumns.isEmpty {
            let col = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("Layer"))
            table.addTableColumn(col); table.outlineTableColumn = col
        }
        table.headerView = nil; table.dataSource = self; table.delegate = self
        table.backgroundColor = .clear; table.style = .sourceList
        table.autoresizingMask = [.width]
        table.columnAutoresizingStyle = .lastColumnOnlyAutoresizingStyle
        table.rowSizeStyle = .medium; table.intercellSpacing = NSSize(width: 0, height: 4)
        table.indentationPerLevel = 10; table.floatsGroupRows = false
        table.target = self; table.action = #selector(selectLayer)
        let scroll = NSScrollView(); scroll.drawsBackground = false; scroll.hasVerticalScroller = true; scroll.autohidesScrollers = true
        scroll.documentView = table; pin(scroll, in: leftContent, inset: 8)
        leftContent.layoutSubtreeIfNeeded()
        table.sizeLastColumnToFit()
        reloadLayerList()
    }
    func reloadLayerList() {
        restoringOutline = true
        table.reloadData()
        for node in layerNodes where !sectionState.collapsed.contains("layers." + node.id) { table.expandItem(node) }
        restoringOutline = false
    }
    func outlineView(_ outlineView: NSOutlineView, numberOfChildrenOfItem item: Any?) -> Int { (item as? LayerNode)?.children.count ?? layerNodes.count }
    func outlineView(_ outlineView: NSOutlineView, child index: Int, ofItem item: Any?) -> Any { ((item as? LayerNode)?.children ?? layerNodes)[index] }
    func outlineView(_ outlineView: NSOutlineView, isItemExpandable item: Any) -> Bool { !(item as! LayerNode).children.isEmpty }
    func outlineView(_ outlineView: NSOutlineView, isGroupItem item: Any) -> Bool { !(item as! LayerNode).children.isEmpty }
    func outlineView(_ outlineView: NSOutlineView, shouldSelectItem item: Any) -> Bool { (item as! LayerNode).children.isEmpty }
    func outlineViewItemDidCollapse(_ notification: Notification) {
        guard !restoringOutline, let node = notification.userInfo?["NSObject"] as? LayerNode else { return }
        sectionState.collapsed.insert("layers." + node.id)
    }
    func outlineViewItemDidExpand(_ notification: Notification) {
        guard !restoringOutline, let node = notification.userInfo?["NSObject"] as? LayerNode else { return }
        sectionState.collapsed.remove("layers." + node.id)
    }
    func outlineView(_ outlineView: NSOutlineView, viewFor tableColumn: NSTableColumn?, item: Any) -> NSView? {
        let node = item as! LayerNode
        guard node.children.isEmpty else { return label(node.title, 11, .semibold, .secondaryLabelColor) }
        let icon = NSImageView(image: symbol(node.icon, size: 16) ?? symbol("square")!)
        icon.contentTintColor = .controlAccentColor; icon.widthAnchor.constraint(equalToConstant: 20).isActive = true
        let visibility = NSButton(checkboxWithTitle: "", target: self, action: #selector(changeCheck))
        visibility.identifier = NSUserInterfaceItemIdentifier("layer:" + node.title)
        visibility.state = defaultVisibility(node.title) ? .on : .off
        visibility.font = .systemFont(ofSize: 12)
        visibility.setAccessibilityLabel(localized(node.title) + " — " + localized("显示图层"))
        visibility.toolTip = localized("显示图层")
        if node.id == "satellite" { visibility.isEnabled = snapshot["satelliteAvailable"] as? Bool == true }
        let open: () -> Void = { [weak self] in self?.showLayer(node.title) }
        let detailLabel = localized("打开图层设置") + " · " + localized(node.title)
        let name = ActionButton(node.title, open)
        name.alignment = .left
        name.lineBreakMode = .byTruncatingTail
        name.font = .systemFont(ofSize: 13)
        name.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        name.setContentHuggingPriority(.defaultLow, for: .horizontal)
        name.setAccessibilityLabel(detailLabel)
        let chevron = ActionButton("", open)
        chevron.identifier = NSUserInterfaceItemIdentifier("layer.open." + node.id)
        chevron.image = symbol("chevron.forward", size: 10)
        chevron.imagePosition = .imageOnly
        chevron.contentTintColor = .secondaryLabelColor
        chevron.widthAnchor.constraint(equalToConstant: 22).isActive = true
        chevron.setAccessibilityLabel(detailLabel)
        for button in [name, chevron] {
            button.bezelStyle = .recessed
            button.showsBorderOnlyWhileMouseInside = true
            button.toolTip = detailLabel
        }
        let cell = NSTableCellView()
        for view in [icon, name, visibility, chevron] {
            view.translatesAutoresizingMaskIntoConstraints = false
            cell.addSubview(view)
            view.centerYAnchor.constraint(equalTo: cell.centerYAnchor).isActive = true
        }
        NSLayoutConstraint.activate([
            icon.leadingAnchor.constraint(equalTo: cell.leadingAnchor),
            name.leadingAnchor.constraint(equalTo: icon.trailingAnchor, constant: 4),
            name.trailingAnchor.constraint(equalTo: visibility.leadingAnchor, constant: -4),
            visibility.trailingAnchor.constraint(equalTo: chevron.leadingAnchor, constant: -4),
            chevron.trailingAnchor.constraint(equalTo: cell.trailingAnchor, constant: -3)
        ])
        return cell
    }
    @objc func selectLayer() {
        guard let node = table.item(atRow: table.clickedRow) as? LayerNode, node.children.isEmpty else { return }
        showLayer(node.title)
    }

    func buildCanvasTools() {
        let tools = material(.popover)
        canvasTools = tools
        tools.wantsLayer = true
        tools.layer?.cornerRadius = 14
        tools.layer?.masksToBounds = true
        status.font = .systemFont(ofSize: 15, weight: .medium)
        status.alignment = .center
        status.lineBreakMode = .byTruncatingTail
        status.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        statusIcon.image = symbol("checkmark.circle.fill", size: 19)
        statusIcon.contentTintColor = .systemGreen
        statusIcon.setAccessibilityElement(false)
        statusIcon.isHidden = true
        statusSpinner.style = .spinning
        statusSpinner.controlSize = .small
        statusSpinner.isIndeterminate = true
        statusSpinner.isDisplayedWhenStopped = false
        statusSpinner.isHidden = true
        statusSpinner.setAccessibilityElement(false)
        NSLayoutConstraint.activate([
            statusSpinner.widthAnchor.constraint(equalToConstant: 18),
            statusSpinner.heightAnchor.constraint(equalToConstant: 18)
        ])
        let message = row([statusSpinner, statusIcon, status, statusDetails, statusCancel], gap: 9)
        let cancelPlacement = ActionButton(RmpNativeCommand.cancel.rawValue) { [weak self] in self?.runRmpTool(.cancel) }
        rmpPlacementLabel.font = .systemFont(ofSize: 12)
        rmpPlacementLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        let placement = row([rmpPlacementLabel, cancelPlacement])
        placement.isHidden = true
        let contents = column([message, placement], gap: 5)
        contents.edgeInsets = NSEdgeInsets(top: 10, left: 20, bottom: 10, right: 20)
        pin(contents, in: tools)
        tools.translatesAutoresizingMaskIntoConstraints = false
        center.addSubview(tools)
        NSLayoutConstraint.activate([
            tools.centerXAnchor.constraint(equalTo: center.centerXAnchor),
            tools.bottomAnchor.constraint(equalTo: center.bottomAnchor, constant: -20),
            tools.widthAnchor.constraint(lessThanOrEqualTo: center.widthAnchor, constant: -32),
            tools.heightAnchor.constraint(greaterThanOrEqualToConstant: 44)
        ])
    }

    func showStatus(_ message: String, kind: CanvasStatusKind = .message) {
        statusExpiry?.cancel()
        statusDismissed = false
        status.stringValue = message
        statusKind = kind
        statusIcon.isHidden = kind != .ready
        refreshStatusVisibility()
        guard kind == .ready else { return }
        statusExpiry = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard !Task.isCancelled, let self else { return }
            self.statusDismissed = true
            self.refreshStatusVisibility()
        }
    }

    func refreshLocalizedStatusControls() {
        statusDetails.title = localized("查看报错")
        statusCancel.title = localized("取消任务")
        let cityName = textValue(object(snapshot["cityInfo"])["name"])
        window.title = cityName.isEmpty ? localized("城市地图") : cityName
    }

    func refreshStatusVisibility() {
        statusDetails.isHidden = lastFailureDetails == nil || status.stringValue != localized("操作未完成")
        statusCancel.isHidden = rmp.busy && context == "metro"
            ? !rmp.canCancelGeneration : !busy || statusKind == .ready
        let placementActive = rmpToolEnabled(.cancel)
        rmpPlacementLabel.superview?.isHidden = !placementActive
        if placementActive { rmpPlacementLabel.stringValue = rmp.placementHint }
        status.superview?.isHidden = statusDismissed
        let canvasHidden = context == "metro" ? statusDismissed && !placementActive : statusDismissed
            || (mapPlaceholder.state != .ready && statusDetails.isHidden && pendingCityEditors == nil && cityDraft?.working != true)
        canvasTools?.isHidden = canvasHidden
        let showSpinner = statusKind == .progress && !statusDismissed && !canvasHidden
        if statusSpinner.isHidden == showSpinner {
            statusSpinner.isHidden = !showSpinner
            if showSpinner { statusSpinner.startAnimation(nil) }
            else { statusSpinner.stopAnimation(nil) }
        }
        diagnosticsPanel?.refreshMapStatus()
    }

    func dismissStatus() {
        statusExpiry?.cancel()
        statusDismissed = true
        refreshStatusVisibility()
    }

    func showMapState(_ state: PreviewPlaceholder.State) {
        let readinessChanged = (state == .ready) != (mapPlaceholder.state == .ready)
        let reveal = state == .ready && readinessChanged && !staticMode && context != "metro"
        mapPlaceholder.show(state)
        // Keep WebGL visible until its first frame.
        viewer.view.layer?.opacity = state == .ready ? 1 : 0
        viewer.view.setAccessibilityHidden(state != .ready)
        if state != .ready { viewer.view.layer?.removeAnimation(forKey: "revealPreview") }
        if reveal && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            let fade = CABasicAnimation(keyPath: "opacity")
            fade.fromValue = 0
            fade.toValue = 1
            fade.duration = 0.16
            viewer.view.layer?.add(fade, forKey: "revealPreview")
        }
        mapPlaceholder.isHidden = context == "metro" || state == .ready
        refreshStatusVisibility()
        if readinessChanged { sendPreviewUiState() }
    }

    @objc func showThemes(_ sender: NSButton) {
        let popover = NSPopover(); popover.behavior = .transient
        let vc = NSViewController()
        var cards: [NSView] = []
        for theme in themes {
            let selected = theme.id == currentTheme?.id
            let b = button(theme.name + (selected ? "  ✓" : ""), nil, #selector(selectTheme(_:)))
            b.identifier = NSUserInterfaceItemIdentifier("theme:" + theme.id)
            b.image = theme.thumbnail; b.image?.size = NSSize(width: 106, height: 72)
            b.imagePosition = .imageAbove; b.bezelStyle = .regularSquare
            b.setButtonType(.toggle); b.state = selected ? .on : .off
            b.widthAnchor.constraint(equalToConstant: 126).isActive = true; b.heightAnchor.constraint(equalToConstant: 106).isActive = true
            cards.append(b)
        }
        let rows = stride(from: 0, to: cards.count, by: 3).map { row(Array(cards[$0..<min($0 + 3, cards.count)])) }
        let content = column([label("地图主题", 14, .semibold)] + rows, gap: 12)
        let scroll = NSScrollView(); scroll.drawsBackground = false; scroll.hasVerticalScroller = true; scroll.autohidesScrollers = true
        scroll.contentView = TopAlignedClipView(); scroll.contentView.drawsBackground = false
        content.edgeInsets = NSEdgeInsets(top: 18, left: 18, bottom: 18, right: 18)
        scroll.documentView = content
        content.widthAnchor.constraint(equalTo: scroll.contentView.widthAnchor).isActive = true
        vc.view = scroll
        let size = NSSize(width: 434, height: min(406, 56 + CGFloat(rows.count) * 118))
        scroll.setFrameSize(size); vc.preferredContentSize = size
        popover.contentViewController = vc; popover.contentSize = size
        gallery = popover; popover.show(relativeTo: sender.bounds, of: sender, preferredEdge: .maxY)
    }









    @objc func backToLayers() { if context == "map" { buildSidebar() } else { showTransitSettings() } }
    @objc func changePage(_ sender: NSSegmentedControl) { page = sender.selectedSegment; rebuildBand(); if bandHeight.constant == 0 { toggleBand() } }
    @objc func toggleBand() {
        let collapsed = bandHeight.constant > 0
        bandHeight.constant = collapsed ? 0 : 60; band.isHidden = collapsed
        collapse.image = symbol(collapsed ? "chevron.down" : "chevron.up", size: 14)
        collapse.toolTip = localized(collapsed ? "展开功能区" : "收起功能区")
    }
    func configureMetroWorkspace(_ active: Bool) {
        if active {
            if mapInspectorCollapsed == nil { mapInspectorCollapsed = rightItem.isCollapsed }
            rightItem.isCollapsed = true
        } else if let collapsed = mapInspectorCollapsed {
            mapInspectorCollapsed = nil; rightItem.isCollapsed = collapsed
        }
        showMapState(mapPlaceholder.state)
        syncInspectorVisibility()
        window.isDocumentEdited = active && rmp.dirty
    }
    @objc func toggleInspector() {
        if context == "metro" {
            runRmpTool(.details)
            return
        }
        rightItem.isCollapsed.toggle()
        syncInspectorVisibility()
    }
    @objc func toggleSidebar() {
        leftItem.isCollapsed.toggle()
        syncSidebarVisibility()
    }
    func syncSidebarVisibility() {
        restoreSidebar.isHidden = !leftItem.isCollapsed
        restoreSidebar.toolTip = localized("展开左栏")
        restoreSidebar.setAccessibilityLabel(restoreSidebar.toolTip)
    }
    func syncInspectorVisibility() {
        restoreInspector.isHidden = context == "metro" || !rightItem.isCollapsed
        restoreInspector.toolTip = localized("展开右栏")
        restoreInspector.setAccessibilityLabel(restoreInspector.toolTip)
    }






}
