// 管理交通功能区的控件与命令。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Automation;
using RibbonSpace.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private readonly List<(bool Metro, ContextRibbonToggle Toggle)> _contextToggles = new();
    private readonly List<(bool Metro, CheckBox Toggle, Func<bool> Read)> _contextPanelToggles = new();
    private bool _syncingContextPanel;
    private readonly List<(bool Metro, TextBlock Text)> _lineSummaryControls = new();
    private Task _contextWriteTail = Task.CompletedTask;
    private readonly Dictionary<string, (string Context, int Generation, Func<Task> Write, bool Refresh)> _pendingContextWrites = new();
    private CancellationTokenSource? _contextWriteDelay;
    private int _contextWriteVersion, _contextWritesInFlight;
    private bool ContextWritesPending => _pendingContextWrites.Count > 0 || _contextWritesInFlight > 0;


    private JsonElement ContextOptions(bool metro) => _snapshot.GetProperty(metro ? "metroOptions" : _context == "map" ? "mapTransitOptions" : "transitOptions");
    private bool ReadContextToggle(bool metro, string key, string? child = null) =>
        child is null ? ContextOptions(metro).GetProperty(key).GetBoolean()
            : ContextOptions(metro).GetProperty(key).GetProperty(child).GetBoolean();

    private readonly List<(string Context, ContextRibbonToggle Toggle)> _lineLabelToggles = new();

    private void AddLineLabelToggle(RibbonGroup group, string context)
    {
        var map = context == "map";
        bool Read() => _snapshot.ValueKind == JsonValueKind.Object &&
            _snapshot.GetProperty(map ? "mapTransitOptions" : "transitOptions").GetProperty("line_label_enabled").GetBoolean();
        var label = UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_2035432E8A");
        var toggle = new ContextRibbonToggle(label, RibbonIcons.Create("label.transit_icon"), Read,
            () => QueueContextWriteAsync(false, () => _engine.RequestAsync("setTransitOption",
                new { map, draft = IsTransitDraft, key = "line_label_enabled", value = !Read() })), SetError);
        AutomationProperties.SetAutomationId(toggle.Button, context + ".line_labels");
        group.Items.Add(toggle.Button);
        _lineLabelToggles.Add((context, toggle));
        RegisterRibbonColors(toggle.Button, toggle.Button.Icon, "PRIMARY", selected: Read);
    }

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
                () => QueueContextWriteAsync(false, write), error => SetError(error));
            GetGroup(group).Items.Add(binding.Button);
            _contextToggles.Add((false, binding));
            RegisterRibbonColors(binding.Button, binding.Button.Icon, "PRIMARY", selected: read);
        }
        Add(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_1AB216CC0D"), UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_7EE5D33BF1"), () => ReadContextToggle(false, "stations"),
            () => _engine.RequestAsync("setTransitOption", new { metro = false, draft = IsTransitDraft, key = "stations", value = !ReadContextToggle(false, "stations") }));
        AddButton(GetGroup(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_1AB216CC0D")), UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_CF2382056B"), ShowTransitStationSettingsAsync, "view.transit.station_settings");
        Add(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_1AB216CC0D"), UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_ED178EDA0E"), () => ReadContextToggle(false, "merge_enabled"),
            () => _engine.RequestAsync("setTransitOption", new { metro = false, draft = IsTransitDraft, key = "merge_enabled", value = !ReadContextToggle(false, "merge_enabled") }));
        AddButton(GetGroup(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_1AB216CC0D")), UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_26A0369483"), ShowTransitLinesAsync, "view.transit.lines");
        AddButton(GetGroup(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_C8FFBDF157")), UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_8FB38E6289"), ShowTransitLineSettingsAsync, "view.transit.line_color");
        GetGroup(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_C8FFBDF157")).IsGroupVisible = false;
        AddButton(GetGroup(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_ED31FBB483")), UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_6EDFD8773A"), CompilePreviewAsync, "map.refresh_preview");
        AddLineLabelToggle(GetGroup(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_1AB216CC0D")), "transit");
        AddButton(GetGroup(UiLanguage.Text("MainWindow_TransitRibbon_BuildTransitRibbonAsync_ED31FBB483")),
            UiLanguage.Text("Artwork_Enter"), CreateCityArtworkAsync, "map.edit_city", true);
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
        foreach (var (context, toggle) in _lineLabelToggles)
            toggle.Refresh(!_closing && _documentEnabled && (!_cityArtwork || IsTransitDraft) && context == _context);
        foreach (var (metro, toggle) in _contextToggles)
            toggle.Refresh(!_closing && _documentEnabled && _context == (metro ? "metro" : "transit"));
        _syncingContextPanel = true;
        try
        {
            foreach (var (metro, toggle, read) in _contextPanelToggles)
            {
                toggle.IsEnabled = !_closing && _documentEnabled && (_context == "metro") == metro;
                if (!ContextWritesPending) toggle.IsChecked = read();
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
        var summary = _snapshot.GetProperty("lineSelection").GetProperty(metro ? "metro" : _context == "map" ? "map" : "transit");
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
        return _engine.RequestAsync("setTransitOption", new { metro, map = _context == "map", draft = IsTransitDraft, key, value = values });
    }

    private Task QueueContextWriteAsync(bool metro, Func<Task> write, Func<bool>? stillCurrent = null,
        bool refreshPreview = true, string? debounceKey = null)
    {
        if (_closing || !_documentEnabled || stillCurrent?.Invoke() == false) return Task.CompletedTask;
        var version = ++_contextWriteVersion;
        _contextWriteDelay?.Cancel();
        _contextWriteDelay?.Dispose();
        var delay = _contextWriteDelay = debounceKey is null ? null : new CancellationTokenSource();
        var cancellation = delay?.Token ?? CancellationToken.None;
        var key = $"{_documentGeneration}:{_context}:{debounceKey ?? version.ToString()}";
        _pendingContextWrites[key] = (_context, _documentGeneration, write, refreshPreview);
        var previous = _contextWriteTail;
        async Task Execute()
        {
            if (delay is not null)
            {
                try { await Task.Delay(300, cancellation); }
                catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { return; }
            }
            await previous;
            if (version != _contextWriteVersion) return;
            var batch = _pendingContextWrites.Values.ToArray();
            _pendingContextWrites.Clear();
            var generation = _documentGeneration;
            var context = _context;
            bool Current() => !_closing && generation == _documentGeneration && context == _context;
            _contextWritesInFlight++;
            try
            {
                await RunAsync(async () =>
                {
                    var refresh = false;
                    try
                    {
                        foreach (var item in batch)
                        {
                            if (!Current()) break;
                            if (item.Generation != generation || context != item.Context) continue;
                            await item.Write();
                            // 后续嵌套选项须在最新状态上合并，避免同批修改彼此覆盖。
                            _snapshot = await _engine.RequestAsync("getSnapshot");
                            refresh |= item.Refresh;
                        }
                        if (refresh && Current())
                        {
                            if (metro) MarkMetroGenerationChanged();
                            else { await RefreshLivePreviewAsync(); if (Current()) await ApplyContextAsync(); }
                        }
                    }
                    finally { if (Current()) await RefreshSnapshotAsync(); }
                });
            }
            finally
            {
                _contextWritesInFlight--;
                if (Current()) RefreshContextRibbon();
            }
        }
        return _contextWriteTail = Execute();
    }

    private async Task FlushContextWritesAsync()
    {
        do
        {
            if (_pendingContextWrites.Count > 0 && _documentEnabled)
                await QueueContextWriteAsync(_context == "metro", () => Task.CompletedTask, refreshPreview: false);
            else await _contextWriteTail;
        } while (ContextWritesPending);
    }

    private async Task ShowContextParametersAsync(bool metro)
    {
        await RefreshSnapshotAsync();
        var schema = await _engine.RequestAsync("getTransitSchema", new { metro, map = _context == "map" });
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
        _publicTransitSettingsPage = "lines";
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
                var title = types[i + column].GetProperty("label").GetString()!;
                var content = new Grid { ColumnSpacing = 6 };
                content.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                content.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                var icon = RibbonIcons.Named(key switch
                {
                    "Bus" or "Trolleybus" => "bus",
                    "Metro" or "Train" or "Monorail" or "Tram" => "train",
                    "CableCar" => "aerial-lift", "Ship" or "Ferry" => "ship",
                    "Airplane" => "plane", "Helicopter" => "helicopter", "Blimp" => "zeppelin",
                    _ => throw new InvalidDataException("Unknown transit type: " + key),
                });
                icon.Width = icon.Height = 16;
                if (icon is FontIcon fontIcon) fontIcon.FontSize = 16;
                content.Children.Add(icon);
                var label = new TextBlock { Text = title, TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center };
                Grid.SetColumn(label, 1); content.Children.Add(label);
                var box = new CheckBox { Content = content, IsChecked = Read(), MinWidth = 0,
                    HorizontalContentAlignment = HorizontalAlignment.Stretch, HorizontalAlignment = HorizontalAlignment.Stretch };
                AutomationProperties.SetName(box, title);
                AutomationProperties.SetAutomationId(box, (metro ? "metro" : "transit") + "-type-" + key);
                _contextPanelToggles.Add((metro, box, Read));
                box.Checked += Changed;
                box.Unchecked += Changed;
                async void Changed(object sender, RoutedEventArgs args)
                {
                    if (_syncingContextPanel || !current()) return;
                    var enabled = box.IsChecked == true;
                    await QueueContextWriteAsync(metro,
                        () => _engine.RequestAsync("setTransitType", new { metro, context = _context == "transit", draft = IsTransitDraft, key, enabled }), current, debounceKey: "type:" + key);
                }
                Grid.SetColumn(box, column);
                row.Children.Add(box);
            }
            section.Children.Add(row);
        }
    }
}
