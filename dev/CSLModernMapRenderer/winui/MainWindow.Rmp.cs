// 管理主窗口与 RMP 编辑视图的集成。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Automation;
using RibbonSpace;
using RibbonSpace.Controls;
using Windows.System;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private RmpEditorView? _rmpView;
    private bool _generatingRmp, _cancellingRmp, _syncingRmp, _generationSettingsChanged;
    private TaskCompletionSource<JsonElement>? _rmpGeneration;
    private string? _rmpGenerationId;
    private readonly Dictionary<string, ComboBox> _rmpChoices = new();
    private readonly List<(RibbonToggleButton Button, string Key, string? Mode)> _rmpToggles = new();
    private RibbonGroup? _rmpElements;
    private bool RmpBlocked => _generatingRmp || _rmpView?.IsBusy == true ||
        _rmpView?.State is { ValueKind: JsonValueKind.Object } state && state.TryGetProperty("placing", out var placing) && placing.GetBoolean();
    private static string RmpText(string key) => UiLanguage.Text("RmpEditor_" + key);

    private void BuildRmpRibbon()
    {
        var tab = _metroTab!;
        RegisterCommand("file.open", () => _context == "metro" ? RunRmpCommandAsync("open") : OpenCityAsync(), false, new Button(), VirtualKey.O);
        void Button(RibbonGroup group, string key, string command, VirtualKey? shortcut = null) =>
            AddButton(group, RmpText(key), () => RunRmpCommandAsync(command), "rmp." + command, key: shortcut);
        var project = Group(tab, RmpText("Project"));
        Button(project, "Open", "open"); Button(project, "Save", "save"); Button(project, "SaveAs", "saveAs");
        AddButton(project, RmpText("About"), ShowRmpAboutAsync, "view.metro.about", requiresDocument: false);
        var generate = Group(tab, RmpText("Generate"));
        AddButton(generate, RmpText("GenerationSettings"), ShowMetroSettingsAsync, "view.metro.settings");
        AddButton(generate, RmpText("GenerateDraft"), GenerateRmpAsync, "view.metro.refresh");
        var refreshNotice = new Button { Content = RmpText("Apply"), Visibility = Visibility.Collapsed };
        RegisterCommand("view.metro.refresh", GenerateRmpAsync, true, refreshNotice);
        refreshNotice.Click += async (_, _) => await ExecuteCommandAsync("view.metro.refresh");
        OperationError.ActionButton = refreshNotice;
        void UpdateRefreshNotice() => refreshNotice.Visibility =
            OperationError.Message == RmpText("SettingsChanged") && OperationError.Severity == InfoBarSeverity.Warning
                ? Visibility.Visible : Visibility.Collapsed;
        OperationError.RegisterPropertyChangedCallback(InfoBar.MessageProperty, (_, _) => UpdateRefreshNotice());
        OperationError.RegisterPropertyChangedCallback(InfoBar.SeverityProperty, (_, _) => UpdateRefreshNotice());
        AddButton(generate, RmpText("Cancel"), CancelCurrentTaskAsync, "app.cancel", requiresDocument: false);
        var history = Group(tab, RmpText("History"));
        Button(history, "Undo", "undo", VirtualKey.Z); Button(history, "Redo", "redo", VirtualKey.Y);
        var draw = Group(tab, RmpText("Draw"));
        AddRmpToggle(draw, "Select", "mode", "select");
        foreach (var key in new[] { "stations", "paths", "styles" })
        {
            var combo = new ComboBox { PlaceholderText = RmpText(key), MinWidth = 120, MaxWidth = 175 };
            AutomationProperties.SetAutomationId(combo, "rmp-" + key);
            AutomationProperties.SetName(combo, RmpText(key));
            draw.Items.Add(combo); _rmpChoices[key] = combo;
            combo.SelectionChanged += async (_, _) =>
            {
                if (_syncingRmp || combo.SelectedItem is not ComboBoxItem item) return;
                var id = (string)item.Tag;
                var mode = key == "stations" ? id : RmpLineMode(key, id);
                await RunAsync(() => RunRmpCommandAsync("setMode", new { mode }));
            };
        }
        _rmpElements = Group(tab, RmpText("Elements"));
        BuildRmpElements(_rmpElements);
        var view = Group(tab, RmpText("View"));
        Button(view, "Fit", "fit");
        foreach (var key in new[] { "detailsVisible", "gridLines", "snapLines", "showWaterControls" }) AddRmpToggle(view, key, key);
        _metroOutputGroup = Group(tab, RmpText("Export"));
        Button(_metroOutputGroup, "Svg", "svg"); Button(_metroOutputGroup, "Png", "png");
        _metroCloseGroup = Group(tab, UiLanguage.Text("ContextRibbon_Close"));
        AddButton(_metroCloseGroup, UiLanguage.Text("MainWindow_xaml_BuildRibbon_71B67FAAD9"), LeaveContextAsync, "view.metro.close", true);
        RegisterCommand("workspace.saveAs", () => _context == "metro" ? RunRmpCommandAsync("saveAs") : ShowExportAsync(), true,
            new Button(), VirtualKey.S, VirtualKeyModifiers.Control | VirtualKeyModifiers.Shift);
        RegisterCommand("workspace.export", () => _context == "metro" ? RunRmpCommandAsync("export") : ShowExportAsync(), true,
            new Button(), VirtualKey.E, VirtualKeyModifiers.Control | VirtualKeyModifiers.Shift);
    }

    private void AddRmpToggle(RibbonGroup group, string label, string key, string? mode = null)
    {
        var button = new RibbonToggleButton { Label = RmpText(label), Icon = RibbonIcons.Create("rmp." + key),
            Size = RibbonItemSize.Medium };
        AutomationProperties.SetAutomationId(button, "rmp-" + (mode ?? key));
        group.Items.Add(button); _rmpToggles.Add((button, key, mode));
        RegisterCommand("rmp." + (mode ?? key), () => mode is not null
            ? RunRmpCommandAsync("setMode", new { mode })
            : RunRmpCommandAsync("setViewOptions", new { options = new Dictionary<string, bool> { [key] = !RmpState(key) } }), true, button);
        button.Click += async (_, _) => await ExecuteCommandAsync("rmp." + (mode ?? key));
    }

    private bool RmpState(string key) => _rmpView?.State is { ValueKind: JsonValueKind.Object } state && state.GetProperty(key).GetBoolean();

    private string RmpLineMode(string choice, string id)
    {
        var styles = _rmpView!.Tools.GetProperty("styles").EnumerateArray().ToArray();
        var mode = _rmpView.State.GetProperty("mode").GetString()!;
        var current = mode.StartsWith("line-") ? mode[5..].Split('/') : ["", ""];
        bool Supports(JsonElement style, string path) => style.GetProperty("paths").EnumerateArray().Any(item => item.GetString() == path);
        var selected = choice == "styles" ? styles.Single(style => style.GetProperty("id").GetString() == id)
            : styles.FirstOrDefault(style => style.GetProperty("id").GetString() == current[1] && Supports(style, id));
        if (selected.ValueKind == JsonValueKind.Undefined) selected = styles.First(style => Supports(style, id));
        var path = choice == "paths" ? id : Supports(selected, current[0]) ? current[0] : selected.GetProperty("paths")[0].GetString()!;
        return $"line-{path}/{selected.GetProperty("id").GetString()}";
    }

    private void RefreshRmpRibbon()
    {
        _syncingRmp = true;
        try
        {
            if (_rmpView?.Tools is { ValueKind: JsonValueKind.Object } tools)
            {
                foreach (var (key, combo) in _rmpChoices)
                {
                    if (combo.Items.Count != 0) continue;
                    foreach (var item in tools.GetProperty(key).EnumerateArray())
                        combo.Items.Add(new ComboBoxItem { Content = item.GetProperty("label").GetString(), Tag = item.GetProperty("id").GetString()! });
                }
            }
            var mode = _rmpView?.State is { ValueKind: JsonValueKind.Object } state ? state.GetProperty("mode").GetString()! : "";
            foreach (var (key, combo) in _rmpChoices)
            {
                var selected = key == "stations" ? mode : !mode.StartsWith("line-") ? "" : key == "paths" ? mode[5..].Split('/')[0] : mode.Split('/')[1];
                combo.SelectedItem = combo.Items.Cast<ComboBoxItem>().FirstOrDefault(item => (string)item.Tag == selected);
                combo.IsEnabled = _context == "metro" && _rmpView?.IsReady == true && !RmpBlocked;
            }
            foreach (var (button, key, selectedMode) in _rmpToggles)
                button.IsChecked = selectedMode is not null ? selectedMode == mode : RmpState(key);
        }
        finally { _syncingRmp = false; }
        UpdateCommandStates();
    }

    private async Task RunRmpCommandAsync(string command, object? payload = null)
    {
        if (_context != "metro" || RmpBlocked) return;
        if (command != "open" && _rmpView?.IsReady != true) return;
        if (command is "export" or "png" or "svg")
        {
            await ShowExportAsync(command == "export" ? null : command.ToUpperInvariant(), "metro");
            return;
        }
        try { await GetRmpView().ExecuteAsync(command, payload); }
        catch { DiscardEmptyRmpView(); throw; }
        finally
        {
            if (_rmpView?.IsReady == true) _mapTask.Reset(true);
            ApplyMapTaskState(); RefreshRmpRibbon();
        }
    }

    private RmpEditorView GetRmpView()
    {
        if (_rmpView is not null) return _rmpView;
        var view = _rmpView = new RmpEditorView(this, Root);
        RmpWorkspace.Children.Add(view);
        view.StateChanged += RefreshRmpRibbon;
        view.ProjectLoaded += () => ShowNotice(RmpText("LoadedHint"));
        view.CommandRequested = command => RunAsync(() => RunRmpCommandAsync(command));
        return view;
    }

    private void DiscardEmptyRmpView()
    {
        if (_rmpView is not { HasProject: false }) return;
        _rmpView.Dispose(); _rmpView = null; RmpWorkspace.Children.Clear();
    }

    private async Task GenerateRmpAsync()
    {
        if (_context != "metro" || RmpBlocked || _engineBusy) return;
        var generation = _documentGeneration;
        _generatingRmp = true;
        var first = _rmpView?.IsReady != true;
        var requestId = _rmpGenerationId = Guid.NewGuid().ToString("N");
        var completion = _rmpGeneration = new(TaskCreationOptions.RunContinuationsAsynchronously);
        UpdateCommandStates();
        try
        {
            if (_rmpView is { HasProject: true } current && !await current.ConfirmRegenerationAsync()) return;
            await _contextWriteTail;
            if (_context != "metro" || generation != _documentGeneration || _engineBusy) return;
            _rmpView?.Suspend(true);
            BeginMapTask(MapTaskPhase.Generating, RmpText("GenerateDraft"), requestId: requestId, waitForScene: false);
            await _engine.RequestAsync("prepareRmpProject", new { requestId });
            var draft = await completion.Task.WaitAsync(TimeSpan.FromMinutes(5));
            if (_closing || _cancellingRmp || generation != _documentGeneration) return;
            BeginMapTask(MapTaskPhase.Loading, RmpText("Starting"), canCancel: false, waitForScene: false);
            var view = GetRmpView();
            view.Suspend(true);
            if (await view.ApplyDraftAsync(draft, first))
            {
                _generationSettingsChanged = false;
                if (OperationError.Message == RmpText("SettingsChanged")) OperationError.IsOpen = false;
            }
        }
        catch (OperationCanceledException) { }
        catch { DiscardEmptyRmpView(); throw; }
        finally
        {
            _rmpGeneration = null; _rmpGenerationId = null; _generatingRmp = false; _cancellingRmp = false;
            _rmpView?.Suspend(false);
            _mapTask.Reset(_rmpView?.IsReady == true); ApplyMapTaskState();
            await RefreshSnapshotAsync(); RefreshRmpRibbon();
        }
    }

    private void MarkMetroGenerationChanged()
    {
        _generationSettingsChanged = true;
        if (_context == "metro")
        {
            OperationError.Severity = InfoBarSeverity.Warning;
            OperationError.Message = RmpText("SettingsChanged");
            OperationError.IsOpen = true;
        }
    }

    private async Task<bool> ReleaseRmpAsync()
    {
        if (_generatingRmp || _rmpView is { } view && !await view.ConfirmUnsavedAsync()) return false;
        _rmpView?.Dispose(); _rmpView = null; RmpWorkspace.Children.Clear();
        _syncingRmp = true;
        try { foreach (var combo in _rmpChoices.Values) combo.SelectedItem = null; }
        finally { _syncingRmp = false; }
        return true;
    }
}
