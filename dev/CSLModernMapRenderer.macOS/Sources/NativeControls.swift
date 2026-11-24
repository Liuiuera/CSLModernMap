import AppKit

func referenceSize(_ value: Any) -> Double {
    if let pair = value as? JSON { return (pair["fixed"] as? Double ?? 0) + (pair["scaled"] as? Double ?? 0) }
    return (value as? NSNumber)?.doubleValue ?? 0
}

func resizedAppearanceValue(_ value: Any, to total: Double) -> Any {
    guard let pair = value as? JSON else { return total }
    let previous = referenceSize(value)
    if previous == total { return value }
    let share = previous > 0 ? (pair["fixed"] as? Double ?? 0) / previous : 0
    return ["fixed": total * share, "scaled": total * (1 - share)]
}

@MainActor final class ActionButton: NSButton {
    var perform: (() -> Void)?
    var enabledWhen: (() -> Bool)?
    convenience init(_ title: String, _ perform: @escaping () -> Void) {
        self.init(title: localized(title), target: nil, action: nil)
        identifier = NSUserInterfaceItemIdentifier(title)
        bezelStyle = .rounded; self.perform = perform; target = self; action = #selector(invoke)
    }
    @objc func invoke() { perform?() }
}
@MainActor final class ChoiceSegments: NSSegmentedControl {
    var onSelect: ((Int) -> Void)?
    convenience init(_ titles: [String], selected: Int, onSelect: @escaping (Int) -> Void) {
        self.init(labels: titles.map { localized($0) }, trackingMode: .selectOne, target: nil, action: nil)
        self.onSelect = onSelect
        segmentStyle = .rounded
        segmentDistribution = .fillEqually
        selectedSegment = selected
        target = self
        action = #selector(choose)
    }
    @objc private func choose() { if selectedSegment >= 0 { onSelect?(selectedSegment) } }
}

@MainActor func settingsGrid(_ rows: [(String, NSView)]) -> NSGridView {
    let grid = NSGridView(views: rows.map { [label($0.0), $0.1] })
    grid.columnSpacing = 12
    grid.rowSpacing = 10
    grid.column(at: 0).xPlacement = .trailing
    grid.column(at: 1).xPlacement = .fill
    grid.yPlacement = .center
    return grid
}

@MainActor final class ActionMenuItem: NSMenuItem {
    var perform: (() -> Void)?
    convenience init(_ title: String, key: String = "", _ perform: @escaping () -> Void) {
        self.init(title: localized(title), action: nil, keyEquivalent: key)
        self.perform = perform; target = self; action = #selector(invoke)
    }
    @objc func invoke() { perform?() }
}
@MainActor final class FieldControl: NSObject, NSTextFieldDelegate {
    let view: NSView
    var change: (Any) -> Void
    var popup: NSPopUpButton?
    var values: [Any] = []
    var available = true
    var numeric = false
    var structured = false
    var optional = false
    var low: Double?, high: Double?
    var slider: NSSlider?
    var readValue: (() -> Any?)?
    var onTextEdit: (() -> Void)?
    var colorIncludesAlpha = false
    var enabledWhen: (() -> Bool)?
    init(_ title: String, schema: JSON, value: Any, change: @escaping (Any) -> Void) {
        self.change = change
        let kind = textValue(schema["kind"])
        structured = ["numbers", "colors", "strings", "types", "ramp", "array"].contains(kind)
        optional = schema["optional"] as? Bool ?? false
        if kind == "toggle" || kind == "bool" {
            view = NSButton(checkboxWithTitle: localized(title), target: nil, action: nil)
        } else if kind == "choice" || kind == "enum" || kind == "font" {
            view = NSPopUpButton(frame: .zero, pullsDown: false)
        } else if kind == "color" { view = NSColorWell() }
        else { view = NSTextField(string: structured ? ((try? jsonString(value)) ?? "[]") : textValue(value)) }
        super.init()
        if let p = view as? NSPopUpButton {
            popup = p
            for item in array(schema["choices"]) {
                values.append(item["value"] ?? ""); p.addItem(withTitle: kind == "font" ? textValue(item["label"] ?? item["value"]) : NativePresentation.choice(item))
            }
            if let index = values.firstIndex(where: { textValue($0) == textValue(value) }) { p.selectItem(at: index) }
            else if kind == "font", !textValue(value).isEmpty {
                values.append(value); p.addItem(withTitle: textValue(value)); p.selectItem(at: values.count - 1)
            }
            p.target = self; p.action = #selector(changed)
            p.isEnabled = !values.isEmpty
        } else if let b = view as? NSButton { b.state = (value as? Bool == true) ? .on : .off; b.target = self; b.action = #selector(changed) }
        else if let c = view as? NSColorWell {
            colorIncludesAlpha = schema["alpha"] as? Bool == true || textValue(value).count == 9
            c.color = Self.color(textValue(value)); c.target = self; c.action = #selector(changed)
            c.widthAnchor.constraint(equalToConstant: 64).isActive = true
        } else if let f = view as? NSTextField {
            numeric = kind == "number" || kind == "int" || kind == "float"
            low = (schema["low"] as? NSNumber)?.doubleValue; high = (schema["high"] as? NSNumber)?.doubleValue
            f.delegate = self; f.target = self; f.action = #selector(changed)
        }
        available = (schema["enabled"] as? Bool ?? true) && (popup == nil || !values.isEmpty)
        (view as? NSControl)?.isEnabled = available
        view.toolTip = localized(title)
    }
    static func color(_ hex: String) -> NSColor {
        let raw = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        guard let n = UInt64(raw, radix: 16), raw.count == 6 || raw.count == 8 else { return .labelColor }
        let shift = raw.count == 8 ? 8 : 0
        return NSColor(srgbRed: Double((n >> (16 + shift)) & 255)/255, green: Double((n >> (8 + shift)) & 255)/255, blue: Double((n >> shift) & 255)/255, alpha: raw.count == 8 ? Double(n & 255)/255 : 1)
    }
    @objc func changed() {
        guard available, (view as? NSControl)?.isEnabled != false else { return }
        if let p = popup {
            guard p.indexOfSelectedItem >= 0 else { return }
            change(values[p.indexOfSelectedItem])
        } else if let b = view as? NSButton { change(b.state == .on) }
        else if let c = view as? NSColorWell, let rgb = c.color.usingColorSpace(.sRGB) {
            let hex = String(format: "#%02X%02X%02X", Int(rgb.redComponent*255), Int(rgb.greenComponent*255), Int(rgb.blueComponent*255))
            change(hex + (colorIncludesAlpha ? String(format: "%02X", Int(rgb.alphaComponent*255)) : ""))
        }
        else if let f = view as? NSTextField {
            if optional && f.stringValue.trimmingCharacters(in: .whitespaces).isEmpty { change(NSNull()); return }
            if structured {
                guard let data = f.stringValue.data(using: .utf8), let values = try? JSONSerialization.jsonObject(with: data) as? [Any] else { NSSound.beep(); return }
                change(values)
            } else if numeric {
                guard let number = Double(f.stringValue), number.isFinite, low.map({number >= $0}) ?? true, high.map({number <= $0}) ?? true else { NSSound.beep(); return }
                change(number)
            } else { change(f.stringValue) }
        }
    }
    func controlTextDidChange(_ obj: Notification) { onTextEdit?() }
    func controlTextDidEndEditing(_ obj: Notification) { changed() }
    func pendingInputValue() throws -> Any? {
        guard let input = view as? NSTextField else { return nil }
        let text = input.currentEditor()?.string ?? input.stringValue
        // Preserve legacy values until edited.
        if let original = readValue?() {
            let originalText = structured ? try jsonString(original) : textValue(original)
            if text == originalText { return original }
        }
        if optional && text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return NSNull() }
        if structured {
            guard let bytes = text.data(using: .utf8), let value = try? JSONSerialization.jsonObject(with: bytes) as? [Any] else {
                throw AppFailure(message: (view.toolTip ?? "") + "：" + localized("请输入有效的列表。"))
            }
            return value
        }
        if numeric {
            guard let value = Double(text), value.isFinite, low.map({ value >= $0 }) ?? true, high.map({ value <= $0 }) ?? true else {
                throw AppFailure(message: (view.toolTip ?? "") + "：" + localized("请输入有效范围内的数值。"))
            }
            return value
        }
        return text
    }
    func makeSlider() -> NSSlider? {
        guard numeric, let low = low, let high = high else { return nil }
        let slider = NSSlider(value: (view as? NSTextField)?.doubleValue ?? low, minValue: low, maxValue: high, target: self, action: #selector(sliderChanged))
        slider.isContinuous = false; slider.setAccessibilityLabel(view.toolTip ?? "")
        slider.setContentHuggingPriority(.defaultLow, for: .horizontal)
        self.slider = slider; return slider
    }
    @objc private func sliderChanged() {
        guard let slider = slider, slider.isEnabled else { return }
        (view as? NSTextField)?.doubleValue = slider.doubleValue; changed()
    }
    func refresh(enabled: Bool, synchronize: Bool = true) {
        (view as? NSControl)?.isEnabled = enabled && available && (enabledWhen?() ?? true)
        slider?.isEnabled = (view as? NSControl)?.isEnabled == true
        // Preserve active edits during snapshot updates.
        guard synchronize, (view as? NSTextField)?.currentEditor() == nil, let value = readValue?() else { return }
        if let popup = popup {
            if let index = values.firstIndex(where: { textValue($0) == textValue(value) }) { popup.selectItem(at: index) }
        } else if let button = view as? NSButton { button.state = value as? Bool == true ? .on : .off }
        else if let color = view as? NSColorWell { if !color.isActive { color.color = Self.color(textValue(value)) } }
        else if let input = view as? NSTextField { input.stringValue = structured ? ((try? jsonString(value)) ?? "[]") : textValue(value); slider?.doubleValue = input.doubleValue }
    }
}
final class LayerNode: NSObject {
    let id: String
    let title: String
    let icon: String
    let children: [LayerNode]
    init(id: String, title: String, icon: String = "", children: [LayerNode] = []) {
        self.id = id; self.title = title; self.icon = icon; self.children = children
    }
}

@MainActor final class SectionState {
    var collapsed: Set<String> = []
    private var initialized: Set<String> = []
    func initialize(_ key: String, expanded: Bool) {
        if initialized.insert(key).inserted && !expanded { collapsed.insert(key) }
    }
}

@MainActor final class FormSection: NSStackView {
    let body = column([], gap: 10)
    let disclosure = NSButton()
    let state: SectionState
    let key: String
    init(_ title: String, key: String, state: SectionState) {
        self.key = key; self.state = state
        super.init(frame: .zero)
        orientation = .vertical; alignment = .leading; spacing = 10
        translatesAutoresizingMaskIntoConstraints = false
        disclosure.title = ""; disclosure.font = .systemFont(ofSize: 12, weight: .semibold)
        disclosure.bezelStyle = .disclosure; disclosure.setButtonType(.onOff)
        disclosure.target = self; disclosure.action = #selector(toggle)
        disclosure.setAccessibilityLabel(localized(title))
        let titleButton = NSButton(title: localized(title), target: self, action: #selector(toggleTitle))
        titleButton.isBordered = false; titleButton.alignment = .left; titleButton.font = .systemFont(ofSize: 12, weight: .semibold)
        let heading = row([disclosure, titleButton, NSView()], gap: 6)
        addArrangedSubview(heading); addArrangedSubview(body)
        heading.widthAnchor.constraint(equalTo: widthAnchor).isActive = true
        body.widthAnchor.constraint(equalTo: widthAnchor).isActive = true
        applyState()
    }
    required init?(coder: NSCoder) { fatalError() }
    func applyState() {
        let expanded = !state.collapsed.contains(key)
        disclosure.state = expanded ? .on : .off; body.isHidden = !expanded
        disclosure.setAccessibilityValue(expanded ? 1 : 0)
    }
    @objc func toggleTitle() {
        disclosure.state = disclosure.state == .on ? .off : .on
        toggle()
    }
    @objc func toggle() {
        if disclosure.state == .on { state.collapsed.remove(key) } else { state.collapsed.insert(key) }
        applyState()
    }
}

@MainActor final class TopAlignedClipView: NSClipView { override var isFlipped: Bool { true } }
@MainActor final class FormView: NSScrollView {
    let stack = column([], gap: 12)
    var fields: [FieldControl] = []
    var sections: [FormSection] = []
    private var activeSection: FormSection?
    var sectionState: SectionState?
    var onScroll: ((CGFloat) -> Void)?
    private var restoringScroll = false
    override init(frame: NSRect) {
        super.init(frame: frame); drawsBackground = false; hasVerticalScroller = true; autohidesScrollers = true
        contentView = TopAlignedClipView(); contentView.drawsBackground = false
        stack.edgeInsets = NSEdgeInsets(top: 18, left: 18, bottom: 18, right: 18)
        documentView = stack
        stack.widthAnchor.constraint(equalTo: contentView.widthAnchor).isActive = true
        contentView.postsBoundsChangedNotifications = true
        NotificationCenter.default.addObserver(self, selector: #selector(scrolled), name: NSView.boundsDidChangeNotification, object: contentView)
    }
    required init?(coder: NSCoder) { fatalError() }
    func reset() {
        activeSection = nil; sections.removeAll(); fields.removeAll()
        stack.arrangedSubviews.forEach { $0.removeFromSuperview() }
    }
    func add(_ view: NSView) {
        if let section = activeSection {
            section.body.addArrangedSubview(view)
            view.widthAnchor.constraint(equalTo: section.body.widthAnchor).isActive = true
        } else {
            stack.addArrangedSubview(view)
            view.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -36).isActive = true
        }
    }
    func section(_ title: String, key: String, expanded: Bool = true) {
        activeSection = nil
        if !sections.isEmpty { add(rule()) }
        if sectionState == nil { sectionState = SectionState() }
        sectionState!.initialize(key, expanded: expanded)
        let section = FormSection(title, key: key, state: sectionState!)
        add(section); sections.append(section); activeSection = section
    }
    @discardableResult func field(_ title: String, _ schema: JSON, _ value: Any, change: @escaping (Any) -> Void) -> FieldControl {
        let field = FieldControl(title, schema: schema, value: value, change: change); fields.append(field)
        if field.view is NSButton && !(field.view is NSPopUpButton) { add(field.view) }
        else {
            let heading = NSTextField(wrappingLabelWithString: localized(title)); heading.font = .systemFont(ofSize: 12)
            let control: NSView
            if schema["slider"] as? Bool == true, let slider = field.makeSlider() {
                field.view.widthAnchor.constraint(equalToConstant: 62).isActive = true
                control = row([slider, field.view], gap: 8)
            } else { control = field.view }
            let contents = column([heading, control], gap: 5)
            heading.widthAnchor.constraint(equalTo: contents.widthAnchor).isActive = true
            if control is NSStackView || control is NSPopUpButton || control is NSTextField {
                control.widthAnchor.constraint(equalTo: contents.widthAnchor).isActive = true
            }
            add(contents); field.view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        }
        return field
    }
    @objc private func scrolled() {
        if !restoringScroll { onScroll?(contentView.bounds.minY) }
    }
    func restoreScroll(_ position: CGFloat) {
        restoringScroll = true
        defer { restoringScroll = false }
        layoutSubtreeIfNeeded(); stack.layoutSubtreeIfNeeded()
        contentView.scroll(to: NSPoint(x: 0, y: min(max(0, position), max(0, stack.bounds.height - contentView.bounds.height))))
        reflectScrolledClipView(contentView)
    }
}
@MainActor final class EditorWindow: NSWindow, NSWindowDelegate {
    let surface = material(.underWindowBackground, blending: .behindWindow)
    var dirty = false
    var saving = false
    var retained: [Any] = []
    var onClose: (() -> Void)?
    var closeDecision: (() -> Bool)?
    init(_ title: String, width: CGFloat = 760, height: CGFloat = 650) {
        super.init(contentRect: NSRect(x: 0, y: 0, width: width, height: height), styleMask: [.titled, .closable, .resizable, .miniaturizable], backing: .buffered, defer: false)
        isOpaque = false; backgroundColor = .clear; contentView = surface
        self.title = localized(title); isReleasedWhenClosed = false; delegate = self; minSize = NSSize(width: 450, height: 350)
    }
    func permitClose() -> Bool {
        if saving { return false }
        if let closeDecision = closeDecision { return closeDecision() }
        if !dirty { return true }
        let alert = NSAlert(); alert.messageText = localized("放弃未保存的修改？"); alert.addButton(withTitle: localized("继续编辑")); alert.addButton(withTitle: localized("放弃修改"))
        return alert.runModal() == .alertSecondButtonReturn
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool { permitClose() }
    func windowWillClose(_ notification: Notification) { onClose?() }
}
