using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private BuildingListPanel? _buildingList;
    private EditorWindow? _buildingListWindow;
    private int _buildingListRequest, _buildingHighlightRequest;
    private JsonElement? _buildingHighlight;
    private string? _buildingCatalogState;

    private async Task ShowBuildingListAsync()
    {
        if (!_documentEnabled || _context != "map") return;
        if (_buildingListWindow is { } existing) { existing.Activate(); return; }
        var request = ++_buildingListRequest;
        var generation = _documentGeneration;
        var epoch = _snapshot.GetProperty("documentEpoch").GetInt32();
        var data = await _engine.RequestAsync("getBuildingCatalog", new { documentEpoch = epoch });
        if (_closing || request != _buildingListRequest || generation != _documentGeneration || _context != "map") return;
        var window = new EditorWindow(UiLanguage.Text("BuildingList_Open"), 1100, 780, translucent: true, owner: this);
        var panel = new BuildingListPanel(data);
        _buildingListWindow = window;
        _buildingList = panel;
        _buildingCatalogState = BuildingCatalogState();
        panel.Selected = id =>
        {
            if (id is { } value) _ = RunAsync(() => SelectBuildingAsync(value));
            else { SendViewer(new { type = "clearObjectSelection" }); ShowInspectorHome(); }
        };
        panel.CategoryChanged = category => _ = RunAsync(() => HighlightBuildingsAsync(category));
        panel.GameNamesChanged = enabled =>
        {
            var generation = _documentGeneration;
            _ = QueueLayerActionAsync(() => UpdateFieldAsync("poi.game_names", enabled),
                () => !_closing && generation == _documentGeneration && _buildingList == panel);
        };
        window.SetBody(panel);
        window.Closed += (_, _) =>
        {
            if (_buildingListWindow != window) return;
            _buildingListWindow = null;
            CloseBuildingList();
        };
        window.Activate();
        if (_inspectorSelection is { } selection)
        {
            SyncBuildingList(selection);
            if (_objectInspector is { } inspector) panel.ShowDetails(inspector.Model, selection);
        }
    }

    private void CloseBuildingList()
    {
        _buildingListRequest++; _buildingHighlightRequest++;
        var window = _buildingListWindow;
        _buildingListWindow = null;
        _buildingList = null; _buildingHighlight = null; _buildingCatalogState = null;
        window?.ForceClose();
        SendViewer(new { type = "setBuildingHighlight" });
    }

    private string BuildingCatalogState() => _snapshot.GetProperty("fields").GetProperty("poi.game_names").GetRawText()
        + ":" + _snapshot.GetProperty("fields").GetProperty("poi.set").GetRawText()
        + ":" + _snapshot.GetProperty("theme").GetString();

    private async Task RefreshBuildingCatalogAsync()
    {
        var panel = _buildingList;
        if (panel is null || !_documentEnabled) return;
        var state = BuildingCatalogState();
        if (state == _buildingCatalogState) { panel.SynchronizeGameNames(); return; }
        var generation = _documentGeneration;
        var request = ++_buildingListRequest;
        var data = await _engine.RequestAsync("getBuildingCatalog", new {
            documentEpoch = _snapshot.GetProperty("documentEpoch").GetInt32() });
        if (_closing || panel != _buildingList || generation != _documentGeneration || request != _buildingListRequest) return;
        panel.UpdateCatalog(data);
        _buildingCatalogState = state;
        if (_inspectorSelection is { } selection && selection.GetProperty("objectType").GetString() == "building")
            await ShowObjectInspectorAsync(selection);
    }

    private async Task SelectBuildingAsync(int id)
    {
        var panel = _buildingList;
        if (panel is null || !_documentEnabled) return;
        var selection = JsonSerializer.SerializeToElement(new { objectType = "building", properties = new { buildingId = id } });
        await ShowObjectInspectorAsync(selection, locate: true);
    }

    private async Task HighlightBuildingsAsync(string category)
    {
        var request = ++_buildingHighlightRequest;
        var panel = _buildingList;
        _buildingHighlight = null;
        SendViewer(new { type = "setBuildingHighlight" });
        if (category.Length == 0 || panel is null) return;
        var generation = _documentGeneration;
        var data = await _engine.RequestAsync("getBuildingHighlight", new {
            category, documentEpoch = _snapshot.GetProperty("documentEpoch").GetInt32() });
        if (_closing || request != _buildingHighlightRequest || panel != _buildingList || generation != _documentGeneration) return;
        _buildingHighlight = data.Clone();
        RestoreBuildingHighlight();
    }

    private void RestoreBuildingHighlight() => SendViewer(new { type = "setBuildingHighlight",
        data = _buildingHighlight, sceneId = _sceneId, revision = _revision });

    private void SyncBuildingList(JsonElement selection)
    {
        var props = selection.GetProperty("properties");
        int? id = props.TryGetProperty("buildingId", out var building) ? building.GetInt32() : null;
        if (id is null && props.TryGetProperty("sourceKind", out var kind) && kind.GetString() == "building"
            && props.TryGetProperty("sourceId", out var source) && int.TryParse(source.ToString(), out var sourceId)) id = sourceId;
        _buildingList?.SelectBuilding(id);
    }
}
