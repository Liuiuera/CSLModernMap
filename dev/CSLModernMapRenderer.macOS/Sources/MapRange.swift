import AppKit

struct MapRangeProfile {
    let city: MapRangeBounds
    let world: MapRangeBounds
    let limit: Int
    let presets: [Int]
    let hasOutside: Bool
    private let cityImage: NSImage?
    private let worldImage: NSImage?

    init?(_ data: JSON) {
        guard data["available"] as? Bool == true,
              let city = MapRangeBounds(data["cityBounds"]), let world = MapRangeBounds(data["worldBounds"]) else { return nil }
        self.city = city; self.world = world
        limit = data["limit"] as? Int ?? 0
        presets = data["presets"] as? [Int] ?? []
        hasOutside = data["hasOutside"] as? Bool == true
        let previews = object(data["previews"])
        func image(_ key: String) -> NSImage? {
            guard let url = URL(string: textValue(previews[key])), url.isFileURL else { return nil }
            return NSImage(contentsOf: url)
        }
        guard let cityImage = image("city"), let worldImage = city == world ? cityImage : image("world") else { return nil }
        self.cityImage = cityImage; self.worldImage = worldImage
    }

    func image(for area: MapRangeBounds) -> NSImage? {
        area == world ? worldImage : cityImage
    }
}

@MainActor final class MapRangeCanvas: NSView {
    override var isFlipped: Bool { true }
    var area: MapRangeBounds? { didSet { needsDisplay = true } }
    var selection: MapRangeBounds? { didSet { needsDisplay = true } }
    var image: NSImage? { didSet { needsDisplay = true } }
    var editable = false
    var canMove = false
    var changed: ((MapRangeBounds) -> Void)?
    var previewChanged: ((MapRangeBounds) -> Void)?
    private var start: CGPoint?
    private var original: MapRangeBounds?
    private var moving = false
    private var forceRedraw = false

    func redrawSelection() { forceRedraw = true }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        guard let area else { return }
        let frame = area.fitted(in: bounds.insetBy(dx: 2, dy: 2))
        NSColor.controlBackgroundColor.setFill(); frame.fill()
        image?.draw(in: frame, from: .zero, operation: .sourceOver, fraction: 1, respectFlipped: true, hints: nil)
        guard let selection else { return }
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath(rect: frame).addClip()
        let path = NSBezierPath(rect: area.rect(of: selection, in: frame))
        NSColor.systemBlue.withAlphaComponent(0.16).setFill(); path.fill()
        NSColor.systemBlue.setStroke(); path.lineWidth = 2; path.stroke()
        NSGraphicsContext.restoreGraphicsState()
    }

    override func mouseDown(with event: NSEvent) {
        guard editable, let area, let selection else { return }
        let frame = area.fitted(in: bounds.insetBy(dx: 2, dy: 2))
        let p = convert(event.locationInWindow, from: nil)
        guard frame.contains(p) else { return }
        start = p; original = selection
        moving = canMove && !forceRedraw && area.rect(of: selection, in: frame).contains(p)
    }

    override func mouseDragged(with event: NSEvent) {
        guard let start, let original, let area else { return }
        let frame = area.fitted(in: bounds.insetBy(dx: 2, dy: 2))
        let p = convert(event.locationInWindow, from: nil)
        let a = area.point(start, in: frame), b = area.point(p, in: frame)
        if moving { selection = original.moved(by: CGPoint(x: b.x-a.x, y: b.y-a.y), inside: area) }
        else { selection = MapRangeBounds([min(a.x,b.x), min(a.y,b.y), max(a.x,b.x), max(a.y,b.y)]) }
        if let selection { previewChanged?(selection) }
    }

    override func mouseUp(with event: NSEvent) {
        guard let start, let original else { return }
        let end = convert(event.locationInWindow, from: nil)
        mouseDragged(with: event)
        self.start = nil; self.original = nil
        let dx = abs(end.x-start.x), dy = abs(end.y-start.y)
        if selection == nil || (moving ? dx < 3 && dy < 3 : dx < 3 || dy < 3) {
            selection = original; previewChanged?(original); return
        }
        forceRedraw = false
        if let selection { changed?(selection) }
    }

    func show(_ profile: MapRangeProfile, area: MapRangeBounds, selection: MapRangeBounds) {
        self.area = area; self.selection = selection; image = profile.image(for: area)
    }
}

@MainActor final class MapRangeChooser: NSObject {
    private let profile: MapRangeProfile
    private let engine: EngineClient
    private let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 750, height: 510), styleMask: [.titled], backing: .buffered, defer: false)
    private let canvas = MapRangeCanvas()
    private let mode = NSPopUpButton()
    private let tiles = NSPopUpButton()
    private let summary = label("")
    private let status = NSTextField(wrappingLabelWithString: "")
    private var selection: MapRangeBounds
    private var modes = ["city"]
    private var result: MapRangeBounds?
    private var presetTask: Task<Void, Never>?
    private lazy var apply = ActionButton("应用范围") { [weak self] in self?.finish(true) }
    private lazy var cancel = ActionButton("取消") { [weak self] in self?.finish(false) }
    private lazy var redraw = ActionButton("重新框选") { [weak self] in self?.redrawRange() }

    init(engine: EngineClient, profile: MapRangeProfile, initial: MapRangeBounds?) {
        self.engine = engine; self.profile = profile
        selection = initial.flatMap { profile.world.contains($0) ? $0 : nil } ?? profile.city
        super.init()
        window.title = localized("输出范围")
        window.isReleasedWhenClosed = false
        if profile.hasOutside { modes.append("world") }
        if profile.limit > 0 { modes.append("tiles") }
        modes.append("manual")
        mode.addItems(withTitles: modes.map { localized(["city":"全城", "world":"含外围地形", "tiles":"按地块选择", "manual":"手动绘制范围"][$0]!) })
        let initialMode = selection == profile.city ? "city" : (profile.hasOutside && selection == profile.world ? "world" : "manual")
        mode.selectItem(at: modes.firstIndex(of: initialMode)!)
        mode.target = self; mode.action = #selector(modeChanged)
        if profile.limit > 0 {
            tiles.addItems(withTitles: (1...profile.limit).map { "\($0) × \($0)" })
            tiles.selectItem(at: min(3, profile.limit)-1)
        }
        tiles.target = self; tiles.action = #selector(modeChanged)
        let presets = row(profile.presets.filter { (1...max(1, profile.limit)).contains($0) }.map { n in
            ActionButton("\(n)×\(n)") { [weak self] in
                guard let self, let index = self.modes.firstIndex(of: "tiles") else { return }
                self.tiles.selectItem(at: n-1); self.mode.selectItem(at: index); self.modeChanged()
            }
        }, gap: 5)
        status.textColor = .secondaryLabelColor
        let help = NSTextField(wrappingLabelWithString: localized("按住鼠标左键拖动以框选范围；手动范围内可拖动已选区域。"))
        help.textColor = .secondaryLabelColor
        let controls = column([label("输出范围", 16, .semibold), mode, label("每边地块数"), tiles, presets, redraw, summary, status, help], gap: 12)
        controls.widthAnchor.constraint(equalToConstant: 270).isActive = true
        for v in [mode, tiles, status, help] { v.widthAnchor.constraint(equalTo: controls.widthAnchor).isActive = true }
        canvas.widthAnchor.constraint(equalToConstant: 420).isActive = true
        canvas.heightAnchor.constraint(equalToConstant: 420).isActive = true
        let content = row([controls, canvas], gap: 18); content.alignment = .top
        let footer = row([NSView(), cancel, apply]); apply.keyEquivalent = "\r"; cancel.keyEquivalent = "\u{1b}"
        let layout = column([content, rule(), footer], gap: 12)
        footer.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true
        pin(layout, in: window.contentView!, inset: 18)
        canvas.changed = { [weak self] bounds in
            guard let self else { return }
            self.selection = bounds
            self.mode.selectItem(at: self.modes.firstIndex(of: "manual")!)
            self.refresh()
            self.status.stringValue = ""
        }
        canvas.previewChanged = { [weak self] bounds in self?.summary.stringValue = bounds.summary }
        refresh()
    }

    private var selectedMode: String { modes[mode.indexOfSelectedItem] }

    private func refresh() {
        tiles.isEnabled = selectedMode == "tiles"
        canvas.canMove = selectedMode == "manual"
        canvas.editable = true
        let area = selectedMode == "world" || !profile.city.contains(selection) ? profile.world : profile.city
        canvas.show(profile, area: area, selection: selection)
        summary.stringValue = selection.summary
        apply.isEnabled = true
    }

    @objc private func modeChanged() {
        presetTask?.cancel(); status.stringValue = ""
        switch selectedMode {
        case "city": selection = profile.city
        case "world": selection = profile.world
        case "tiles":
            tiles.isEnabled = true; canvas.editable = false; apply.isEnabled = false
            let count = tiles.indexOfSelectedItem + 1
            presetTask = Task { [weak self] in
                guard let self else { return }
                do {
                    let reply = try await engine.request("getRangePreset", ["tiles": count, "anchor": "center"])
                    try Task.checkCancellation()
                    guard let bounds = MapRangeBounds(reply["bounds"]) else { throw AppFailure(message: "范围宽度和高度必须大于 0。") }
                    selection = bounds; refresh()
                } catch is CancellationError {} catch {
                    guard !Task.isCancelled else { return }
                    status.stringValue = error.localizedDescription
                    mode.selectItem(at: modes.firstIndex(of: "manual")!); refresh()
                }
            }
            return
        default: break
        }
        refresh()
    }

    private func redrawRange() {
        mode.selectItem(at: modes.firstIndex(of: "manual")!); modeChanged()
        canvas.redrawSelection(); status.stringValue = localized("在地图上拖动绘制范围")
    }

    private func finish(_ accepted: Bool) {
        guard let parent = window.sheetParent, !accepted || apply.isEnabled else { return }
        presetTask?.cancel()
        result = accepted ? selection : nil
        parent.endSheet(window, returnCode: accepted ? .OK : .cancel)
    }

    func choose(parent: NSWindow) async -> MapRangeBounds? {
        guard parent.attachedSheet == nil else { return nil }
        return await withCheckedContinuation { continuation in
            parent.beginSheet(window) { [self] response in
                presetTask?.cancel(); window.orderOut(nil)
                continuation.resume(returning: response == .OK ? result : nil)
            }
        }
    }
}
