import AppKit

enum ExportNaming {
    static func filename(city: String, context: String, format: String, time: Date = Date()) -> String {
        let invalid = CharacterSet(charactersIn: #"<>:"/\|?*"#).union(CharacterSet(charactersIn: UnicodeScalar(0)!...UnicodeScalar(31)!))
        let safeCity = city.components(separatedBy: invalid).joined(separator: "_")
            .replacingOccurrences(of: "[ .]+$", with: "", options: .regularExpression)
        let mode = ["transit", "metro"].contains(context) ? context : "map"
        let key = "export.filename." + mode
        let view = NativeStrings.catalogs[NativeStrings.language]?[key] ?? localized(mode == "transit" ? "公共交通图" : mode == "metro" ? "地铁示意图" : "地图")
        let stamp = DateFormatter()
        stamp.locale = Locale(identifier: "en_US_POSIX")
        stamp.dateFormat = "yyyyMMdd-HHmmss"
        let suffix = format == "RMP" ? "rmp.json" : format.lowercased()
        return "\(safeCity)_\(view)_\(stamp.string(from: time)).\(suffix)"
    }
}

@MainActor final class ExportController:NSObject {
    unowned let owner:InterfaceController
    let window=EditorWindow("导出",width:520,height:580)
    let form=FormView()
    let draft: CityDraft?
    private var draftEdge: FieldControl?
    private let isCityRMG: Bool
    private var rmgCompletion: JSON?
    private let entryRegion: JSON?
    private var customRegion: JSON?
    private var rangeMode = "city"
    private var rangeProfile: MapRangeProfile?
    private var rangeProfileRequest: Task<JSON, Error>?
    private var choosingRange = false
    private var rangeField: FieldControl?
    private let rangeSummary = label("")
    private let dimensionsSummary = label("")
    private let rangePreview = MapRangeCanvas()
    private lazy var rangeButton = ActionButton("选择范围…") { [weak self] in
        guard let self else { return }; self.owner.run { try await self.chooseRange() }
    }
    private var effectiveRegion: JSON? {
        rangeMode == "entry" ? entryRegion : rangeMode == "custom" ? customRegion : nil
    }
    private var effectiveBounds: MapRangeBounds? {
        MapRangeBounds(effectiveRegion?["bounds"] ?? output["bounds"])
    }
    let draftState: JSON
    private lazy var choose = ActionButton("选择保存位置…") { [weak self] in self?.chooseTarget() }
    let progress=NSTextField(labelWithString:"")
    private let destination = label("", 12, .regular, .secondaryLabelColor)
    private lazy var exportButton = ActionButton("导出") { [weak self] in self?.owner.run { try await self?.submit() } }
    private lazy var cancelButton = ActionButton("取消任务") { [weak self] in self?.owner.cancelTask() }
    private var exporting: Bool {
        get { window.saving }
        set {
            window.saving = newValue
            exportButton.isEnabled = !newValue
            cancelButton.isHidden = !newValue || draft != nil || rmpExport
            choose.isEnabled = !newValue
            for field in form.fields { field.refresh(enabled: !newValue) }
            rangeButton.isEnabled = !newValue && !choosingRange
            owner.updateCommands()
        }
    }
    var profile:JSON
    private(set) var format:String
    var requestID=""
    var target:URL?
    var autoOpen=true
    var output:JSON
    var rmpOptions:JSON=["scale":100,"transparent":false,"svgVersion":2]
    var rmpExport:Bool
    init(owner: InterfaceController, profile: JSON, format: String, rmp: Bool,
         draft: CityDraft? = nil, draftState: JSON = [:], entryBounds: [Double]? = nil) {
        self.owner = owner
        self.profile = profile
        self.format = format
        self.rmpExport = rmp
        self.isCityRMG = format == "RMG" && !rmp
        // Freeze the entry selection instead of following the main map.
        self.entryRegion = (entryBounds ?? owner.selectedBounds).map {
            ["bounds": $0, "source": entryBounds != nil ? "random_spot" : "selection"]
        }
        self.draft = draft
        self.draftState = draftState
        output = object(profile["output"])
        super.init()
        rangeMode = entryBounds != nil ? "entry" : "city"
        if let size = object(draftState["config"])["longEdge"] { output["size"] = size }
    }
    func show(){
        let host = window.surface
        window.minSize = NSSize(width: 460, height: 360)
        let body = form
        exportButton.keyEquivalent = "\r"
        destination.lineBreakMode = .byTruncatingMiddle
        destination.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        progress.font = .systemFont(ofSize: 12)
        progress.textColor = .secondaryLabelColor
        progress.lineBreakMode = .byTruncatingMiddle
        progress.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        let footer = row([choose, destination, NSView(), cancelButton, exportButton], gap: 10)
        let divider = rule()
        let layout = column([body, progress, divider, footer], gap: 10)
        for view in [body, progress, divider, footer] { view.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true }
        pin(layout, in: host, inset: 18)
        exporting = false
        window.closeDecision = { [weak self] in self?.choosingRange != true }
        window.onClose = { [weak self] in
            guard let self else { return }
            self.owner.panels.removeAll { $0 === self.window }
            self.owner.activeExport = nil
        }
        owner.panels.append(window); rebuild(); window.center(); window.makeKeyAndOrderFront(nil)
        if draft == nil && !rmpExport && !isCityRMG {
            owner.run { [weak self] in try await self?.loadRangeProfile() }
        }
    }
    func changeSize(_ value: Any) {
        guard let size = (value as? NSNumber)?.intValue, size > 0 else { return }
        let old = (output["size"] as? NSNumber)?.doubleValue ?? 4096
        let scale = (output["elementScale"] as? NSNumber)?.doubleValue ?? old / 4096
        output["size"] = size
        output["elementScale"] = scale * Double(size) / max(1, old)
        if old != Double(size) { rebuild() }
    }
    func rebuild(){
        form.reset()
        rangeField = nil
        if isCityRMG {
            form.add(label(localized("格式") + " · RMG"))
            if let result = rmgCompletion { showRMGResult(result) }
        } else {
            let formats = draft != nil ? ["PNG"] : rmpExport ? ["PNG", "SVG"] : profile["formats"] as? [String] ?? []
            form.field("格式", ["kind": "choice", "choices": formats.map { ["value": $0, "label": $0] }], format) { [weak self] value in
                guard let self else { return }
                self.format = textValue(value)
                self.target = nil
                self.rebuild()
            }
        }
        let caps=object(object(profile["formatCapabilities"])[format])
        if draft != nil {
            let config = object(draftState["config"])
            form.add(label(localized("地图旋转") + " · " + textValue(config["rotation"]) + "°"))
            form.add(label(localized("元素尺寸倍率") + " · " + textValue(config["elementScale"])))
            draftEdge = form.field("自定义边长（px）", ["kind":"number", "low":1, "high":32768], output["size"] ?? 4096) { [weak self] in
                self?.output["size"] = ($0 as? NSNumber)?.intValue
            }
        } else if rmpExport{
            if format=="PNG"{
                form.field("导出比例（%）",["kind":"choice","choices":[25,50,100,150,200,250,300,400,500,750,1000,1500,2000].map{["value":$0,"label":"\($0)%"]}],rmpOptions["scale"] ?? 100){[weak self] value in self?.rmpOptions["scale"]=value}
                form.field("透明背景",["kind":"toggle"],rmpOptions["transparent"] ?? false){[weak self] value in self?.rmpOptions["transparent"]=value}
            }else{form.field("SVG 版本",["kind":"choice","choices":[["value":2.0,"label":"SVG 2.0"],["value":1.1,"label":"SVG 1.1"]]],rmpOptions["svgVersion"] ?? 2){[weak self] value in self?.rmpOptions["svgVersion"]=value}}
        }else{
            if caps["showsRange"] as? Bool==true{
                var choices = [["value":"city", "label":"全城"]]
                if entryRegion != nil { choices.append(["value":"entry", "label":"入口范围"]) }
                choices.append(["value":"custom", "label":"自定义范围"])
                rangeField = form.field("输出范围", ["kind":"choice", "choices": choices], rangeMode) { [weak self] value in
                    guard let self else { return }
                    let mode = textValue(value)
                    if mode == "custom" && self.customRegion == nil {
                        self.refreshRange()
                        self.owner.run { try await self.chooseRange() }
                    } else {
                        self.rangeMode = mode
                        if mode != "custom" { self.customRegion = nil }
                        self.refreshRange()
                    }
                }
                rangeField?.readValue = { [weak self] in self?.rangeMode }
                if rangePreview.constraints.isEmpty { rangePreview.heightAnchor.constraint(equalToConstant: 144).isActive = true }
                form.add(rangePreview)
                form.add(rangeSummary)
                form.add(dimensionsSummary)
                form.add(rangeButton)
            }
            if (caps["outputFields"] as? [String] ?? []).contains("size"){
                let sizes=profile["sizes"] as? [Int] ?? []
                form.field("图片尺寸",["kind":"choice","choices":sizes.map{["value":$0,"label":"\($0) px"]}],output["size"] ?? profile["size"] ?? 4096){[weak self] value in self?.changeSize(value)}
                if profile["customSize"] as? Bool==true{form.field("自定义边长（px）",["kind":"number","low":512,"high":32768],output["size"] ?? 4096){[weak self] value in self?.changeSize(value)}}
            }
            if (caps["outputFields"] as? [String] ?? []).contains("rotation"){form.field("地图旋转",["kind":"choice","choices":[0,90,180,270].map{["value":$0,"label":"\($0)°"]}],output["rotation"] ?? 0){[weak self] value in self?.output["rotation"]=value; self?.refreshRange()}}
            let fields = caps["outputFields"] as? [String] ?? []
            if fields.contains("elementScale") {
                form.field("元素尺寸倍率", ["kind":"number","low":0.000001,"high":1e300], output["elementScale"] ?? 1) { [weak self] in self?.output["elementScale"] = $0 }
            }
            if fields.contains("htmlQuality") {
                let levels = [("smooth","流畅"),("standard","标准"),("fine","精细")]
                form.field("网页地图质量", ["kind":"choice","choices":levels.map { ["value":$0.0,"label":$0.1] }], output["htmlQuality"] ?? "standard") { [weak self] in self?.output["htmlQuality"] = $0 }
            }
            if caps["showsPoiProfile"] as? Bool==true{form.add(owner.textIconButtons())}
        }
        destination.stringValue = target?.path ?? localized("尚未选择保存位置")
        destination.toolTip = target?.path
        form.field("完成后打开输出文件夹",["kind":"toggle"],autoOpen){[weak self] value in self?.autoOpen=value as? Bool ?? false}
        refreshRange()
    }
    private func showRMGResult(_ result: JSON) {
        if let exported = result["exportedLineCount"] as? Int, let skipped = result["skippedLineCount"] as? Int {
            form.add(label(String(format: localized("已生成 %d 条，跳过 %d 条"), exported, skipped)))
        }
        let directory = NSTextField(wrappingLabelWithString: textValue(result["directory"]))
        directory.isSelectable = true
        form.add(directory)
        if let warnings = result["warnings"] as? [String], !warnings.isEmpty {
            form.add(ActionButton("查看 RMG 导出详情") { [weak self] in
                self?.owner.showTextWindow("RMG 导出详情", warnings.joined(separator: "\n"))
            })
        }
    }
    private var rangeIsCurrent: Bool {
        owner.activeExport === self && owner.epoch == profile["documentEpoch"] as? Int
    }

    private func loadRangeProfile() async throws {
        guard rangeProfile == nil, rangeIsCurrent else { return }
        let request = rangeProfileRequest ?? Task { try await owner.engine.request("getRangeProfile") }
        rangeProfileRequest = request
        defer { rangeProfileRequest = nil }
        let reply = try await request.value
        guard rangeIsCurrent else { return }
        rangeProfile = MapRangeProfile(reply)
        refreshRange()
    }

    private func refreshRange() {
        rangeField?.refresh(enabled: !exporting && !choosingRange)
        rangeButton.isEnabled = !exporting && !choosingRange
        rangePreview.isHidden = rangeProfile == nil
        guard let bounds = effectiveBounds else { return }
        rangeSummary.stringValue = bounds.summary
        let rotation = (output["rotation"] as? NSNumber)?.intValue ?? 0
        let edge = (output["size"] as? NSNumber)?.intValue ?? 4096
        let (width, height) = bounds.dimensions(edge: edge, rotation: rotation)
        dimensionsSummary.stringValue = localized("图像尺寸") + ": \(width) × \(height) px"
        if let rangeProfile { rangePreview.show(rangeProfile, area: rangeProfile.world, selection: bounds) }
    }

    private func chooseRange() async throws {
        guard !exporting, !choosingRange, rangeIsCurrent else { return }
        choosingRange = true; refreshRange()
        defer { choosingRange = false; refreshRange() }
        try await loadRangeProfile()
        guard rangeIsCurrent else { return }
        guard let rangeProfile else { throw AppFailure(message: "无法加载导出范围预览：") }
        let chooser = MapRangeChooser(engine: owner.engine, profile: rangeProfile, initial: effectiveBounds)
        guard let bounds = await chooser.choose(parent: window), rangeIsCurrent else { return }
        customRegion = ["bounds": bounds.values, "source": "custom"]
        rangeMode = "custom"
    }
    func chooseTarget(thenExport: Bool = false){
        func accept(_ url: URL?) {
            guard let url = url else { return }
            target = url
            rebuild()
            if thenExport { owner.run { try await self.submit() } }
        }
        if ["RMG","RMP"].contains(format) && !rmpExport{
            let chooser=NSOpenPanel();chooser.canChooseFiles=false;chooser.canChooseDirectories=true;chooser.canCreateDirectories=true
            chooser.beginSheetModal(for:window){ result in if result == .OK { accept(chooser.url) } }
        }else{
            let chooser=NSSavePanel();chooser.allowedFileTypes=[format.lowercased()];chooser.nameFieldStringValue = ExportNaming.filename(city: rmpExport ? owner.rmp.exportCityName : textValue(profile["cityName"]), context: textValue(profile["context"]), format: format)
            chooser.beginSheetModal(for:window){ result in if result == .OK { accept(chooser.url) } }
        }
    }
    func submit() async throws {
        guard !window.saving, !choosingRange else { return }
        if let field = draftEdge {
            guard let number = try field.pendingInputValue() as? Double, let size = Int(exactly: number) else {
                throw AppFailure(message: localized("长边必须是整数像素。"))
            }
            output["size"] = size
        }
        guard let target else { chooseTarget(thenExport: true); return }
        guard owner.epoch==profile["documentEpoch"] as? Int else{throw AppFailure(message:"城市已经改变，请重新打开导出窗口")}
        let region = effectiveRegion
        if rmgCompletion != nil {
            rmgCompletion = nil
            rebuild()
        }
        exporting=true;requestID=UUID().uuidString;progress.stringValue=localized("正在导出…")
        do{
            if let draft {
                guard owner.cityDraft === draft, let revision = draftState["revision"] as? Int else { throw AppFailure(message: localized("请重新打开草稿导出窗口。")) }
                draft.exportID = requestID
                owner.updateCommands()
                let result = try await owner.engine.request("exportCityArtwork", ["documentEpoch": owner.epoch,
                    "requestId": requestID, "expectedRevision": revision, "target": target.path, "longEdge": output["size"] ?? 4096])
                guard result["started"] as? Bool == true else { throw AppFailure(message: localized("未能开始导出")) }
                return
            }
            if rmpExport{try await owner.rmp.export(target,format:format.lowercased(),options:rmpOptions);complete(target.path);return}
            let context=textValue(profile["context"])
            let latest=try await owner.engine.request("getExportProfile",["context":context])
            guard textValue(latest["sharedFingerprint"])==textValue(profile["sharedFingerprint"]) else{throw AppFailure(message:"地图样式已改变，请关闭并重新打开导出窗口")}
            let caps=object(object(profile["formatCapabilities"])[format])
            if let outputTarget=caps["outputProfileTarget"] as? String{
                for key in caps["outputFields"] as? [String] ?? []{if let value=output[key]{_=try await owner.engine.request("setOutputOption",["documentEpoch":owner.epoch,"profileTarget":outputTarget,"key":key,"value":value])}}
            }
            var request:JSON=["format":format,"context":context,"target":target.path,"documentEpoch":owner.epoch,"requestId":requestID,"output":output,"auto_open":false]
            if caps["showsRange"] as? Bool == true, let region { request["region"] = region }
            if (caps["outputFields"] as? [String] ?? []).contains("rotation"), let rotation=output["rotation"] { _=try await owner.engine.request("setOutputOption",["key":"rotation","value":rotation]) }
            let result=try await owner.engine.request("export",request)
            if result["started"] as? Bool != true{throw AppFailure(message:"当前不能开始导出，请等待其他任务完成")}
        }catch{draft?.exportID = ""; exporting=false;progress.stringValue=localized("操作未完成");throw error}
    }
    func event(_ name:String,_ data:JSON){
        guard !requestID.isEmpty,textValue(data["requestId"])==requestID else{return}
        switch name{
        case "progress":progress.stringValue="\(Int((data["percent"] as? Double) ?? 0))%  \(textValue(data["message"]))"
        case "externalExportDone":
            rmgCompletion = data
            complete(textValue(data["directory"]))
            rebuild()
        case "exportDone", "artworkExported":
            draft?.exportID = ""
            complete(textValue(data["path"] ?? target?.path))
        case "exportCancelled":exporting=false;progress.stringValue=localized("导出已取消")
        case "error","artworkExportFailed":draft?.exportID = "";exporting=false;progress.stringValue=localized("操作未完成");owner.showError(AppFailure(payload: data))
        default:break
        }
    }
    func complete(_ path:String){exporting=false;progress.stringValue=String(format: localized("已完成：%@"), path);if autoOpen{NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath:path)])}}
    func cancelled(){guard window.saving, draft == nil, !rmpExport else{return};exporting=false;requestID="";progress.stringValue=localized("导出已取消")}
    func failed(_ error: Error){draft?.exportID = "";exporting=false;requestID="";progress.stringValue=localized("操作未完成")}
}
extension InterfaceController {
    @objc func showExport(){presentExport(format:"PNG",exportContext:context=="map" ? "normal":context)}
    @objc func showWebExport(){presentExport(format:"HTML",exportContext:context=="map" ? "normal":context)}
    func presentExport(format: String, exportContext: String, entryBounds: [Double]? = nil) {
        if let existing=activeExport{existing.window.makeKeyAndOrderFront(nil);return}
        let draft = cityDraft, generation = epoch, mode = context
        run{[self] in
            var state: JSON = [:]
            if let draft {
                guard draft.editor.flushInputs() else { return }
                draft.editor.previewTask?.cancel()
                try await draft.updateTheme()
                state = try await engine.request("getCityArtworkState", ["documentEpoch": generation])
            }
            let profile=try await engine.request("getExportProfile",["context":exportContext])
            guard activeExport == nil, cityDraft === draft, epoch == generation, context == mode else { return }
            let editor = ExportController(owner: self, profile: profile, format: format,
                                          rmp: exportContext == "metro" && format != "RMG",
                                          draft: draft, draftState: state, entryBounds: entryBounds)
            activeExport = editor
            editor.show()
        }
    }
    func showInterchanges(){
        let panel=EditorWindow("立交合集",width:500,height:650);let form=FormView();pin(form, in:panel.surface)
        var request:JSON=["top_count":3,"tile_size":1024,"poi_names":true,"labels":true]
        var layers=Set(["terrain","water","road","building","poi","label"])
        for (key,title,low,high) in [("top_count","立交数量",1,24),("tile_size","单张尺寸（px）",256,4096)]{form.field(title,["kind":"number","low":low,"high":high],request[key]!){value in request[key]=Int(value as? Double ?? 0)}}
        for (key,title) in [("poi_names","地点名称"),("labels","文字标注")]{form.field(title,["kind":"toggle"],true){request[key]=$0}}
        for (key,title) in [("terrain","地形"),("water","水域"),("road","道路"),("building","建筑"),("poi","地点图标"),("label","文字")]{form.field(title,["kind":"toggle"],true){value in if value as? Bool==true{layers.insert(key)}else{layers.remove(key)}}}
        form.add(ActionButton("导出立交合集…"){[weak self] in
            let chooser=NSSavePanel();chooser.allowedFileTypes=["png"];chooser.nameFieldStringValue="interchanges.png"
            chooser.beginSheetModal(for:panel){response in guard response == .OK,let url=chooser.url,let self=self else{return};self.run{
                request["layers"]=Array(layers)
                try await self.renderInterchanges(target:url, request:request)
                panel.close()
            }}
        })
        panels.append(panel);panel.center();panel.makeKeyAndOrderFront(nil)
    }
    func renderInterchanges(target: URL, request: JSON) async throws {
        let id = UUID().uuidString
        toolTasks[id] = (epoch, target.path)
        do {
            let result = try await engine.request("renderInterchanges", ["target":target.path, "requestId":id, "documentEpoch":epoch, "request":request])
            guard result["started"] as? Bool == true else { throw AppFailure(message:"未能开始生成立交合集") }
            showStatus(localized("正在生成立交合集…"))
        } catch { toolTasks.removeValue(forKey:id); throw error }
    }
}
