import AppKit
import WebKit

final class WebHost: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    let view: WKWebView
    let channel: String
    var event: ((JSON) -> Void)?
    var failed: ((Error) -> Void)?
    var ready = false
    private var queued: [JSON] = []
    private var origin: URL?
    private var diagnostics = ""
    init(channel: String) {
        self.channel = channel
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        view = WKWebView(frame: .zero, configuration: config)
        super.init()
        config.userContentController.add(self, name: channel)
        let diagnosticScript = """
        const nativeReport = message => window.webkit.messageHandlers.\(channel).postMessage({type:'nativeScriptError',message});
        window.addEventListener('error', event => { if(event.message) nativeReport(event.message + ' ' + event.filename + ':' + event.lineno); });
        window.addEventListener('unhandledrejection', event => nativeReport(String(event.reason)));
        """
        diagnostics = diagnosticScript
        config.userContentController.addUserScript(WKUserScript(source: diagnosticScript, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        view.navigationDelegate = self
        view.setValue(false, forKey: "drawsBackground")
    }
    func load(_ url: URL) {
        origin = url; ready = false; queued.removeAll()
        if channel == "cslPreview" {
            let settings = (try? jsonString(PreviewQuality.saved.message)) ?? "{}"
            let controller = view.configuration.userContentController
            controller.removeAllUserScripts()
            controller.addUserScript(WKUserScript(source: diagnostics, injectionTime:.atDocumentStart, forMainFrameOnly:true))
            controller.addUserScript(WKUserScript(source:"window.__CSL_PREVIEW_QUALITY__ = " + settings, injectionTime:.atDocumentStart, forMainFrameOnly:true))
        }
        let token = url.pathComponents.dropFirst().first ?? ""
        let prefix = "http://127.0.0.1:\(url.port ?? 80)/\(token)/"
        let rules: [[String: Any]] = [
            ["trigger": ["url-filter": "^https?://"], "action": ["type": "block"]],
            ["trigger": ["url-filter": "^" + NSRegularExpression.escapedPattern(for: prefix)], "action": ["type": "ignore-previous-rules"]]
        ]
        do {
            WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "local-" + channel, encodedContentRuleList: try jsonString(rules)) { [weak self] rules, error in
                guard let self = self, self.origin == url else { return }
                if let error = error { self.failed?(error); return }
                self.view.configuration.userContentController.removeAllContentRuleLists()
                if let rules = rules { self.view.configuration.userContentController.add(rules) }
                self.view.load(URLRequest(url: url))
            }
        } catch { failed?(error) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 45) { [weak self] in
            guard let self = self, self.origin == url, !self.ready else { return }
            self.failed?(AppFailure(message: "本地网页未能完成初始化"))
        }
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, trusted(message.frameInfo.request.url) else { return }
        let frame: JSON
        if let string = message.body as? String, let data = string.data(using: .utf8), let parsed = try? JSONSerialization.jsonObject(with: data) { frame = object(parsed) }
        else { frame = object(message.body) }
        if frame["type"] as? String == "nativeScriptError" {
            failed?(AppFailure(message: textValue(frame["message"]))); return
        }
        if frame["type"] as? String == "ready" || (channel == "cslQuality" && frame["type"] as? String == "qualityDemoReady") {
            ready = true; let commands = queued; queued.removeAll()
            for command in commands { send(command) }
        }
        event?(frame)
    }
    func trusted(_ url: URL?) -> Bool {
        guard let url = url, let origin = origin else { return false }
        return url.scheme == "http" && url.host == "127.0.0.1" && url.port == origin.port && url.pathComponents.dropFirst().first == origin.pathComponents.dropFirst().first
    }
    func send(_ command: JSON) {
        if !ready { queued.append(command); return }
        do {
            let script = "window.\(channel).dispatch(\(try jsonString(command))); null"
            view.evaluateJavaScript(script) { [weak self] _, error in if let error = error { self?.failed?(error) } }
        } catch { failed?(error) }
    }
    func applyAndPresent(_ commands: [JSON]) async throws {
        guard !commands.isEmpty else { return }
        guard ready else {
            for command in commands { send(command) }
            return
        }
        _ = try await view.callAsyncJavaScript("""
            for (const command of commands) await window[channel].dispatch(command);
            await new Promise(resolve => {
                const timer = setTimeout(resolve, 250);
                requestAnimationFrame(() => requestAnimationFrame(() => {
                    clearTimeout(timer);
                    resolve();
                }));
            });
            """, arguments: ["commands": commands, "channel": channel], in: nil, contentWorld: .page)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(trusted(navigationAction.request.url) ? .allow : .cancel)
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { failed?(error) }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { ready = false; failed?(AppFailure(message: "地图网页进程已退出，请重新打开应用。")) }
    func dispose() { view.stopLoading(); view.configuration.userContentController.removeScriptMessageHandler(forName: channel); queued.removeAll() }
}
