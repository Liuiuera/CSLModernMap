// 管理主窗口的对象检查面板。
using System.Text.Json;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private int _inspectorRequest;
    private JsonElement? _inspectorSelection;
    private ObjectInspectorPanel? _objectInspector;
    private string? _inspectorContext;
    private readonly Dictionary<string, Dictionary<string, bool>> _inspectorExpansion = new();

    private async Task ShowObjectInspectorAsync(JsonElement frame)
    {
        var request = ++_inspectorRequest;
        var generation = _documentGeneration;
        var context = _context;
        _inspectorContext = context;
        var selection = frame.Clone();
        var epoch = _snapshot.GetProperty("documentEpoch").GetInt32();
        _inspectorSelection = selection;
        _objectInspector = null;
        ClearLayerPanel(InspectorPanel);
        InspectorPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("MainWindow_Inspector_ShowObjectInspectorAsync_49DC129433") });
        _inspectorOpen = true; UpdateScaleAndLayout();
        bool Current() => !_closing && request == _inspectorRequest && generation == _documentGeneration && context == _context;
        async Task<JsonElement> Read() => await _engine.RequestAsync("getObjectInspector",
            new { @object = selection, context, documentEpoch = epoch });
        var model = await Read();
        if (!Current()) return;
        var kind = model.GetProperty("objectType").GetString()!;
        if (!_inspectorExpansion.TryGetValue(kind, out var expansion))
            _inspectorExpansion[kind] = expansion = new();
        var panel = new ObjectInspectorPanel(model, async (action, key, value, revision) =>
        {
            if (!Current()) throw new InvalidOperationException(UiLanguage.Text("MainWindow_Inspector_ShowObjectInspectorAsync_2175C1B4DC"));
            if (action == "getObjectInspector") return await Read();
            JsonElement result = default;
            var previous = _layerWriteTail;
            async Task Write()
            {
                await previous;
                if (!Current()) throw new InvalidOperationException(UiLanguage.Text("MainWindow_Inspector_ShowObjectInspectorAsync_2175C1B4DC"));
                result = await _engine.RequestAsync(action, new { @object = selection, context,
                    documentEpoch = epoch, settingsRevision = revision, key, value, group = key });
                if (!Current()) return;
                await RefreshLivePreviewAsync();
                if (!Current()) return;
                await RefreshSnapshotAsync();
                if (!Current()) return;
                await ApplyContextAsync(refresh: false);
                foreach (var item in _snapshot.GetProperty("layers").EnumerateObject())
                    SendViewer(LayerVisibilityCommand.Create(item.Name, item.Value.GetBoolean(), _sceneId, _revision));
            }
            _layerWriteTail = Write();
            // 即使当前请求失败，也保持共享队列可用。
            var operation = _layerWriteTail;
            _layerWriteTail = operation.ContinueWith(_ => { }, TaskScheduler.Default);
            await operation;
            return result;
        }, Current, section => _ = RunAsync(() => EditThemeAsync(section)),
        () => { SendViewer(new { type = "clearObjectSelection" }); ShowInspectorHome(); },
        () => SendViewer(new { type = "locateObject", featureId = selection.TryGetProperty("featureId", out var id) ? id.GetString() : "" }), expansion);
        if (!Current()) return;
        ClearLayerPanel(InspectorPanel);
        _objectInspector = panel;
        InspectorPanel.Children.Add(panel);
        panel.SetAvailable(_documentEnabled && context is "map" or "transit");
    }

    private void RestoreInspectorSelection()
    {
        if (_inspectorSelection is not { } frame) return;
        SendViewer(new { type = "restoreObjectSelection", selection = frame });
    }
}
