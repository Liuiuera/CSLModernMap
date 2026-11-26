import AppKit

extension InterfaceController {
    @objc func commandAction(_ sender: Any) {
        let title = (sender as? NSButton)?.identifier?.rawValue ?? (sender as? NSMenuItem)?.identifier?.rawValue ?? (sender as? NSButton)?.title ?? (sender as? NSMenuItem)?.title ?? ""
        execute(title)
    }
    func commandEnabled(_ title: String) -> Bool {
        if ["关于 CSLModernMap","帮助","诊断信息","打开配置文件夹"].contains(title) { return true }
        guard pendingCityEditors == nil, !draftSettingsOpen else { return false }
        if title == "重置主题编辑和导出方式" { return engineReady && !draftWorkflowOpen }
        if let draft = cityDraft {
            guard draft.exportID.isEmpty, !draft.starting, !draft.finishing, !draft.editor.window.saving else { return false }
            switch title {
            case "撤销": return draft.editor.editable && !draft.editor.undoStack.isEmpty
            case "重做": return draft.editor.editable && !draft.editor.redoStack.isEmpty
            case "保存", "保存主题": return draft.hasChanges || fieldWriting || !fieldQueue.isEmpty
            case "刷新草稿": return !draft.working
            case "分类特写", "公交设置", "草稿样式", "选择线路…", "退出草稿", "草稿设置",
                 "导出", "导出…": return true
            default: return false
            }
        }
        if ["预览质量…","重新启动交互预览"].contains(title) { return engineReady && pendingQuality == nil && context != "metro" }
        if ["兼容图像预览","在浏览器预览"].contains(title) { return engineReady && !busy && context != "metro" && snapshot["hasDocument"] as? Bool == true }
        if title == "创建地图草稿" { return engineReady && !staticMode && viewer.ready && !sceneID.isEmpty && !busy && context != "metro" }
        if context == "metro", ["撤销", "重做"].contains(title), let editor = nativeTextEditor {
            return title == "撤销" ? editor.undoManager?.canUndo == true : editor.undoManager?.canRedo == true
        }
        guard engineReady, !busy, !fieldWriting else { return false }
        if title == "地铁示意图" { return context != "metro" && !rmp.opening }
        if context == "metro", let tool = RmpNativeCommand(rawValue: title) { return rmpToolEnabled(tool) }
        if context == "metro", rmp.busy || rmp.placing { return false }
        if title == "打开项目…" { return context == "metro" }
        if title == "导出网页…" && context == "metro" { return false }
        if context == "metro" {
            if ["撤销", "重做"].contains(title) {
                guard NSApp.keyWindow === window, rmp.hasProject else { return false }
                if rmp.webHasFocus, rmp.state["textFocused"] as? Bool == true { return true }
                return rmpToolEnabled(title == "撤销" ? .undo : .redo)
            }
            if ["保存项目", "另存项目…", "保存", "另存为…", "导出…"].contains(title) { return rmp.hasProject && !rmp.busy }
        } else if ["保存项目", "另存项目…", "撤销", "重做"].contains(title) { return false }
        if ["打开城市…","最近打开","清理缓存","界面语言…","返回地图"].contains(title) { return true }
        return snapshot["hasDocument"] as? Bool == true
    }
    func updateCommands() {
        refreshStatusVisibility()
        mapPlaceholder.enableOpen(commandEnabled("打开城市…"))
        mapPlaceholder.enableRetry(commandEnabled("重新启动交互预览"))
        for button in commandButtons { button.isEnabled = commandEnabled(button.identifier?.rawValue ?? button.title) }
        refreshBandActions()
        for panel in [sidebarForm, inspectorForm, transitForm] {
            guard let panel = panel else { continue }
            for field in panel.fields { field.refresh(enabled: engineReady && !busy && cityDraft?.finishing != true && (context != "metro" || !rmp.busy), synchronize: !fieldWriting && fieldQueue.isEmpty) }
            refreshActions(in: panel)
        }
        for window in panels.compactMap({ $0 as? EditorWindow }) {
            (window.retained.first as? RegionSelector)?.refreshAvailability()
            if let selector = window.retained.first as? TransitLineSelector {
                selector.synchronizeEnabledTypes(Set(currentTransitOptions(selector.metro)["types"] as? [String] ?? []))
                selector.refreshAvailability()
            }
        }
        themeEditor?.refreshAvailability()
        cityDraft?.editor.refreshAvailability()
        refreshLineSelectionSummary(); refreshMetroSettingsStatus()
        updateFileOpenMenu()
        NSApp.mainMenu?.update()
        sendPreviewUiState()
    }
    func refreshBandActions() { refreshActions(in: band) }
    private func refreshActions(in view: NSView) {
        if let button = view as? ActionButton {
            button.isEnabled = commandEnabled(button.identifier?.rawValue ?? button.title) && (button.enabledWhen?() ?? true)
        }
        for child in view.subviews { refreshActions(in: child) }
    }
    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        let command = menuItem.identifier?.rawValue ?? menuItem.title
        if let option = RmpNativeCommand(rawValue: command)?.viewOption {
            menuItem.state = context == "metro" && rmp.state[option] as? Bool == true ? .on : .off
        }
        return commandEnabled(command)
    }
    func execute(_ raw: String) {
        let title = raw.trimmingCharacters(in: .whitespaces)
        guard commandEnabled(title) else { return }
        if context == "metro", let tool = RmpNativeCommand(rawValue: title) { runRmpTool(tool); return }
        switch title {
        case "城市概览与建筑列表":
            if let catalog = buildingList { catalog.window.makeKeyAndOrderFront(nil); return }
            let catalog = BuildingList(owner:self)
            buildingList = catalog; panels.append(catalog.window)
            catalog.window.center(); catalog.window.makeKeyAndOrderFront(nil); catalog.reload()
        case "预览质量…": previewSettings()
        case "重新启动交互预览":
            run {
                do { try await self.startInteractivePreview() }
                catch is CancellationError { return }
                catch { self.showPreviewError(error, forceUnavailable: true) }
            }
        case "在浏览器预览": openBrowserPreview()
        case "兼容图像预览": run { try await self.renderStaticPreview() }
        case "创建地图草稿": beginDraft()
        case "打开城市…":
            let chooser = NSOpenPanel(); chooser.allowedFileTypes = ["cmm","gz","mmap","cslmap","xml","json"]; chooser.allowsMultipleSelection = false
            chooser.beginSheetModal(for: window) { [weak self] response in if response == .OK, let url = chooser.url { self?.run { try await self?.openCity(url) } } }
        case "打开项目…": rmp.command("open")
        case "最近打开": showRecent()
        case "保存项目": rmp.command("save")
        case "保存主题": cityDraft?.save()
        case "保存": if let draft = cityDraft { draft.save() } else { rmp.command("save") }
        case "另存项目…": rmp.command("saveAs")
        case "撤销": performWorkspaceHistory(undo: true)
        case "重做": performWorkspaceHistory(undo: false)
        case "导出…", "导出":
            if cityDraft != nil || context == "metro" || activeExport != nil { showExport() }
            else { chooseDraftWorkflow("export") { [weak self] in self?.showExport() } }
        case "导出网页…": showWebExport()
        case "从城市数据生成 RMG…": presentExport(format: "RMG", exportContext: "metro")
        case "编辑主题", "编辑主题…":
            if themeEditor != nil { showThemeEditor() }
            else { chooseDraftWorkflow("theme") { [weak self] in self?.showThemeEditor() } }
        case "重置主题编辑和导出方式":
            run { [self] in
                _ = try await engine.request("resetMapWorkflowChoices")
                showStatus(localized("已重置主题编辑和导出方式"), kind: .ready)
            }
        case "图标映射", "图标映射…": showPoiEditor()
        case "文字", "图标文字": showTextIconEditor()
        case "道路文字": showTextIconEditor(category: .roadText)
        case "区划文字": showTextIconEditor(category: .districtText)
        case "图标": showTextIconEditor(category: .icons)
        case "公共交通图": enterTransit()
        case "地铁示意图": enterMetro()
        case "返回地图": returnToMap()
        case "随机特写": showRandomSpot()
        case "立交合集": showInterchanges()
        case "清理缓存":
            run { [self] in
                let result = try await engine.request("clearCache")
                guard result["cleared"] as? Bool == true else { throw AppFailure(message:"缓存清理未完成") }
                showStatus(localized("缓存已清理"))
                try await refreshSnapshot()
            }
        case "诊断信息": showDiagnostics()
        case "打开配置文件夹": openFolder(engine.dataDirectory, failure: "无法打开配置文件夹")
        case "界面语言…": showLanguage()
        case "关于 CSLModernMap": NSApp.orderFrontStandardAboutPanel(options: [.applicationName:"CSLModernMap", .applicationVersion:Bundle.main.object(forInfoDictionaryKey:"CFBundleShortVersionString") ?? ""])
        case "帮助": showTextWindow("帮助", "打开 CMM 城市文件后，在左侧调整图层；选择地图对象查看右侧属性。\n\n样式页可编辑主题、地点图标及映射。创建与工具提供公共交通、地铁、随机特写和立交合集。\n\n导出窗口按当前地图模式提供支持的格式。地铁编辑器使用独立的 RMP 项目保存。")
        default: showError(AppFailure(message:localized("此操作暂不可用。")))
        }
    }
    func showRecent() {
        let menu = NSMenu()
        for path in snapshot["recentFiles"] as? [String] ?? [] {
            let url = URL(fileURLWithPath: path)
            menu.addItem(ActionMenuItem(url.lastPathComponent) { [weak self] in
                guard let self else { return }
                var isDirectory: ObjCBool = false
                guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory),
                      !isDirectory.boolValue else {
                    let alert = NSAlert()
                    alert.alertStyle = .warning
                    alert.messageText = localized("找不到最近打开的城市文件")
                    alert.informativeText = path
                    self.recordDiagnosticError(alert.messageText + "\n" + path)
                    alert.addButton(withTitle: localized("关闭"))
                    alert.beginSheetModal(for: self.window)
                    return
                }
                self.run { try await self.openCity(url) }
            })
        }
        if menu.items.isEmpty { let item = NSMenuItem(title:localized("暂无最近打开的城市"),action:nil,keyEquivalent:""); item.isEnabled=false; menu.addItem(item) }
        menu.popUp(positioning:nil,at:NSPoint(x:20,y:window.contentView!.bounds.height-100),in:window.contentView)
    }
    func buildMenus() {
        let main = NSMenu()
        @discardableResult
        func add(_ title: String, _ names: [(String,String)]) -> NSMenu {
            let sub=NSMenu(title:localized(title))
            for (name,key) in names {
                if name.isEmpty { sub.addItem(.separator()); continue }
                let item = NSMenuItem(title:localized(name),action:#selector(commandAction(_:)),keyEquivalent:key); item.identifier=NSUserInterfaceItemIdentifier(name); item.target=self; sub.addItem(item)
            }
            let parent=NSMenuItem(); parent.title=localized(title); parent.submenu=sub; main.addItem(parent)
            return sub
        }
        add("CSLModernMap",[("关于 CSLModernMap",""),("界面语言…",""),("","")])
        main.items[0].submenu?.addItem(withTitle:localized("隐藏 CSLModernMap"),action:#selector(NSApplication.hide(_:)),keyEquivalent:"h")
        main.items[0].submenu?.addItem(withTitle:localized("退出 CSLModernMap"),action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q")
        let file = add("文件",[("打开项目…",""),("打开城市…",""),("最近打开",""),("保存项目","s"),("另存项目…","S"),("导出…","e"),("导出网页…",""),("",""),("重置主题编辑和导出方式",""),("打开配置文件夹","")])
        openProjectMenuItem = file.items.first { $0.identifier?.rawValue == "打开项目…" }
        openCityMenuItem = file.items.first { $0.identifier?.rawValue == "打开城市…" }
        updateFileOpenMenu()
        let edit=NSMenu(title:localized("编辑"))
        for title in ["撤销", "重做"] {
            let item=NSMenuItem(title:localized(title),action:#selector(commandAction(_:)),keyEquivalent: title == "撤销" ? "z" : "Z")
            item.identifier=NSUserInterfaceItemIdentifier(title); item.target=self;edit.addItem(item)
        }
        for (title,selector,key) in [("剪切",#selector(NSText.cut(_:)),"x"),("复制",#selector(NSText.copy(_:)),"c"),("粘贴",#selector(NSText.paste(_:)),"v"),("全选",#selector(NSText.selectAll(_:)),"a")] { edit.addItem(withTitle:localized(title),action:selector,keyEquivalent:key) }
        let ep=NSMenuItem();ep.title=edit.title;ep.submenu=edit;main.addItem(ep)
        let view=NSMenu(title:localized("显示"))
        for (title,selector,key) in [("展开或收起功能区",#selector(toggleBand),""),("显示或隐藏对象属性",#selector(toggleInspector),"2")] {
            let item=NSMenuItem(title:localized(title),action:selector,keyEquivalent:key)
            item.identifier=NSUserInterfaceItemIdentifier(title);item.target=self;view.addItem(item)
        }
        for title in ["预览质量…","重新启动交互预览","兼容图像预览","在浏览器预览"] {
            let item = NSMenuItem(title:localized(title), action:#selector(commandAction(_:)), keyEquivalent:"")
            item.identifier = NSUserInterfaceItemIdentifier(title); item.target = self; view.addItem(item)
        }
        view.autoenablesItems=true;let vp=NSMenuItem();vp.submenu=view;main.addItem(vp)
        add("地铁示意图", RmpNativeCommand.elements.map { ($0.rawValue, "") }
            + [("", "")] + RmpNativeCommand.view.map { ($0.rawValue, "") })
        rmpMenuItem = main.items.last
        rmpMenuItem?.isHidden = context != "metro"
        let windows=NSMenu(title:localized("窗口"));windows.addItem(withTitle:localized("最小化"),action:#selector(NSWindow.performMiniaturize(_:)),keyEquivalent:"m")
        let wp=NSMenuItem();wp.submenu=windows;main.addItem(wp);NSApp.windowsMenu=windows
        add("帮助",[("帮助",""),("诊断信息","")]);NSApp.mainMenu=main
    }
    private func updateFileOpenMenu() {
        let metro = context == "metro"
        rmpMenuItem?.isHidden = !metro
        openProjectMenuItem?.isHidden = !metro
        openProjectMenuItem?.keyEquivalent = metro ? "o" : ""
        openCityMenuItem?.keyEquivalent = metro ? "" : "o"
    }
    func openFolder(_ url: URL, failure: String) {
        run {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
            guard NSWorkspace.shared.open(url) else {
                throw AppFailure(message: localized(failure) + "\n" + url.path)
            }
        }
    }
    func showTextWindow(_ title:String,_ text:String) {
        let diagnostic = title == "报错详情"
        let panel = EditorWindow(title, width: diagnostic ? 740 : 540, height: diagnostic ? 500 : 280)
        if !diagnostic { panel.minSize = NSSize(width: 420, height: 220) }
        let scroll = NSTextView.scrollableTextView()
        guard let content = scroll.documentView as? NSTextView else { return }
        content.isEditable = false
        content.isSelectable = true
        content.textContainerInset = NSSize(width: 20, height: 18)
        content.font = diagnostic ? .monospacedSystemFont(ofSize: 12, weight: .regular) : .systemFont(ofSize: 14)
        content.textColor = .textColor
        content.string = text
        content.drawsBackground = false
        scroll.drawsBackground = false
        if title == "报错详情" {
            let copy = ActionButton("复制报错") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
            }
            let layout = column([scroll, copy])
            scroll.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true
            pin(layout, in: panel.surface, inset: 12)
        } else { pin(scroll, in: panel.surface) }
        panel.onClose = { [weak self, weak panel] in self?.panels.removeAll { $0 === panel } }
        panels.append(panel)
        panel.center()
        panel.makeKeyAndOrderFront(nil)
    }
    func showLanguage() {
        let languages = metadata["languages"] as? [String] ?? []
        let menu=NSMenu()
        for code in languages { menu.addItem(ActionMenuItem(NativePresentation.languages[code] ?? code) { [weak self] in self?.run { [self] in
            guard let self=self, self.closeEditors() else{return}
            self.metadata=try await self.engine.request("setLanguage",["language":code]);self.viewer.send(["type":"setLanguage","language":code]);try await self.refreshSnapshot();self.applyLanguage();try await self.rmp.setLanguage();if let layer=self.selectedLayer{self.showLayer(layer)}
        } }) }
        menu.popUp(positioning:nil,at:NSPoint(x:100,y:window.contentView!.bounds.height-40),in:window.contentView)
    }
    @objc func selectTheme(_ sender:NSButton) {
        guard let key = sender.identifier?.rawValue, key.hasPrefix("theme:"),
              let theme = themes.first(where: { $0.id == String(key.dropFirst(6)) }) else { return }
        guard closeEditors() else{return}; gallery?.close()
        update("setTheme",["theme":theme.id])
    }
    @objc func showPoiStyles(_ sender:NSButton) {
        let menu=NSMenu();let schema=object(object(snapshot["fieldSchema"])["poi.set"])
        for option in array(schema["choices"]) { menu.addItem(ActionMenuItem(NativePresentation.choice(option)) { [weak self] in self?.update("setField",["key":"poi.set","value":option["value"] ?? ""]) }) }
        menu.popUp(positioning:nil,at:NSPoint(x:sender.bounds.minX,y:sender.bounds.maxY),in:sender)
    }
}
