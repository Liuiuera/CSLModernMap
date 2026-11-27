import AppKit

@MainActor extension NativeSmoke {
    func waitForNavigation(_ predicate: String, _ message: String) async throws {
        for _ in 0..<500 {
            if try await owner.viewer.view.evaluateJavaScript("Boolean(" + predicate + ")") as? Bool == true { return }
            try await Task.sleep(nanoseconds:50_000_000)
        }
        let status = try await owner.viewer.view.evaluateJavaScript("document.querySelector('.navigation-status')?.textContent || ''")
        throw AppFailure(message: message + ": " + textValue(status))
    }

    func chooseNavigationPoint(_ index: Int, _ coordinate: String) async throws {
        _ = try await owner.viewer.view.evaluateJavaScript("""
        (()=>{const input=document.querySelectorAll('.navigation-endpoint input')[\(index)];
        input.focus();input.click();document.querySelector('.navigation-panel > details').open=true;
        document.querySelector('.navigation-actions input').value=\(try jsonString(coordinate));
        document.querySelector('[data-navigation-key="useCoordinate"]').click();return null;})()
        """)
        try await waitForNavigation("document.querySelectorAll('.navigation-endpoint')[\(index)].getAttribute('aria-pressed') === 'false'", "Navigation endpoint did not resolve")
    }

    func inspectNavigation() async throws {
        owner.window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps:true)
        try await waitForNavigation("document.querySelector('.navigation-control')?.hidden === false", "Navigation entry missing")
        _ = try await owner.viewer.view.evaluateJavaScript("document.querySelector('.navigation-control > button').click();null")
        try await chooseNavigationPoint(0, "20, 0")
        try await chooseNavigationPoint(1, "100, 80")
        try await waitForNavigation("document.querySelectorAll('.navigation-route-option:enabled').length === 2 && document.querySelectorAll('.navigation-map-card').length === 1", "Navigation Worker did not return two routes")
        _ = try await owner.viewer.view.evaluateJavaScript("document.querySelector('[data-navigation-key=fit]').click();null")
        try await Task.sleep(nanoseconds:1_500_000_000)
        checks.append("native-webkit-worker-driving-routes")
        try await capture("navigation-light")
        owner.window.appearance = NSAppearance(named:.darkAqua)
        try await capture("navigation-dark")
        owner.window.appearance = NSAppearance(named:.aqua)
        owner.window.setContentSize(NSSize(width:1100,height:700));try await settleLayout()
        try await capture("navigation-narrow")
        checks.append("native-navigation-light-dark-narrow")
        _ = try await owner.viewer.view.evaluateJavaScript("document.querySelector('.navigation-simulate').click();null")
        try await waitForNavigation("document.querySelector('.navigation-control').classList.contains('navigation-simulation-active')", "Navigation simulation did not start")
        _ = try await owner.viewer.view.evaluateJavaScript("document.querySelector('[data-simulation-key=exit]').click();null")
        try await waitForNavigation("!document.querySelector('.navigation-control').classList.contains('navigation-simulation-active')", "Navigation simulation did not exit")
        _ = try await owner.viewer.view.evaluateJavaScript("document.querySelector('[data-navigation-key=clearRoute]').click();null")
        try await waitForNavigation("document.querySelectorAll('.navigation-route-card').length === 0", "Navigation route did not clear")
        checks.append("native-navigation-simulation-exit-and-clear")
    }
}
