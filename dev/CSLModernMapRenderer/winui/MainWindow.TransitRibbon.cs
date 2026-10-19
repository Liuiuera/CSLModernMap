// 管理交通功能区的控件与命令。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using RibbonSpace.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private readonly List<(bool Metro, ContextRibbonToggle Toggle)> _contextToggles = new();
    private readonly List<(bool Metro, CheckBox Toggle, Func<bool> Read)> _contextPanelToggles = new();
    private bool _syncingContextPanel;
    private readonly List<(bool Metro, TextBlock Text)> _lineSummaryControls = new();
    private Task _contextWriteTail = Task.CompletedTask;

    private JsonElement ContextOptions(bool metro) => _snapshot.GetProperty(metro ? "metroOptions" : "transitOptions");
    private bool ReadContextToggle(bool metro, string key, string? child = null) =>
        child is null ? ContextOptions(metro).GetProperty(key).GetBoolean()
            : ContextOptions(metro).GetProperty(key).GetProperty(child).GetBoolean();

    private Task BuildTransitRibbonAsync()
    {
        var tab = _transitTab!;
        var groups = new Dictionary<string, RibbonGroup>();
        RibbonGroup GetGroup(string title)
        {
            if (!groups.TryGetValue(title, out var group)) groups[title] = group = Group(tab, title);
            return group;
        }
        void Add(string group, string title, Func<bool> read, Func<Task> write)
        {
            var binding = new ContextRibbonToggle(title, RibbonIcons.Context(title), read,
                () => QueueContextWriteAsync(false, write), error => SetError(error.Message));
            GetGroup(group).Items.Add(binding.Button);
            _contextToggles.Add((false, binding));
            RegisterRibbonColors(binding.Button, binding.Button.Icon, "PRIMARY", selected: read);
        }
        Add(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_1AB216CC0D"), UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_7EE5D33BF1"), () => ReadContextToggle(false, "stations"),
            () => _engine.RequestAsync("setTransitOption", new { metro = false, key = "stations", value = !ReadContextToggle(false, "stations") }));
        AddButton(GetGroup(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_1AB216CC0D")), UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_CF2382056B"), ShowTransitStationSettingsAsync, "view.transit.station_settings");
        Add(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_1AB216CC0D"), UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_ED178EDA0E"), () => ReadContextToggle(false, "merge_enabled"),
            () => _engine.RequestAsync("setTransitOption", new { metro = false, key = "merge_enabled", value = !ReadContextToggle(false, "merge_enabled") }));
        Add(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_C8FFBDF157"), UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_661499ED97"), () => ReadContextToggle(false, "auto_color"),
            () => _engine.RequestAsync("setTransitOption", new { metro = false, key = "auto_color", value = !ReadContextToggle(false, "auto_color") }));
        AddButton(GetGroup(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_1AB216CC0D")), UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_26A0369483"), ShowTransitLinesAsync, "view.transit.lines");
        AddButton(GetGroup(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_C8FFBDF157")), UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_8FB38E6289"), ShowTransitLineSettingsAsync, "view.transit.line_color");
        GetGroup(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_C8FFBDF157")).IsGroupVisible = false;
        AddButton(GetGroup(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_ED31FBB483")), UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_6EDFD8773A"), CompilePreviewAsync, "map.refresh_preview");
        MoveContextRibbonActionsLast(false);
        RefreshContextRibbon();
        return Task.CompletedTask;
    }

    private void MoveContextRibbonActionsLast(bool metro)
    {
        var tab = metro ? _metroTab! : _transitTab!;
        var output = metro ? _metroOutputGroup! : _transitOutputGroup!;
        var close = metro ? _metroCloseGroup! : _transitCloseGroup!;
        tab.Groups.Remove(output); tab.Groups.Remove(close);
        tab.Groups.Add(output); tab.Groups.Add(close);
    }

    private void RefreshContextRibbon()
    {
        if (_snapshot.ValueKind != JsonValueKind.Object) return;
        foreach (var (metro, toggle) in _contextToggles)
            toggle.Refresh(!_closing && _documentEnabled && _context == (metro ? "metro" : "transit"));
        _syncingContextPanel = true;
        try
        {
            foreach (var (metro, toggle, read) in _contextPanelToggles)
            {
                toggle.IsEnabled = !_closing && _documentEnabled && _context == (metro ? "metro" : "transit");
                toggle.IsChecked = read();
            }
        }
        finally { _syncingContextPanel = false; }
        ApplyRibbonColors();
        foreach (var (metro, label) in _lineSummaryControls) label.Text = LineSelectionText(metro);
        _refreshPublicTransitSettings?.Invoke();
        _refreshMetroSettings?.Invoke();
    }

    private string LineSelectionText(bool metro)
    {
        var summary = _snapshot.GetProperty("lineSelection").GetProperty(metro ? "metro" : "transit");
        var labels = TransitTypeLabels();
        var types = string.Join("、", summary.GetProperty("types").EnumerateArray().Select(item => labels.GetValueOrDefault(item.GetString()!, item.GetString()!)));
        return string.Format(UiLanguage.Text("MainWindow_TransitRibbon_LineSelectionText_07BC73C987"), summary.GetProperty("selected").GetInt32(),
            summary.GetProperty("total").GetInt32(), types.Length > 0 ? types : UiLanguage.Text("MainWindow_TransitRibbon_LineSelectionText_8609728302"));
    }

    private void AddLineSelectionSummary(StackPanel panel, bool metro)
    {
        var label = new TextBlock { Text = LineSelectionText(metro), TextWrapping = TextWrapping.Wrap };
        panel.Children.Add(label); _lineSummaryControls.Add((metro, label));
    }

    private Task WriteContextChildAsync(bool metro, string key, string child, object value)
    {
        // 与最新快照合并，避免参数编辑撤销功能区切换。
        var values = JsonNode.Parse(ContextOptions(metro).GetProperty(key).GetRawText())!.AsObject();
        values[child] = JsonSerializer.SerializeToNode(value);
        return _engine.RequestAsync("setTransitOption", new { metro, key, value = values });
    }

    private Task QueueContextWriteAsync(bool metro, Func<Task> write, Func<bool>? stillCurrent = null, bool refreshPreview = true)
    {
        var generation = _documentGeneration;
        var previous = _contextWriteTail;
        async Task Execute()
        {
            await previous;
            if (_closing || generation != _documentGeneration || !_documentEnabled ||
                _context != (metro ? "metro" : "transit") || stillCurrent?.Invoke() == false) return;
            await RunAsync(async () =>
            {
                try
                {
                    await write();
                    if (refreshPreview)
                    {
                        if (metro) MarkMetroGenerationChanged();
                        else { await RefreshLivePreviewAsync(); await ApplyContextAsync(); }
                    }
                }
                finally { if (!_closing && generation == _documentGeneration) await RefreshSnapshotAsync(); }
            });
        }
        return _contextWriteTail = Execute();
    }

    private async Task ShowContextParametersAsync(bool metro)
    {
        await RefreshSnapshotAsync();
        var schema = await _engine.RequestAsync("getTransitSchema", new { metro });
        ClearLayerPanel(SettingsPanel);
        _contextPanelToggles.Clear();
        _lineSummaryControls.Clear();
        if (metro)
        {
            ShowMetroSettings(schema);
            return;
        }
        ShowPublicTransitSettings(schema);
    }

    private async Task ShowTransitLineSettingsAsync()
    {
        await ShowTransitSettingsAsync();
        _selectPublicTransitPage?.Invoke("lines");
    }

    private void AddTransitTypes(StackPanel section, bool metro, Func<bool> current)
    {
        var types = _snapshot.GetProperty("transitTypes").EnumerateArray().ToArray();
        for (var i = 0; i < types.Length; i += 2)
        {
            var row = new Grid();
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            for (var column = 0; column < 2 && i + column < types.Length; column++)
            {
                var key = types[i + column].GetProperty("key").GetString()!;
                bool Read() => ContextOptions(metro).GetProperty("types").EnumerateArray().Any(type => type.GetString() == key);
                var box = new CheckBox { Content = new TextBlock { Text = types[i + column].GetProperty("label").GetString(),
                    TextWrapping = TextWrapping.Wrap }, IsChecked = Read(), MinWidth = 0, HorizontalAlignment = HorizontalAlignment.Stretch };
                _contextPanelToggles.Add((metro, box, Read));
                box.Checked += Changed;
                box.Unchecked += Changed;
                async void Changed(object sender, RoutedEventArgs args)
                {
                    if (_syncingContextPanel || !current()) return;
                    var enabled = box.IsChecked == true;
                    await QueueContextWriteAsync(metro,
                        () => _engine.RequestAsync("setTransitType", new { metro, context = !metro, key, enabled }), current);
                }
                Grid.SetColumn(box, column);
                row.Children.Add(box);
            }
            section.Children.Add(row);
        }
    }
}
