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

    private async Task ShowObjectInspectorAsync(JsonElement frame, bool locate = false)
    {
        var request = ++_inspectorRequest;
        var generation = _documentGeneration;
        var context = _context;
        _inspectorContext = context;
        var epoch = _snapshot.GetProperty("documentEpoch").GetInt32();
        var selection = frame.Clone();
        var props = frame.GetProperty("properties");
        int? buildingId = props.TryGetProperty("buildingId", out var building) ? building.GetInt32() : null;
        if (buildingId is null && props.TryGetProperty("sourceKind", out var sourceKind) && sourceKind.GetString() == "building"
            && props.TryGetProperty("sourceId", out var source) && int.TryParse(source.ToString(), out var sourceId)) buildingId = sourceId;
        if (buildingId is > 0)
        {
            selection = await _engine.RequestAsync("getBuildingSelection", new { buildingId, documentEpoch = epoch });
            if (_closing || request != _inspectorRequest || generation != _documentGeneration || context != _context) return;
            SendViewer(new { type = "selectCatalogBuilding", selection, locate, sceneId = _sceneId, revision = _revision });
        }
        SyncBuildingList(selection);
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
        _buildingList?.ShowDetails(model, selection);
        if (_artworkEditor is { } editor)
        {
            var path = model.TryGetProperty("stylePath", out var stylePath) ? stylePath.GetString()! : model.GetProperty("themeSection").GetString()!;
            editor.FocusStyle(path);
            var facts = new ObjectInspectorPanel(model,
                (_, _, _, _) => throw new InvalidOperationException("Read-only object facts"), Current,
                section => editor.FocusStyle(section),
                () => { SendViewer(new { type = "clearObjectSelection" }); ShowArtworkProperties(); },
                () => SendViewer(new { type = "locateObject", featureId = selection.TryGetProperty("featureId", out var selectedId) ? selectedId.GetString() : "" }),
                factsOnly: true);
            ShowArtworkProperties(facts);
            return;
        }
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
        if (selection.TryGetProperty("members", out var members) && members.GetArrayLength() > 0)
        {
            var children = new StackPanel { Spacing = 6 };
            foreach (var member in members.EnumerateArray())
            {
                var id = member.GetProperty("id").GetInt32();
                children.Children.Add(new TextBlock { Text = $"{BuildingListPanel.DisplayName(member.GetProperty("name").GetString()!, id)} · {id}\n{member.GetProperty("categoryName").GetString()}",
                    TextWrapping = Microsoft.UI.Xaml.TextWrapping.Wrap });
            }
            InspectorPanel.Children.Add(new Expander { Header = UiLanguage.Text("BuildingList_Members"), Content = children,
                HorizontalAlignment = Microsoft.UI.Xaml.HorizontalAlignment.Stretch, HorizontalContentAlignment = Microsoft.UI.Xaml.HorizontalAlignment.Stretch });
        }
        panel.SetAvailable(_documentEnabled && context is "map" or "transit");
    }

    private void RestoreInspectorSelection()
    {
        if (_inspectorSelection is not { } frame) return;
        SendViewer(new { type = "restoreObjectSelection", selection = frame });
    }
}
