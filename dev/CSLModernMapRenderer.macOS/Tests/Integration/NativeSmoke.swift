import AppKit

@MainActor final class NativeSmoke {
    unowned let owner:InterfaceController
    let source:String
    let output:URL
    var finished=false
    var testing=false
    var checks:[String]=[] { didSet { FileHandle.standardError.write(Data(("check: " + (checks.last ?? "") + "\n").utf8)) } }
    var exportContinuation:CheckedContinuation<JSON,Error>?
    var exportID=""
    init?(owner: InterfaceController, arguments: [String]) {
        guard let i = arguments.firstIndex(of: "--smoke"), arguments.count > i + 2 else { return nil }
        self.owner = owner
        source = arguments[i + 1]
        output = URL(fileURLWithPath: arguments[i + 2])
    }
    func openThemeGallery() throws {
        guard let button = owner.commandButtons.first(where: { $0.action == #selector(InterfaceController.showThemes(_:)) })
        else { throw AppFailure(message: "Theme button missing") }
        button.performClick(nil)
    }
    func start() {
        owner.run { [self] in
            try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
            if CommandLine.arguments.contains("--rmp-commands-probe") {
                try await inspectRmpNativeCommands()
                finish(true, nil)
            } else if CommandLine.arguments.contains("--rmp-probe") {
                try await owner.rmp.initialize()
                checks.append("rmp-webkit-tools")
                finish(true, nil)
            } else { try await owner.openCity(URL(fileURLWithPath: source)) }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 600) { [weak self] in
            guard let self, !self.finished else { return }
            self.fail(AppFailure(message: "Native smoke timed out"))
        }
    }
    func sceneLoaded(_ frame:JSON){guard !testing,frame["completion"] as? String=="complete" else{return};testing=true;owner.run{[self] in
        checks.append("engine-open-and-webkit-scene")
        if CommandLine.arguments.contains("--draft-probe") {
            try await inspectCityDrafts()
            finish(true, nil)
            return
        }
        if CommandLine.arguments.contains("--preview-integration-probe") { try await inspectPreviewIntegration() }
        if CommandLine.arguments.contains("--themes-probe") {
            try await inspectThemes(); finish(true, nil); return
        }
        if CommandLine.arguments.contains("--navigation-probe") {
            try await inspectNavigation(); finish(true, nil); return
        }
        if CommandLine.arguments.contains("--text-icons-probe") {
            try await inspectTextIconSettings()
            try await inspectTextIconExport()
            finish(true, nil)
            return
        }
        if CommandLine.arguments.contains("--terrain-probe") {
            try await inspectTerrainSettings()
            try await inspectTerrainExports()
            finish(true, nil)
            return
        }
        if CommandLine.arguments.contains("--settings-probe") {
            try await inspectSettings()
            finish(true,nil)
            return
        }
        if CommandLine.arguments.contains("--layout-probe") {
            try await inspectLayout(mapOnly: CommandLine.arguments.contains("--map-layers-only"))
            finish(true,nil)
            return
        }
        if CommandLine.arguments.contains("--real-map-probe") {
            try await inspectRealMap()
            finish(true,nil)
            return
        }
        owner.showLayer("道路");checks.append("road-schema-form")
        _=try await owner.engine.request("setField",["key":"road.level.highway.footprint_scale","value":1.10]);let state=try await owner.engine.request("getSnapshot");guard abs((object(state["fields"])["road.level.highway.footprint_scale"] as? Double ?? 0) - 1.10) < 0.000001 else{throw AppFailure(message:"Field did not roundtrip: \(textValue(object(state["fields"])["road.level.highway.footprint_scale"]))")};checks.append("field-roundtrip")
        _=try await owner.engine.request("setField",["key":"road.level.highway.footprint_scale","value":object(owner.snapshot["fields"])["road.level.highway.footprint_scale"] ?? 1])
        let themes=try await owner.engine.request("getThemeDraft");_=try await owner.engine.request("describeThemeDraft",["data":themes["data"] ?? [:]]);checks.append("theme-contract")
        let poi=try await owner.engine.request("getPoiEditor");_=try await owner.engine.request("validatePoiMapping",["token":poi["token"] ?? "","changes":[:]]);checks.append("poi-contract")
        let lines=try await owner.engine.request("getTransitLines");_=try await owner.engine.request("setTransitLines",["metro":false,"lineIds":[],"documentEpoch":owner.epoch]);_=try await owner.engine.request("setTransitLines",["metro":false,"lineIds":lines["lineIds"] ?? NSNull(),"documentEpoch":owner.epoch]);checks.append("transit-empty-and-all")
        for format in ["PNG","HTML"]{
            exportID=UUID().uuidString
            let result:JSON=try await withCheckedThrowingContinuation{continuation in exportContinuation=continuation;owner.run{[self] in do{
                let started=try await owner.engine.request("export",["format":format,"context":"normal","documentEpoch":owner.epoch,"target":output.appendingPathComponent("地图."+format.lowercased()).path,"requestId":exportID,"output":["size":1024],"auto_open":false]);if started["started"] as? Bool != true{throw AppFailure(message:"Export did not start")}
            }catch{exportContinuation?.resume(throwing:error);exportContinuation=nil}}}
            checks.append(format+"-export:"+textValue(result["path"]))
        }
        try await capture("map-light")
        owner.window.appearance = NSAppearance(named: .darkAqua)
        try await capture("map-dark")
        owner.window.appearance = NSAppearance(named: .aqua)
        owner.metadata = try await owner.engine.request("setLanguage", ["language":"en"])
        owner.applyLanguage()
        guard owner.tabs.label(forSegment:0) == "Home" else { throw AppFailure(message:"Native language did not change") }
        checks.append("native-language")
        owner.metadata = try await owner.engine.request("setLanguage", ["language":"zh-CN"])
        owner.applyLanguage()
        try await owner.openMetro();checks.append("rmp-generated-webkit")
        let project=try await owner.rmp.request("saveProject");guard object(project["project"])["graph"] != nil else{throw AppFailure(message:"RMP project missing graph")}
        let originalGraph = try jsonString(object(project["project"])["graph"] ?? [:])
        _ = try await owner.rmp.request("rotateMap", ["delta":90])
        guard owner.rmp.state["canUndo"] as? Bool == true else { throw AppFailure(message:"RMP edit did not enter history") }
        _ = try await owner.rmp.request("undo")
        let undone = try await owner.rmp.request("saveProject")
        guard try jsonString(object(undone["project"])["graph"] ?? [:]) == originalGraph else { throw AppFailure(message:"RMP undo did not restore project") }
        _ = try await owner.rmp.request("redo"); checks.append("rmp-undo-redo")
        guard try await owner.rmp.save(to: output.appendingPathComponent("smoke.rmp.json")) else { throw AppFailure(message:"RMP project was not saved") }
        let saved = try Data(contentsOf: output.appendingPathComponent("smoke.rmp.json"))
        try await owner.rmp.load(object(try JSONSerialization.jsonObject(with:saved)), path:output.appendingPathComponent("smoke.rmp.json"))
        checks.append("rmp-save-reopen")
        let beforeLanguage = try await owner.rmp.request("saveProject")
        owner.metadata = try await owner.engine.request("setLanguage", ["language":"en"])
        try await owner.rmp.setLanguage()
        let afterLanguage = try await owner.rmp.request("saveProject")
        guard try jsonString(beforeLanguage["project"] ?? [:]) == jsonString(afterLanguage["project"] ?? [:]) else { throw AppFailure(message:"RMP language switch changed project") }
        owner.metadata = try await owner.engine.request("setLanguage", ["language":"zh-CN"])
        try await owner.rmp.setLanguage()
        checks.append("rmp-language-preserves-project")
        _ = try await owner.rmp.request("beginInsert", ["insertion":["kind":"title"]])
        _ = try await owner.rmp.request("cancelInsert"); checks.append("rmp-insertion-cancel")
        let beforeInsert = try await owner.rmp.request("saveProject")
        let nodeCount = array(object(object(beforeInsert["project"])["graph"])["nodes"]).count
        _ = try await owner.rmp.request("beginInsert", ["insertion":["kind":"title"]])
        _ = try await owner.rmp.host.view.evaluateJavaScript("const c=document.querySelector('#canvas'); const r=c.getBoundingClientRect(); c.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,clientX:r.x+r.width/2,clientY:r.y+r.height/2})); null")
        let inserted = try await owner.rmp.request("saveProject")
        guard array(object(object(inserted["project"])["graph"])["nodes"]).count > nodeCount else { throw AppFailure(message:"RMP insertion did not create nodes") }
        _ = try await owner.rmp.request("undo")
        let undoInsert = try await owner.rmp.request("saveProject")
        guard array(object(object(undoInsert["project"])["graph"])["nodes"]).count == nodeCount else { throw AppFailure(message:"RMP insertion undo failed") }
        _ = try await owner.rmp.request("redo")
        checks.append("rmp-insert-place-undo-redo")
        try await owner.rmp.export(output.appendingPathComponent("地铁.svg"),format:"svg",options:["svgVersion":2]);checks.append("rmp-svg-export")
        try await owner.rmp.export(output.appendingPathComponent("地铁.png"),format:"png",options:["scale":100,"transparent":false]);checks.append("rmp-png-export")
        try await capture("rmp-editor")
        finish(true,nil)
    }}
    func inspectRealMap() async throws {
        guard (object(owner.snapshot["stats"])["buildings"] as? Int ?? 0) > 0 else { throw AppFailure(message:"Real map probe requires buildings") }
        for _ in 0..<4 {
            owner.viewer.send(["type":"zoomIn"])
            try await Task.sleep(nanoseconds:350_000_000)
        }
        try await settleLayout(); try checkGeometry()
        try await capture("real-map-light")
        owner.window.appearance = NSAppearance(named:.darkAqua)
        try await capture("real-map-dark")
        owner.window.appearance = NSAppearance(named:.aqua)
        owner.window.setContentSize(NSSize(width:1100,height:700))
        try await settleLayout(); try checkGeometry()
        try await capture("real-map-narrow")
        checks.append("real-map-light-dark-narrow")
        let visibility = object(owner.snapshot["layers"])
        for kind in ["building", "zone"] {
            for key in visibility.keys {
                _ = try await owner.engine.request("setLayerVisibility", ["key":key,"enabled":key == kind])
            }
            try await owner.refreshSnapshot();try await owner.applySceneState()
            try await Task.sleep(nanoseconds:500_000_000)
            owner.selectedObject=nil
            // Hidden GeoJSON layers prepare visible tiles asynchronously after being enabled.
            // Retry the viewport until those tiles can be picked, with a bounded deadline.
            let selectionDeadline = Date().addingTimeInterval(30)
            var selectionPasses = 0
            search: while Date() < selectionDeadline {
                selectionPasses += 1
                for y in 1..<16 {
                    for x in 1..<16 {
                        let script = """
                        (()=>{const c=document.querySelector('.maplibregl-canvas');const r=c.getBoundingClientRect();
                        for(const type of ['mousedown','mouseup','click']) c.dispatchEvent(new MouseEvent(type,{bubbles:true,button:0,clientX:r.x+r.width*\(x)/16,clientY:r.y+r.height*\(y)/16}));return null;})()
                        """
                        _ = try await owner.viewer.view.evaluateJavaScript(script)
                        try await Task.sleep(nanoseconds:30_000_000)
                        if textValue(owner.selectedObject?["objectType"]) == kind { break search }
                        if Date() >= selectionDeadline { break search }
                    }
                }
            }
            FileHandle.standardError.write(Data(("selection passes: " + kind + " " + String(selectionPasses) + "\n").utf8))
            guard let selection=owner.selectedObject, textValue(selection["objectType"])==kind else { throw AppFailure(message:"Could not select rendered " + kind) }
            try await owner.showObject(selection)
            let model=try await owner.engine.request("getObjectInspector",["object":selection,"context":"map","documentEpoch":owner.epoch])
            guard !array(model["sections"]).isEmpty else { throw AppFailure(message:"Selected object has no inspector") }
            if let section = owner.inspectorForm?.sections.first {
                section.toggleTitle()
                try await owner.showObject(selection)
                try requireLayout(owner.inspectorForm!.sections.first!.body.isHidden, "inspector refresh preserves section state")
                owner.inspectorForm!.sections.first!.toggleTitle()
            }
            try await capture("selected-"+kind)
            checks.append("rendered-"+kind+"-selection-inspector")
        }
        for (key,value) in visibility { _=try await owner.engine.request("setLayerVisibility",["key":key,"enabled":value]) }
        try await owner.refreshSnapshot();try await owner.applySceneState()
    }
    func capture(_ name: String) async throws {
        owner.root.layoutSubtreeIfNeeded()
        try await Task.sleep(nanoseconds:150_000_000)
        owner.window.displayIfNeeded()
        if CommandLine.arguments.contains("--settings-probe") && !CommandLine.arguments.contains("--fgh-probe") {
            guard let image = owner.left.bitmapImageRepForCachingDisplay(in: owner.left.bounds) else { throw AppFailure(message: "Settings panel capture failed") }
            owner.left.cacheDisplay(in: owner.left.bounds, to: image)
            try image.representation(using: .png, properties: [:])?.write(to: output.appendingPathComponent(name + ".png"))
            return
        }
        // Capture the actual composed window, including the native titlebar and WebKit.
        if let image = CGWindowListCreateImage(.null, .optionIncludingWindow, CGWindowID(owner.window.windowNumber), [.boundsIgnoreFraming, .bestResolution]) {
            let bitmap = NSBitmapImageRep(cgImage:image)
            if let data = bitmap.representation(using:.png, properties:[:]) {
                try data.write(to:output.appendingPathComponent(name+".png")); return
            }
        }
        let webView = owner.context == "metro" ? owner.rmp.host.view : owner.viewer.view
        let webImage: NSImage? = try await withCheckedThrowingContinuation { continuation in
            webView.takeSnapshot(with: nil) { image, error in
                if let error = error { continuation.resume(throwing: error) }
                else { continuation.resume(returning: image) }
            }
        }
        guard let content = owner.window.contentView, let bitmap = content.bitmapImageRepForCachingDisplay(in: content.bounds) else { throw AppFailure(message:"Window capture failed") }
        content.cacheDisplay(in: content.bounds, to: bitmap)
        let composed = NSImage(size: content.bounds.size)
        composed.lockFocus()
        bitmap.draw(in: content.bounds)
        webImage?.draw(in: content.convert(webView.bounds, from: webView))
        composed.unlockFocus()
        if let data = composed.tiffRepresentation, let outputImage = NSBitmapImageRep(data:data) {
            try outputImage.representation(using:.png, properties:[:])?.write(to:output.appendingPathComponent(name+".png"))
        }
    }
    func engineEvent(_ name:String,_ payload:JSON){if name == "exportDone" { FileHandle.standardError.write(Data(("export matched " + textValue(payload["requestId"]) + " / " + exportID + "\n").utf8)) };guard textValue(payload["requestId"])==exportID else{return};if name=="exportDone"{exportContinuation?.resume(returning:payload);exportContinuation=nil}else if name=="error"{exportContinuation?.resume(throwing:AppFailure(message:textValue(payload["message"])));exportContinuation=nil}}
    func fail(_ error:Error){finish(false,error.localizedDescription)}
    func finish(_ success:Bool,_ error:String?){guard !finished else{return};finished=true
        try? FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        if let content = owner.window.contentView, let bitmap = content.bitmapImageRepForCachingDisplay(in: content.bounds) {
            content.cacheDisplay(in: content.bounds, to: bitmap)
            try? bitmap.representation(using: .png, properties: [:])?.write(to: output.appendingPathComponent("native-window.png"))
        }
        let result:JSON=["success":success,"checks":checks,"error":error ?? "","diagnostics":owner.engine.diagnostics,"backingScale":owner.window.backingScaleFactor,"operatingSystem":ProcessInfo.processInfo.operatingSystemVersionString]
        try? JSONSerialization.data(withJSONObject:result,options:[.prettyPrinted,.sortedKeys]).write(to:output.appendingPathComponent("native-smoke.json"))
        owner.viewer.view.takeSnapshot(with:nil){[weak self] image,_ in guard let self=self else{return};if let data=image?.tiffRepresentation,let bitmap=NSBitmapImageRep(data:data){try? bitmap.representation(using:.png,properties:[:])?.write(to:self.output.appendingPathComponent("native-map.png"))};self.owner.terminating=true;self.owner.engine.close();DispatchQueue.main.asyncAfter(deadline:.now()+1){NSApp.terminate(nil)}}
    }
}
