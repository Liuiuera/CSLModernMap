import AppKit

enum RmpNativeCommand: String {
    case title = "添加标题", legend = "添加图例", facilities = "添加设施图标…"
    case text = "普通文字", helper = "辅助连接点", fill = "填色区域", cancel = "取消放置"
    case fit = "适应画布", rotateLeft = "逆时针旋转", rotateRight = "顺时针旋转"
    case details = "显示或隐藏对象属性", grid = "网格", snap = "吸附", water = "水域控制点"
    case names = "显示站名", defaultNames = "显示默认站名"
    case undo = "撤销工程", redo = "重做工程"

    static let elements: [Self] = [.title, .legend, .facilities, .text, .helper, .fill, .cancel]
    static let view: [Self] = [.fit, .rotateLeft, .rotateRight, .details, .grid, .snap, .water, .names, .defaultNames]

    var viewOption: String? {
        switch self {
        case .details: return "detailsVisible"
        case .grid: return "gridLines"
        case .snap: return "snapLines"
        case .water: return "showWaterControls"
        case .names: return "showNames"
        case .defaultNames: return "showDefaultNames"
        default: return nil
        }
    }
}

extension InterfaceController {
    var nativeTextEditor: NSTextView? {
        guard let editor = NSApp.keyWindow?.firstResponder as? NSTextView, editor.isEditable else { return nil }
        return editor
    }

    func rmpToolEnabled(_ tool: RmpNativeCommand) -> Bool {
        guard context == "metro", rmp.hasProject, !busy, !fieldWriting, !rmp.busy, !rmp.placing else { return false }
        switch tool {
        case .cancel: return rmp.placementPending
        case .undo: return rmp.state["canUndo"] as? Bool == true
        case .redo: return rmp.state["canRedo"] as? Bool == true
        case .defaultNames: return rmp.state["showNames"] as? Bool == true
        case .facilities: return !array(rmp.tools["facilities"]).isEmpty
        default: return true
        }
    }

    func runRmpTool(_ tool: RmpNativeCommand) {
        guard rmpToolEnabled(tool) else { return }
        if let option = tool.viewOption {
            rmp.command("setViewOptions", ["options": [option: rmp.state[option] as? Bool != true]])
            return
        }
        switch tool {
        case .title:
            var insertion = ["kind": "title"]
            let city = rmp.exportCityName
            if !city.isEmpty {
                insertion["title"] = localized("{cityName}地铁图").replacingOccurrences(of: "{cityName}", with: city)
            }
            rmp.command("beginInsert", ["insertion": insertion])
        case .legend: rmp.command("beginInsert", ["insertion": ["kind": "legend"]])
        case .facilities: showRmpFacilities()
        case .text: rmp.command("setMode", ["mode": "misc-node-text"])
        case .helper: rmp.command("setMode", ["mode": "misc-node-virtual"])
        case .fill: rmp.command("setMode", ["mode": "misc-node-fill"])
        case .cancel: rmp.command("cancelInsert")
        case .fit: rmp.command("fit")
        case .rotateLeft, .rotateRight: rmp.command("rotateMap", ["delta": tool == .rotateLeft ? -90 : 90])
        case .undo, .redo: rmp.command(tool == .undo ? "undo" : "redo")
        default: break
        }
    }

    func performWorkspaceHistory(undo: Bool) {
        if let draft = cityDraft { draft.editor.restoreHistory(undo: undo); return }
        if let editor = nativeTextEditor {
            if undo { editor.undoManager?.undo() } else { editor.undoManager?.redo() }
        } else if NSApp.keyWindow === window {
            let direction = undo ? "undo" : "redo"
            if rmp.webHasFocus { rmp.command("focusedHistory", ["direction": direction]) }
            else { runRmpTool(undo ? .undo : .redo) }
        }
    }

    func routeMetroKey(_ event: NSEvent) -> NSEvent? {
        guard context == "metro", NSApp.keyWindow === window, event.window === window,
              window.attachedSheet == nil, NSApp.modalWindow == nil, rmpFacilitiesPopover?.isShown != true else { return event }
        let modifiers = event.modifierFlags.intersection([.command, .shift, .control, .option])
        if rmp.webHasFocus, modifiers.contains(.command), !modifiers.contains(.control), !modifiers.contains(.option),
           event.charactersIgnoringModifiers?.lowercased() == "z" {
            performWorkspaceHistory(undo: !modifiers.contains(.shift))
            return nil
        }
        if event.keyCode == 53, modifiers.isEmpty, !rmp.webHasFocus, rmpToolEnabled(.cancel) {
            runRmpTool(.cancel)
            return nil
        }
        return event
    }

    func showRmpFacilities() {
        if rmpFacilitiesPopover?.isShown == true { return }
        let projectID = rmp.projectID
        let picker = RmpFacilityPicker(tools: array(rmp.tools["facilities"])) { [weak self] id in
            guard let self else { return }
            self.rmpFacilitiesPopover?.close()
            guard self.rmp.projectID == projectID, self.rmpToolEnabled(.facilities) else { return }
            self.rmp.command("beginInsert", ["insertion": ["kind": "facility", "facility": id]])
        }
        let popover = NSPopover()
        popover.behavior = .transient
        popover.contentSize = NSSize(width: 320, height: 360)
        popover.contentViewController = picker
        rmpFacilitiesPopover = popover
        let anchor = band.isHidden ? center : band
        let bounds = anchor.bounds
        let edge = NSRect(x: bounds.midX - 20, y: bounds.maxY - 1, width: 40, height: 1)
        popover.show(relativeTo: edge, of: anchor, preferredEdge: .maxY)
        picker.view.window?.makeFirstResponder(picker.search)
    }
}

@MainActor final class RmpFacilityPicker: NSViewController, NSSearchFieldDelegate {
    let search = NSSearchField()
    let results = FormView()
    private let tools: [JSON]
    private let select: (String) -> Void

    init(tools: [JSON], select: @escaping (String) -> Void) {
        self.tools = tools
        self.select = select
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) { fatalError() }

    override func loadView() {
        view = NSView(frame: NSRect(x: 0, y: 0, width: 320, height: 360))
        search.placeholderString = localized("搜索设施图标")
        search.setAccessibilityLabel(localized("搜索设施图标"))
        search.delegate = self
        results.stack.spacing = 4
        let contents = column([search, results], gap: 8)
        pin(contents, in: view, inset: 12)
        for control in [search, results] { control.widthAnchor.constraint(equalTo: contents.widthAnchor).isActive = true }
        results.setContentHuggingPriority(.defaultLow, for: .vertical)
        rebuild()
    }

    func controlTextDidChange(_ obj: Notification) { rebuild() }

    private func rebuild() {
        let terms = search.stringValue.split(whereSeparator: { $0.isWhitespace })
        results.reset()
        for item in tools {
            let id = textValue(item["id"]), name = textValue(item["label"])
            guard !id.isEmpty, terms.allSatisfy({ "\(name) \(id)".localizedCaseInsensitiveContains(String($0)) }) else { continue }
            let button = ActionButton(name) { [weak self] in self?.select(id) }
            button.identifier = NSUserInterfaceItemIdentifier("rmp.facility." + id)
            button.alignment = .left
            if let url = Bundle.main.url(forResource: id, withExtension: "svg", subdirectory: "RmpFacilities"),
               let image = NSImage(contentsOf: url) {
                image.size = NSSize(width: 24, height: 24)
                button.image = image
                button.imagePosition = .imageLeading
            }
            results.add(button)
        }
        if results.stack.arrangedSubviews.isEmpty {
            results.add(NSTextField(wrappingLabelWithString: localized("没有匹配的设施图标。")))
        }
        results.restoreScroll(0)
    }
}
