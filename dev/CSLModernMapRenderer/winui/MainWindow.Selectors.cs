// 构建和同步主窗口中的筛选选择器。
using System.Globalization;
using System.Text.Json;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private bool _selectorOpen;

    private Dictionary<string, string> TransitTypeLabels() => _snapshot.GetProperty("transitTypes")
        .EnumerateArray().ToDictionary(t => t.GetProperty("key").GetString()!, t => t.GetProperty("label").GetString()!);

    private async Task ShowTransitLinesAsync()
    {
        if (_selectorOpen || _engineBusy) return;
        _selectorOpen = true;
        try
        {
            var generation = _documentGeneration;
            var context = _context;
            var metro = context == "metro";
            var data = await _engine.RequestAsync("getTransitLines", new { metro });
            bool Current() => !_closing && generation == _documentGeneration && context == _context && !_engineBusy;
            if (!Current()) return;
            var labels = TransitTypeLabels();
            var choices = data.GetProperty("lines").EnumerateArray().Select(line =>
            {
                var id = line.GetProperty("id").GetInt64().ToString(CultureInfo.InvariantCulture);
                var type = line.GetProperty("type").GetString()!;
                var group = labels.GetValueOrDefault(type, type);
                var name = line.GetProperty("name").GetString() ?? id;
                var stops = line.TryGetProperty("stops", out var s) ? s.GetInt32() : 0;
                var km = line.TryGetProperty("length_m", out var l) ? l.GetDouble() / 1000 : 0;
                return new SelectorChoice(id, name, group,
                    string.Format(UiLanguage.Text("MainWindow_Selectors_ShowTransitLinesAsync_CCABA1884B"), group, stops, km, id),
                    Color: line.TryGetProperty("color", out var color) ? color.GetString() ?? "#8A8F99" : "#8A8F99");
            }).ToArray();
            var all = choices.Select(c => c.Id).ToArray();
            var selected = data.GetProperty("lineIds").ValueKind == JsonValueKind.Null ? all :
                data.GetProperty("lineIds").EnumerateArray().Select(id => id.GetInt64().ToString(CultureInfo.InvariantCulture)).ToArray();
            var selector = new FilteredMultiSelector(choices, selected);
            var dialog = selector.CreateDialog(Root.XamlRoot,
                metro ? UiLanguage.Text("MainWindow_Selectors_ShowTransitLinesAsync_DD96AB6C0D") : UiLanguage.Text("MainWindow_Selectors_ShowTransitLinesAsync_2418A38642"), metro ? 1 : 0);
            if (await dialog.ShowAsync() != ContentDialogResult.Primary || !Current()) return;
            var ids = selector.SelectedIds.Select(id => long.Parse(id, CultureInfo.InvariantCulture)).ToArray();
            await _engine.RequestAsync("setTransitLines", new { metro, documentEpoch = data.GetProperty("documentEpoch").GetInt32(),
                lineIds = ids.Length == all.Length ? null : ids });
            if (metro)
            {
                await RefreshSnapshotAsync();
                MarkMetroGenerationChanged();
            }
            else
            {
                await RefreshLivePreviewAsync();
                await RefreshSnapshotAsync();
                await ApplyContextAsync();
            }
        }
        finally { _selectorOpen = false; }
    }

    private async Task ShowFacilitiesAsync()
    {
        if (_selectorOpen || _engineBusy || !_documentEnabled || _context != "map" || !HasCs2Document) return;
        _selectorOpen = true;
        try
        {
            var generation = _documentGeneration;
            var data = await _engine.RequestAsync("getFacilityCatalog");
            bool Current() => !_closing && generation == _documentGeneration && _context == "map" && HasCs2Document && !_engineBusy;
            if (!Current()) return;
            var catalog = data.GetProperty("facilities").EnumerateArray().ToArray();
            var choices = catalog.Select(item => new SelectorChoice(item.GetProperty("id").GetString()!,
                item.GetProperty("name").GetString()!, item.GetProperty("kindLabel").GetString()!,
                item.GetProperty("kindLabel").GetString()! + " · " + item.GetProperty("id").GetString())).ToArray();
            var enabled = catalog.Where(item => item.GetProperty("enabled").GetBoolean()).Select(item => item.GetProperty("id").GetString()!);
            var selector = new FilteredMultiSelector(choices, enabled);
            var dialog = selector.CreateDialog(Root.XamlRoot, UiLanguage.Text("MainWindow_Selectors_ShowFacilitiesAsync_803AA8753F"));
            if (await dialog.ShowAsync() != ContentDialogResult.Primary || !Current()) return;
            await _engine.RequestAsync("setFacilitySelection", new { ids = selector.SelectedIds,
                documentEpoch = data.GetProperty("documentEpoch").GetInt32() });
            await RefreshLivePreviewAsync(); await RefreshSnapshotAsync();
        }
        finally { _selectorOpen = false; }
    }

    private async Task SelectRegionsAsync(string group)
    {
        if (_selectorOpen || _engineBusy || _context != "map") return;
        _selectorOpen = true;
        try
        {
            var generation = _documentGeneration;
            var epoch = _snapshot.GetProperty("documentEpoch").GetInt32();
            var data = await _engine.RequestAsync("getRegionCatalog");
            bool Current() => !_closing && generation == _documentGeneration && _context == "map" && !_engineBusy;
            if (!Current()) return;
            var choices = data.GetProperty(group).EnumerateArray().Select(item =>
            {
                var id = item.GetProperty("id").GetInt64().ToString(CultureInfo.InvariantCulture);
                var type = item.GetProperty("type").GetString();
                var available = item.GetProperty("available").GetBoolean();
                var label = string.IsNullOrWhiteSpace(type) ? (group == "districts" ? UiLanguage.Text("MainWindow_Selectors_SelectRegionsAsync_2168C5F581") : UiLanguage.Text("MainWindow_Selectors_SelectRegionsAsync_12EDC1EE80")) : type;
                return new SelectorChoice(id, item.GetProperty("name").GetString() ?? id, label,
                    label + " · " + id + (available ? "" : " · " + UiLanguage.Text("MainWindow_Selectors_SelectRegionsAsync_E3294F8722")), available);
            }).ToArray();
            var selected = data.GetProperty(group == "districts" ? "district_ids" : "park_ids")
                .EnumerateArray().Select(id => id.GetInt64().ToString(CultureInfo.InvariantCulture));
            var selector = new FilteredMultiSelector(choices, selected, []);
            var dialog = selector.CreateDialog(Root.XamlRoot, group == "districts" ? UiLanguage.Text("MainWindow_Selectors_SelectRegionsAsync_D00BB06803") : UiLanguage.Text("MainWindow_Selectors_SelectRegionsAsync_173BE63FFD"));
            if (await dialog.ShowAsync() != ContentDialogResult.Primary || !Current()) return;
            await _engine.RequestAsync("setRegionSelection", new { group, documentEpoch = epoch,
                ids = selector.SelectedIds.Select(id => long.Parse(id, CultureInfo.InvariantCulture)).ToArray() });
            await RefreshLivePreviewAsync(); await RefreshSnapshotAsync();
        }
        finally { _selectorOpen = false; }
    }
}
