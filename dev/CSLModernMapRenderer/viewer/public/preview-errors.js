// 在地图模块执行前接收启动和未处理异常；同一份脚本也内联到离线 HTML。
(() => {
  const report = (message) => {
    const status = document.getElementById('status');
    if (status) { status.textContent = message; status.hidden = false; }
    const event = {type: 'renderError', stage: 'viewerScript', fatal: true, message};
    if (window.chrome?.webview) window.chrome.webview.postMessage(JSON.stringify(event));
    else window.webkit?.messageHandlers?.cslPreview?.postMessage(event);
  };
  window.addEventListener('error', event => {
    if (event instanceof ErrorEvent) report(event.error?.stack || event.message);
    else if (event.target instanceof HTMLScriptElement)
      report(`Viewer script could not load: ${event.target.src}`);
  }, true);
  window.addEventListener('unhandledrejection', event => {
    report(event.reason instanceof Error ? event.reason.stack || event.reason.message : String(event.reason));
  });
})();
