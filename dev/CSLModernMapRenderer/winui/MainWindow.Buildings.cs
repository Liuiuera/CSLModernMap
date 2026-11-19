using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private readonly Button BuildingListButton = new() { HorizontalAlignment = HorizontalAlignment.Stretch, IsEnabled = false };
    private BuildingListPanel? _buildingList;
    private EditorWindow? _buildingListWindow;
    private int _buildingListRequest, _buildingHighlightRequest;
    private JsonElement? _buildingHighlight;
    private string? _buildingCatalogState;
    private bool _buildingUseGameNames, _buildingListLoading;
    private DispatcherTimer? _buildingNamesDelay;

    private async Task ShowBuildingListAsync()
    {
        if (!_documentEnabled || _context != "map") return;
        if (_buildingListWindow is { } existing) { existing.Activate(); return; }
        var request = ++_buildingListRequest;
        var generation = _documentGeneration;
        var epoch = _snapshot.GetProperty("documentEpoch").GetInt32();
        _buildingUseGameNames = false;
        var window = new EditorWindow(UiLanguage.Text("BuildingList_Open"), "building", 1100, 780, translucent: true, owner: this);
        var loading = BuildingListPanel.CreateLoadingIndicator();
        var body = new Grid();
        body.Children.Add(loading);
        window.SetBody(body);
        _buildingListWindow = window;
        window.Closed += (_, _) =>
        {
            if (_buildingListWindow != window) return;
            _buildingListWindow = null;
            CloseBuildingList();
        };
        _buildingListLoading = true;
        ApplyMapTaskState();
        window.Activate();
        try
        {
            var data = await _engine.RequestAsync("getBuildingCatalog", new { documentEpoch = epoch, useGameNames = _buildingUseGameNames });
            if (_closing || request != _buildingListRequest || generation != _documentGeneration || _context != "map") return;
            var panel = new BuildingListPanel(data);
            _buildingList = panel;
            _buildingCatalogState = BuildingCatalogState();
            panel.Selected = id =>
            {
                if (id is { } value) _ = RunAsync(() => SelectBuildingAsync(value));
                else { SendViewer(new { type = "clearObjectSelection" }); ShowInspectorHome(); }
            };
            panel.CategoryChanged = category => _ = RunAsync(() => HighlightBuildingsAsync(category));
            _buildingNamesDelay = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(250) };
            _buildingNamesDelay.Tick += (_, _) =>
            {
                _buildingNamesDelay.Stop();
                _ = RunAsync(RefreshBuildingCatalogAsync);
            };
            panel.GameNamesChanged = enabled =>
            {
                _buildingUseGameNames = enabled;
                _buildingListRequest++;
                SetBuildingListLoading(true);
                _buildingNamesDelay.Stop();
                _buildingNamesDelay.Start();
            };
            body.Children.Clear();
            body.Children.Add(panel);
            if (_inspectorSelection is { } selection)
            {
                SyncBuildingList(selection);
                if (_objectInspector is { } inspector) panel.ShowDetails(inspector.Model, selection);
            }
        }
        finally
        {
            ((ProgressRing)loading.Children[0]).IsActive = false;
            if (_buildingListWindow == window)
            {
                SetBuildingListLoading(false);
            }
        }
    }

    private void CloseBuildingList()
    {
        _buildingListRequest++; _buildingHighlightRequest++;
        _buildingNamesDelay?.Stop();
        _buildingNamesDelay = null;
        var window = _buildingListWindow;
        _buildingListWindow = null;
        _buildingList = null; _buildingHighlight = null; _buildingCatalogState = null;
        _buildingListLoading = false;
        ApplyMapTaskState();
        window?.ForceClose();
        SendViewer(new { type = "setBuildingHighlight" });
    }

    private string BuildingCatalogState() => _buildingUseGameNames.ToString()
        + ":" + _snapshot.GetProperty("fields").GetProperty("poi.set").GetRawText()
        + ":" + _snapshot.GetProperty("theme").GetString();

    private void SetBuildingListLoading(bool loading)
    {
        _buildingListLoading = loading;
        _buildingList?.SetLoading(loading);
        ApplyMapTaskState();
    }

    private async Task RefreshBuildingCatalogAsync()
    {
        var panel = _buildingList;
        if (panel is null || !_documentEnabled || _buildingNamesDelay?.IsEnabled == true) return;
        var state = BuildingCatalogState();
        var request = ++_buildingListRequest;
        try
        {
            if (state == _buildingCatalogState) { panel.SynchronizeGameNames(); return; }
            var generation = _documentGeneration;
            var data = await _engine.RequestAsync("getBuildingCatalog", new {
                documentEpoch = _snapshot.GetProperty("documentEpoch").GetInt32(), useGameNames = _buildingUseGameNames });
            if (_closing || panel != _buildingList || generation != _documentGeneration || request != _buildingListRequest) return;
            panel.UpdateCatalog(data);
            _buildingCatalogState = state;
        }
        finally
        {
            if (panel == _buildingList && request == _buildingListRequest) SetBuildingListLoading(false);
        }
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
