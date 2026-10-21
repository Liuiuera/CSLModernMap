// 处理主窗口命令及其调度。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using RibbonSpace;
using RibbonSpace.Controls;
using Windows.System;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private sealed record UiCommand(Func<Task> Run, bool Document, List<Control> Controls);
    private readonly Dictionary<string, UiCommand> _commands = new();
    private readonly HashSet<string> _openingEditors = new();
    private bool _documentEnabled, _inspectorOpen, _allowMainClose, _closingEditors;
    private bool HasCs2Document => _snapshot.ValueKind == JsonValueKind.Object &&
        _snapshot.GetProperty("hasDocument").GetBoolean() && _snapshot.GetProperty("sourceGame").GetString() == "cs2";
    private int _documentGeneration, _pendingFieldWrites;
    private void ApplyPendingSettingsSnapshot() => RefreshLayerBindings();
    private static bool HasFocusedField(DependencyObject root)
    {
        if (root is Control field && (field is TextBox || field is NumberBox || field is ComboBox) && field.FocusState != FocusState.Unfocused) return true;
        for (var i = 0; i < Microsoft.UI.Xaml.Media.VisualTreeHelper.GetChildrenCount(root); i++)
            if (HasFocusedField(Microsoft.UI.Xaml.Media.VisualTreeHelper.GetChild(root, i))) return true;
        return false;
    }
    internal async Task ApplyEditorChangesAsync() { if (_closing || _allowMainClose) return; await RefreshLivePreviewAsync(); await RefreshSnapshotAsync(); }
    private async Task CloseEditorsAndWindowAsync()
    {
        if (_closingEditors) return;
        _closingEditors = true;
        try
        {
            if (!await ReleaseRmpAsync()) return;
            if (!await CloseEditorsAsync()) return;
            _closing = true;
            UpdateCommandStates();
            StatusText.Text = UiLanguage.Text("MainWindow_Commands_CloseEditorsAndWindowAsync_663E1A1ED3");
            await _engine.CloseAsync();
            _allowMainClose = true; Close();
        }
        finally { _closingEditors = false; }
    }
    private async Task<bool> CloseEditorsAsync()
    {
        if (_exportWindow is { } export && !await export.RequestCloseAsync()) return false;
        if (_themeEditor is { } theme && !await theme.RequestCloseAsync()) return false;
        if (_poiEditor is { } poi && !await poi.RequestCloseAsync()) return false;
        if (_mapPoiSettingsWindow is { } settings && !await settings.RequestCloseAsync()) return false;
        return true;
    }
    private ExportWindow? _exportWindow;
    private async Task ShowExportAsync(string? format = null, string? contextOverride = null, object? regionOverride = null)
    {
        var context = contextOverride ?? (_context == "map" ? "normal" : _context);
        if (context == "metro" && format == "RMP")
        {
            if (_context != "metro") await EnterMetroAsync();
            await RunRmpCommandAsync("saveAs");
            return;
        }
        var rmp = context == "metro" && format != "RMG";
        if (rmp && (_rmpView?.IsReady != true || RmpBlocked)) return;
        if (_exportWindow is { } existing)
        {
            existing.Activate();
            return;
        }
        if (!_openingEditors.Add("export")) return;
        try
        {
        var generation = _documentGeneration;
        var expectedContext = _context;
        var profileTarget = context == "transit" ? "transit_png" : context == "metro" ? null : "normal_png";
        var profile = profileTarget is null
            ? await _engine.RequestAsync("getExportProfile", new { context })
            : await _engine.RequestAsync("getExportProfile", new { context, profileTarget });
        if (_closing || _allowMainClose || generation != _documentGeneration || _context != expectedContext) return;
        if (context == "metro" && !rmp)
        {
            var sourceExport = JsonNode.Parse(profile.GetRawText())!.AsObject();
            sourceExport["formats"] = new JsonArray("RMG");
            profile = JsonSerializer.SerializeToElement(sourceExport);
        }
        var window = _exportWindow = new ExportWindow(_engine, profile, regionOverride, format,
            owner: this, editMapSettings: ShowMapPoiSettingsAsync, appearancePalettes: _appearancePalettes,
            settingsChanged: ApplyEditorChangesAsync, rotationChanged: RefreshSnapshotAsync,
            exportRmp: rmp ? _rmpView!.ExportFileAsync : null);
        try { await window.ShowAsync(); }
        finally
        {
            if (_exportWindow == window) _exportWindow = null;
            if (!_closing && !_allowMainClose && _context == "map") RefreshMapOverview();
        }
        }
        finally { _openingEditors.Remove("export"); }
    }

    private void RegisterCommand(string id, Func<Task> run, bool document, Control control, VirtualKey? key = null,
        VirtualKeyModifiers modifiers = VirtualKeyModifiers.Control)
    {
        if (!_commands.TryGetValue(id, out var command))
        {
            command = new UiCommand(run, document, new()); _commands.Add(id, command);
            if (key is { } shortcut)
            {
                var accelerator = new KeyboardAccelerator { Key = shortcut, Modifiers = modifiers };
                accelerator.Invoked += async (_, args) =>
                {
                    if (_context != "metro" && id is "rmp.undo" or "rmp.redo") return;
                    if (!CommandEnabled(id, command)) return;
                    if (_context == "metro" && _rmpView?.HasWebFocus == true &&
                        id is "file.open" or "map.save_png" or "workspace.saveAs" or "workspace.export" or "rmp.undo" or "rmp.redo") return;
                    if (id is "rmp.undo" or "rmp.redo" && HasFocusedField(Root)) return;
                    args.Handled = true; await ExecuteCommandAsync(id);
                };
                Root.KeyboardAccelerators.Add(accelerator);
            }
        }
        command.Controls.Add(control);
    }
    private bool CommandEnabled(string id, UiCommand command)
    {
        var busy = _engineBusy;
        if (_closing) return false;
        if (id == "app.cancel") return _generatingRmp ? _mapTask.CanCancel && !_cancellingRmp : busy;
        if (RmpBlocked) return false;
        if (id == "view.metro.refresh") return _context == "metro" && _documentEnabled && !busy;
        if (id == "rmp.open") return _context == "metro" && !busy;
        if (id.StartsWith("rmp.") || _context == "metro" && id is "map.save_png" or "workspace.saveAs" or "workspace.export")
            return _context == "metro" && _rmpView?.IsReady == true &&
                (id != "rmp.undo" || RmpState("canUndo")) && (id != "rmp.redo" || RmpState("canRedo"));
        if (id == "app.language") return !busy && _pendingFieldWrites == 0 && !_closingEditors && !_changingLanguage;
        if (id is "map.rotate_counterclockwise" or "map.rotate_clockwise")
            return _documentEnabled && _context != "metro" && !busy;
        if (id is "file.open" or "file.open_city" or "map.save_png" or "workspace.saveAs" or "workspace.export" && busy) return false;
        if (command.Document && !_documentEnabled) return false;
        if (id is "view.transit.lines" or "layer.poi.facilities")
            return !busy && (id != "layer.poi.facilities" || _context == "map" && HasCs2Document);
        if (id is "map.select_region" or "create.web_map") return _documentEnabled && _context != "metro" && (id != "create.web_map" || _context == "map");
        if (id is "map.render_selection" or "map.clear_selection") return _documentEnabled && _selectedBounds is not null && _context != "metro";
        return true;
    }
    private async Task ExecuteCommandAsync(string id)
    {
        if (_commands.TryGetValue(id, out var command) && CommandEnabled(id, command)) await RunAsync(command.Run);
        UpdateCommandStates();
    }
    private void UpdateCommandStates()
    {
        foreach (var (id, command) in _commands)
            foreach (var control in command.Controls)
            {
                control.IsEnabled = CommandEnabled(id, command);
                if (id == "map.save_png" && control is RibbonButton saveButton)
                    saveButton.Label = _context == "metro" ? RmpText("Save") : UiLanguage.Text("MainWindow_xaml_BuildRibbon_76420433F2");
                if (id == "layer.poi.facilities")
                    control.Visibility = HasCs2Document ? Visibility.Visible : Visibility.Collapsed;
            }
        if (_facilitySelectionGroup is not null)
            _facilitySelectionGroup.IsGroupVisible = HasCs2Document;
    }
    private void LayoutToolbar()
    {
        var width = ViewerToolbar.ActualWidth;
        var buttonWidth = 0.0;
        foreach (FrameworkElement button in ViewerToolbar.Children)
        {
            button.Measure(new Windows.Foundation.Size(double.PositiveInfinity, double.PositiveInfinity));
            buttonWidth = Math.Max(buttonWidth, button.DesiredSize.Width);
        }
        var columns = Math.Max(1, Math.Min(ViewerToolbar.Children.Count,
            (int)((width + ViewerToolbar.ColumnSpacing) / (buttonWidth + ViewerToolbar.ColumnSpacing))));
        ViewerToolbar.ColumnDefinitions.Clear(); ViewerToolbar.RowDefinitions.Clear();
        for (var i = 0; i < columns; i++) ViewerToolbar.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        for (var i = 0; i < ViewerToolbar.Children.Count; i++)
        {
            if (i % columns == 0) ViewerToolbar.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            Grid.SetRow((FrameworkElement)ViewerToolbar.Children[i], i / columns); Grid.SetColumn((FrameworkElement)ViewerToolbar.Children[i], i % columns);
        }
    }
    private void ApplyCommandMetadata(JsonElement metadata)
    {
        if (!metadata.TryGetProperty("commands", out var commands)) return;
        foreach (var command in commands.EnumerateObject())
            if (_commands.TryGetValue(command.Name, out var binding))
                foreach (var control in binding.Controls)
                    ToolTipService.SetToolTip(control, command.Value.GetProperty("label").GetString() + " " + command.Value.GetProperty("shortcut").GetString());
    }
    private void BuildWorkspaceCommands()
    {
        OverviewButton.Click += (_, _) => ShowMapOverview();
        ViewerToolbar.SizeChanged += (_, _) => LayoutToolbar();
        SettingsPanel.LostFocus += (_, _) => Root.DispatcherQueue.TryEnqueue(ApplyPendingSettingsSnapshot);
        InspectorPanel.LostFocus += (_, _) => Root.DispatcherQueue.TryEnqueue(ApplyPendingSettingsSnapshot);
        InspectorClose.Click += (_, _) => { _inspectorOpen = false; UpdateScaleAndLayout(); };
        InspectorToggle.Click += (_, _) => { _inspectorOpen = !_inspectorOpen; UpdateScaleAndLayout(); };
        var mapActions = new (string Id, string Label, IconElement Icon, Func<Task> Run, VirtualKey? Shortcut)[]
        {
            ("map.rotate_counterclockwise", UiLanguage.Text("Map_RotateCounterclockwise"), RibbonIcons.Named("rotate"), () => RotateMapAsync(-90), null),
            ("map.rotate_clockwise", UiLanguage.Text("Map_RotateClockwise"), RibbonIcons.Named("rotate-clockwise"), () => RotateMapAsync(90), null),
            ("map.select_region", UiLanguage.Text("MainWindow_Commands_BuildWorkspaceCommands_840A04872A"), new SymbolIcon(Symbol.SelectAll), () => { _selectedRegionSource = "selection"; SendViewer(new { type = "beginSelection" }); return Task.CompletedTask; }, null),
            ("map.clear_selection", UiLanguage.Text("MainWindow_Commands_BuildWorkspaceCommands_BCE2377283"), new SymbolIcon(Symbol.Clear), () => { _selectedBounds = null; _selectedRegionSource = null; SendViewer(new { type = "clearSelection" }); return Task.CompletedTask; }, null),
            ("map.render_selection", UiLanguage.Text("MainWindow_Commands_BuildWorkspaceCommands_9B8845539A"), new SymbolIcon(Symbol.Save), ExportSelectionAsync, null),
            ("map.refresh_preview", UiLanguage.Text("MainWindow_Commands_BuildWorkspaceCommands_AEE8874341"), new SymbolIcon(Symbol.Refresh), CompilePreviewAsync, VirtualKey.P),
        };
        foreach (var (id, label, icon, run, shortcut) in mapActions)
        {
            var content = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 4 };
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetAccessibilityView(icon,
                Microsoft.UI.Xaml.Automation.Peers.AccessibilityView.Raw);
            content.Children.Add(new Viewbox { Child = icon, Width = 14, Height = 14 });
            content.Children.Add(new TextBlock { Text = label, FontSize = 12, VerticalAlignment = VerticalAlignment.Center });
            var button = new Button { Content = content, HorizontalAlignment = HorizontalAlignment.Left,
                MinWidth = 0, MinHeight = 28, Padding = new Thickness(8, 3, 8, 3) };
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(button, label);
            RegisterCommand(id, run, true, button, shortcut);
            button.Click += async (_, _) => await ExecuteCommandAsync(id);
            ViewerToolbar.Children.Add(button);
        }
        var export = new Button { Visibility = Visibility.Collapsed };
        RegisterCommand("map.export", () => ShowExportAsync(), true, export);
        UpdateCommandStates();
    }

    private async Task RotateMapAsync(int delta)
    {
        var state = await _engine.RequestAsync("getSnapshot");
        var current = state.GetProperty("output").GetProperty("rotation").GetInt32();
        var rotation = (current + delta + 360) % 360;
        await _engine.RequestAsync("setOutputOption", new { key = "rotation", value = rotation });
        await RefreshSnapshotAsync();
    }

    private void ApplySharedRotation()
    {
        var rotation = _snapshot.GetProperty("output").GetProperty("rotation").GetInt32();
        SendViewer(new { type = "setOutputRotation", rotation });
        _exportWindow?.SetSharedRotation(rotation);
    }
}
