import AppKit

@MainActor final class DiagnosticsPanel: NSObject {
    private unowned let owner: InterfaceController
    let window = EditorWindow("诊断信息", width: 720, height: 480)
    private let mapStatus = NSTextField(wrappingLabelWithString: "")
    private let errorScroll = NSTextView.scrollableTextView()
    private let performanceScroll = NSTextView.scrollableTextView()
    private lazy var clearErrors = ActionButton("清空最近错误") { [weak self] in
        self?.owner.recentErrors.removeAll()
        self?.refreshErrors()
    }
    private lazy var toggle = NSButton(checkboxWithTitle: localized("记录性能日志"),
                                       target: self, action: #selector(changePerformanceSetting(_:)))
    private lazy var performanceLogs = ActionButton("打开性能日志目录") { [weak self] in
        guard let self, let url = self.performanceLogDirectory else { return }
        self.owner.openFolder(url, failure: "无法打开日志目录")
    }
    private var performanceLogDirectory: URL?
    private var enabled: Bool?
    private var requesting = false

    init(owner: InterfaceController) {
        self.owner = owner
        super.init()
        mapStatus.isSelectable = true
        mapStatus.maximumNumberOfLines = 0
        let map = NSView()
        map.addSubview(mapStatus)
        mapStatus.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            mapStatus.leadingAnchor.constraint(equalTo: map.leadingAnchor, constant: 16),
            mapStatus.trailingAnchor.constraint(equalTo: map.trailingAnchor, constant: -16),
            mapStatus.topAnchor.constraint(equalTo: map.topAnchor, constant: 16),
        ])

        var logButtons: [NSView] = []
        if let path = ProcessInfo.processInfo.environment["CSLMM_NATIVE_LOG_DIR"], !path.isEmpty {
            let url = URL(fileURLWithPath: path, isDirectory: true)
            logButtons.append(ActionButton("打开日志目录") { [weak self] in
                self?.owner.openFolder(url, failure: "无法打开日志目录")
            })
        }
        logButtons.append(performanceLogs)
        let tabs = NSTabView()
        for (title, page) in [
            ("地图状态", map),
            ("最近错误", textPage(controls: clearErrors, scroll: errorScroll)),
            ("性能诊断", textPage(controls: column([toggle, row(logButtons)]), scroll: performanceScroll)),
        ] {
            let tab = NSTabViewItem(identifier: title)
            tab.label = localized(title)
            tab.view = page
            tabs.addTabViewItem(tab)
        }
        pin(tabs, in: window.surface, inset: 12)
        window.center()
    }

    func show() {
        refreshMapStatus()
        refreshErrors()
        refreshPerformance()
        window.makeKeyAndOrderFront(nil)
    }

    func refreshMapStatus() {
        let city = textValue(object(owner.snapshot["cityInfo"])["name"])
        let source = textValue(owner.snapshot["sourceFile"])
        let mode = owner.context == "metro" ? "地铁示意图" : owner.context == "transit" ? "公共交通图" : "普通地图"
        let preview: String
        if !owner.engineReady {
            preview = localized("引擎未就绪")
        } else if owner.pendingCityEditors != nil {
            preview = localized("正在读取城市…")
        } else if owner.context == "metro" {
            if owner.rmp.busy { preview = localized("处理中…") }
            else { preview = localized(owner.rmp.hasProject ? "就绪" : "尚未打开项目") }
        } else {
            switch owner.mapPlaceholder.state {
            case .empty(let title), .loading(let title), .unavailable(let title), .previewFailed(let title):
                preview = localized(title)
            case .ready:
                preview = localized(owner.staticMode ? "兼容图像预览" : "交互预览") + " · " + localized("就绪")
            }
        }
        let summary = [
            localized("工作区") + "　" + localized(mode),
            localized("城市") + "　" + (city.isEmpty ? localized("尚未打开城市") : city),
            localized("源文件") + "　" + (source.isEmpty ? "—" : source),
            localized("预览状态") + "　" + preview,
        ].joined(separator: "\n\n")
        if mapStatus.stringValue != summary { mapStatus.stringValue = summary }
        toggle.isEnabled = owner.engineReady && enabled != nil && !requesting
    }

    func refreshErrors() {
        clearErrors.isEnabled = !owner.recentErrors.isEmpty
        let text = owner.recentErrors.isEmpty ? localized("本次运行没有出现错误")
            : owner.recentErrors.reversed().joined(separator: "\n\n")
        (errorScroll.documentView as? NSTextView)?.string = text
    }

    func refreshPerformance() {
        guard !requesting else { return }
        renderPerformanceDetails([:])
        updatePerformanceControls()
        guard owner.engineReady else { return }
        requesting = true
        updatePerformanceControls()
        owner.run { [weak self] in
            guard let self else { return }
            defer { self.requesting = false; self.updatePerformanceControls() }
            let data = try await self.owner.engine.request("getDiagnostics")
            self.enabled = data["enabled"] as? Bool
            if let path = data["logDirectory"] as? String, !path.isEmpty {
                self.performanceLogDirectory = URL(fileURLWithPath: path, isDirectory: true)
            }
            self.renderPerformanceDetails(object(data["details"]))
        }
    }

    @objc private func changePerformanceSetting(_ sender: NSButton) {
        let requested = sender.state == .on
        requesting = true
        sender.isEnabled = false
        owner.run { [weak self] in
            guard let self else { return }
            defer { self.requesting = false; self.updatePerformanceControls() }
            let result = try await self.owner.engine.request("setDiagnostics", ["enabled": requested])
            self.enabled = result["enabled"] as? Bool
        }
    }

    private func updatePerformanceControls() {
        toggle.state = enabled == true ? .on : .off
        toggle.isEnabled = owner.engineReady && enabled != nil && !requesting
        performanceLogs.isEnabled = performanceLogDirectory != nil
    }

    private func renderPerformanceDetails(_ details: JSON) {
        let data = try? JSONSerialization.data(withJSONObject: details, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        var text = details.isEmpty ? localized("暂无性能记录") : data.map { String(decoding: $0, as: UTF8.self) } ?? ""
        let output = owner.engine.diagnostics.trimmingCharacters(in: .whitespacesAndNewlines)
        if !output.isEmpty { text += "\n\n" + localized("引擎输出") + "\n" + output }
        (performanceScroll.documentView as? NSTextView)?.string = text
    }

    private func textPage(controls: NSView, scroll: NSScrollView) -> NSView {
        let view = NSView()
        if let text = scroll.documentView as? NSTextView {
            text.isEditable = false
            text.isSelectable = true
            text.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
            text.textContainerInset = NSSize(width: 10, height: 8)
        }
        let layout = column([controls, scroll])
        pin(layout, in: view, inset: 16)
        scroll.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true
        return view
    }
}

extension InterfaceController {
    func showDiagnostics() {
        if diagnosticsPanel == nil {
            let panel = DiagnosticsPanel(owner: self)
            panel.window.onClose = { [weak self] in self?.diagnosticsPanel = nil }
            diagnosticsPanel = panel
        }
        diagnosticsPanel?.show()
    }

    func recordDiagnosticError(_ details: String) {
        recentErrors.append("[\(Date().formatted(date: .omitted, time: .standard))] \(details)")
        if recentErrors.count > 30 { recentErrors.removeFirst() }
        diagnosticsPanel?.refreshErrors()
    }
}
