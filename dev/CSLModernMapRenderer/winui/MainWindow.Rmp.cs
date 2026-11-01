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
    private bool _generatingRmp, _cancellingRmp, _generationSettingsChanged;
    private TaskCompletionSource<JsonElement>? _rmpGeneration;
    private string? _rmpGenerationId;
    private readonly List<(RibbonToggleButton Button, string Key)> _rmpToggles = new();
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
        _rmpElements = Group(tab, RmpText("Elements"));
        BuildRmpElements(_rmpElements);
        var view = Group(tab, RmpText("View"));
        Button(view, "Fit", "fit");
        AddButton(view, UiLanguage.Text("Map_RotateCounterclockwise"),
            () => RunRmpCommandAsync("rotateMap", new { delta = -90 }), "rmp.rotate_counterclockwise");
        AddButton(view, UiLanguage.Text("Map_RotateClockwise"),
            () => RunRmpCommandAsync("rotateMap", new { delta = 90 }), "rmp.rotate_clockwise");
        foreach (var key in new[] { "detailsVisible", "gridLines", "snapLines", "showWaterControls", "showNames", "showDefaultNames" }) AddRmpToggle(view, key, key);
        _metroOutputGroup = Group(tab, RmpText("Export"));
        Button(_metroOutputGroup, "Svg", "svg"); Button(_metroOutputGroup, "Png", "png");
        _metroCloseGroup = Group(tab, UiLanguage.Text("ContextRibbon_Close"));
        AddButton(_metroCloseGroup, UiLanguage.Text("MainWindow_xaml_BuildRibbon_71B67FAAD9"), LeaveContextAsync, "view.metro.close", true);
        RegisterCommand("workspace.saveAs", () => _context == "metro" ? RunRmpCommandAsync("saveAs") : ShowExportAsync(), true,
            new Button(), VirtualKey.S, VirtualKeyModifiers.Control | VirtualKeyModifiers.Shift);
        RegisterCommand("workspace.export", () => _context == "metro" ? RunRmpCommandAsync("export") : ShowExportAsync(), true,
            new Button(), VirtualKey.E, VirtualKeyModifiers.Control | VirtualKeyModifiers.Shift);
    }

    private void AddRmpToggle(RibbonGroup group, string label, string key)
    {
        var button = new RibbonToggleButton { Label = RmpText(label), Icon = RibbonIcons.Create("rmp." + key),
            Size = RibbonItemSize.Medium };
        AutomationProperties.SetAutomationId(button, "rmp-" + key);
        group.Items.Add(button); _rmpToggles.Add((button, key));
        RegisterCommand("rmp." + key, () => RunRmpCommandAsync("setViewOptions", new { options = new Dictionary<string, bool> { [key] = !RmpState(key) } }), true, button);
        button.Click += async (_, _) => await ExecuteCommandAsync("rmp." + key);
    }

    private bool RmpState(string key) => _rmpView?.State is { ValueKind: JsonValueKind.Object } state && state.GetProperty(key).GetBoolean();

    private void RefreshRmpRibbon()
    {
        foreach (var (button, key) in _rmpToggles)
        {
            button.IsChecked = RmpState(key);
            if (key == "showDefaultNames") button.IsEnabled = _rmpView?.IsReady == true && !RmpBlocked && RmpState("showNames");
        }
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
        view.ProjectLoaded += () => ShowNotice(RmpText(view.NeedsVisualAdjustment ? "VisualAdjustmentHint" : "LoadedHint"));
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
        return true;
    }
}
