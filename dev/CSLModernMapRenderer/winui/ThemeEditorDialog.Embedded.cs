// 将同一主题工作副本和字段控件提供给主窗口。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

internal sealed partial class ThemeEditorDialog
{
    private sealed record StyleFocus(string Section, string? RoadLevel = null, string? LabelType = null);
    private readonly SemaphoreSlim _embeddedPreviewGate = new(1, 1);
    private readonly ContentControl _embeddedProperties = new() { HorizontalContentAlignment = HorizontalAlignment.Stretch };
    private string? _roadLevel, _lastPublishedTheme;
    private bool _pendingInput, _flushingEmbeddedPreview;
    private XamlRoot DialogRoot => _window?.Root.XamlRoot ?? _hostRoot;
    private Window PickerOwner => _window is not null ? _window : _owner;

    public static ThemeEditorDialog CreateEmbedded(MainWindow owner, EngineClient engine, XamlRoot root,
        JsonElement draft, Func<JsonElement, Task> workingThemeChanged)
    {
        ArgumentNullException.ThrowIfNull(workingThemeChanged);
        return new ThemeEditorDialog(owner, engine, root, draft, workingThemeChanged);
    }

    public FrameworkElement NavigationContent => _navigationArea;
    public FrameworkElement PropertiesContent => IsEmbedded ? _embeddedProperties : _fieldsPanel;
    public ComboBox ThemeSelector => _themeChoice;
    public string SelectedThemeId => _isNew ? "user_draft" : _theme;
    internal string WorkingThemeId => _theme;
    public IEnumerable<(string Id, string Name, string? Preview)> ThemeGalleryChoices
    {
        get
        {
            foreach (var theme in _metadata.GetProperty("themes").EnumerateArray())
                yield return (theme.GetProperty("id").GetString()!, theme.GetProperty("name").GetString()!, theme.GetProperty("preview").GetString());
            if (_isNew) yield return (SelectedThemeId, _working["name"]!.GetValue<string>(), _baseTheme);
        }
    }
    public event EventHandler? StateChanged;
    public event EventHandler? NavigationChanged;
    public event EventHandler? CloseupRequested;
    public bool CanUndo => !_invalidated && !_busy && _undoStack.Count > 0;
    public bool CanRedo => !_invalidated && !_busy && _redoStack.Count > 0;
    public bool HasUnsavedChanges => HasChanges || (IsEmbedded && _owner.TransitDraftChanged) || _pendingInput || _errors.Count > 0;
    public bool CanSave => !_invalidated && !_busy && HasUnsavedChanges;
    public bool CanDelete => !_invalidated && !_busy && !_isNew && !_isBuiltin;

    public void UpdateGalleryPreviews(JsonElement themes)
    {
        var metadata = JsonNode.Parse(_metadata.GetRawText())!.AsObject();
        metadata["themes"] = JsonNode.Parse(themes.GetRawText());
        _metadata = JsonSerializer.SerializeToElement(metadata);
    }
    public bool IsBusy => _busy;
    public bool HasValidationErrors => _errors.Count > 0;
    public string StatusText => _status.Text.Length > 0 ? _status.Text : _previewStatus.Text;
    public string CurrentSection => _section;
    public string? CurrentRoadLevel => _roadLevel;
    public string CurrentLabelType => _labelType;

    // data 与身份取自当前工作副本，不能把首次打开时的元数据当成最新草稿。
    public JsonElement WorkingThemeMetadata
    {
        get
        {
            var metadata = JsonNode.Parse(_metadata.GetRawText())!.AsObject();
            metadata["data"] = _working.DeepClone();
            metadata["savedData"] = JsonNode.Parse(_saved);
            metadata["theme"] = _theme; metadata["baseTheme"] = _baseTheme;
            metadata["isNew"] = _isNew; metadata["isBuiltin"] = _isBuiltin;
            metadata["documentEpoch"] = _documentEpoch; metadata["settingsRevision"] = _settingsRevision;
            metadata["hasUnsavedChanges"] = HasUnsavedChanges;
            return JsonSerializer.SerializeToElement(metadata);
        }
    }

    public bool Flush() => !_invalidated && FlushInputs();
    public Task<bool> SaveAsync() => CommitAsync();
    public Task<bool> ConfirmLeaveAsync() => ConfirmCloseAsync();
    public Task CopyAsync() => CopyThemeAsync();
    public Task NewAsync() => NewThemeAsync();
    public Task RenameAsync() => RenameThemeAsync();
    public Task ImportAsync() => ImportThemeAsync();
    public Task ExportAsync() => ExportThemeAsync();
    public Task<bool> SelectThemeByIdAsync(string themeId) => LoadThemeAsync(themeId);
    public void Dispose() => Invalidate();
    public void Undo() => RestoreHistory(_undoStack, _redoStack);
    public void Redo() => RestoreHistory(_redoStack, _undoStack);

    public void FocusStyle(string stylePath)
    {
        var parts = stylePath.Split('.');
        FocusStyle(parts[0], parts[0] == "roads" && parts.Length > 1 && _working["roads"]?[parts[1]] is JsonObject
            ? parts[1] : null, parts[0] == "labels" && parts.Length > 1 ? parts[1] : null);
    }

    public async Task<bool> FlushForExportAsync()
    {
        if (_invalidated || _busy) return false;
        _flushingPreviewInputs = true;
        try { if (!FlushInputs()) return false; }
        finally { _flushingPreviewInputs = false; }
        if (!IsEmbedded) return true;
        if (_lastPublishedTheme == Snapshot() && _embeddedPreviewGate.CurrentCount != 0) return true;
        CancelPreview(); _flushingEmbeddedPreview = true; SetBusy(true);
        _previewDelay = new CancellationTokenSource();
        var token = _previewDelay.Token;
        try
        {
            await PublishEmbeddedPreviewAsync(token);
            return !_invalidated && !token.IsCancellationRequested && _lastPublishedTheme == Snapshot();
        }
        catch (OperationCanceledException) { return false; }
        catch (InvalidOperationException error)
        { Notice(UiLanguage.Text("ThemeEditorDialog_Preview_PreviewAfterDelayAsync_DE72B02080") + error.Message); return false; }
        finally { SetBusy(false); _flushingEmbeddedPreview = false; }
    }

    public async Task<bool> RefreshExternalSettingsRevisionAsync()
    {
        if (_invalidated || _busy) return false;
        SetBusy(true);
        try
        {
            var draft = await _engine.RequestAsync("getThemeDraft", new { theme = _theme, documentEpoch = _documentEpoch });
            if (_invalidated || draft.GetProperty("documentEpoch").GetInt32() != _documentEpoch) return false;
            if (draft.GetProperty("theme").GetString() != _theme)
                throw new InvalidOperationException(UiLanguage.Text("ThemeEditor_ThemeSwitchUnavailable"));
            if (!JsonNode.DeepEquals(JsonNode.Parse(draft.GetProperty("savedData").GetRawText()), JsonNode.Parse(_saved)))
                throw new InvalidOperationException(UiLanguage.Text("ThemeEditor_ExternalThemeChanged"));
            _settingsRevision = draft.GetProperty("settingsRevision").GetString();
            var metadata = JsonNode.Parse(_metadata.GetRawText())!.AsObject();
            metadata["settingsRevision"] = _settingsRevision;
            metadata["themes"] = JsonNode.Parse(draft.GetProperty("themes").GetRawText());
            _metadata = JsonSerializer.SerializeToElement(metadata);
            UpdateThemeChoices(); UpdateStatus();
            return true;
        }
        catch (InvalidOperationException error) { Notice(error.Message); return false; }
        finally { SetBusy(false); }
    }

    public bool FocusStyle(string section, string? roadLevel = null, string? labelType = null)
        => SelectStyle(section, roadLevel, labelType, clearSearch: true);

    private bool SelectStyle(string section, string? roadLevel, string? labelType, bool clearSearch)
    {
        if (_invalidated || _busy || _building || !FlushInputs()) { UpdateNavigationSelection(); return false; }
        if (section != "__basic__" && _working[section] is not JsonObject) return false;
        if (roadLevel == "all") roadLevel = null;
        if (section == "roads" && roadLevel is not null && _working["roads"]?[roadLevel] is not JsonObject) return false;
        if (section == "labels" && labelType is not null && _working["labels"]?[labelType] is not JsonObject) return false;
        _section = section; _roadLevel = section == "roads" ? roadLevel : null;
        if (roadLevel is not null) _selectedRoad = roadLevel;
        if (labelType is not null) _labelType = labelType;
        var building = _building; _building = true;
        if (clearSearch) _search.Text = "";
        _building = building;
        RebuildNavigation(); RebuildFields();
        return true;
    }

    private void BuildEmbeddedWorkbench()
    {
        _commands = new Grid();
        _saveButton = new Button();
        _embeddedProperties.Content = _fieldsPanel;
        Name(_themeChoice, UiLanguage.Text("ThemeEditor_Select"), "theme.choice");
        _themeChoice.SelectionChanged += async (_, _) => await SelectThemeAsync();
        _navigationArea.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _navigationArea.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        _search.Margin = new Thickness(12, 14, 12, 4);
        Name(_search, UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_2874151BD3"), "theme.search");
        _navigationArea.Children.Add(_search);
        _search.TextChanged += (_, _) =>
        {
            if (_invalidated || _building || !FlushInputs()) return;
            RebuildNavigation(); RebuildFields();
        };
        _navigation.OpenPaneLength = 224;
        _navigation.SizeChanged += (_, _) => _navigation.OpenPaneLength = _navigation.ActualWidth;
        Name(_navigation, UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_A3041FFF31"), "theme.navigation");
        _navigation.SelectionChanged += (_, args) =>
        {
            if (_building) return;
            if (args.SelectedItem is NavigationViewItem { Tag: StyleFocus focus })
            {
                if (SelectStyle(focus.Section, focus.RoadLevel, focus.LabelType, clearSearch: false)) CloseupRequested?.Invoke(this, EventArgs.Empty);
            }
            else if (args.SelectedItem is NavigationViewItem { Tag: string section } && SelectStyle(section, null, null, clearSearch: false))
                CloseupRequested?.Invoke(this, EventArgs.Empty);
        };
        Grid.SetRow(_navigation, 1); _navigationArea.Children.Add(_navigation);
        _pageHeader.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        _pageHeader.Children.Add(_sectionTitle);
        _sectionTitle.FontSize = 18;
        Name(_restore, UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_AC9EA45E9D"), "theme.restore");
        _restore.Click += (_, _) => RestoreSection();
        _sampleSurface = Surface(_samplePanel, "LayerFillColorDefaultBrush", new Thickness(12));
        _sampleSurface.CornerRadius = new CornerRadius(5);
        _navigationArea.Loaded += (_, _) => SchedulePreview();
    }

    private bool InFocusedStyle(string[] path)
    {
        if (!IsEmbedded || path.Length < 2 || path[0] != "roads") return true;
        return _roadLevel is null ? !RoadLevels.Any(level => level.Key == path[1]) : path[1] == _roadLevel;
    }

    private void UpdateNavigationDirtyMarks()
    {
        var saved = JsonNode.Parse(_saved)!.AsObject();
        foreach (var (key, item) in _navItems)
        {
            var parts = key.Split('.');
            bool changed;
            if (key == "__basic__")
                changed = _working.Where(pair => pair.Value is not JsonObject)
                    .Any(pair => !JsonNode.DeepEquals(pair.Value, saved[pair.Key]));
            else if (key == "roads.all")
                changed = _working["roads"]!.AsObject().Where(pair => !RoadLevels.Any(level => level.Key == pair.Key))
                    .Any(pair => !JsonNode.DeepEquals(pair.Value, saved["roads"]?[pair.Key]));
            else if (parts.Length == 2)
                changed = !JsonNode.DeepEquals(_working[parts[0]]?[parts[1]], saved[parts[0]]?[parts[1]]);
            else changed = !JsonNode.DeepEquals(_working[key], saved[key]);
            var title = Microsoft.UI.Xaml.Automation.AutomationProperties.GetName(item);
            item.Content = title + (changed ? " ●" : "");
            var hint = changed ? title + " · " + UiLanguage.Text("CityEditor_Unsaved") : title;
            if (parts[0] is not ("__basic__" or "canvas" or "watermark"))
                hint += "\n" + UiLanguage.Text("Sidebar_Locate");
            ToolTipService.SetToolTip(item, hint);
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetHelpText(item, hint);
        }
    }

    private void BuildFocusedRoadsPage()
    {
        var roads = _working["roads"]!.AsObject();
        if (_roadLevel is not null && roads[_roadLevel] is JsonObject road)
        {
            foreach (var (key, value) in road)
                if (value is not null) BuildNode(value, ["roads", _roadLevel, key], _fieldsPanel);
            var all = new Button { Content = UiLanguage.Text("ThemeEditor_AllRoads") };
            Name(all, UiLanguage.Text("ThemeEditor_AllRoads"), "theme.roads.all");
            all.Click += (_, _) => FocusStyle("roads"); _fieldsPanel.Children.Add(all);
            return;
        }
        foreach (var (key, value) in roads)
        {
            if (value is null || RoadLevels.Any(level => level.Key == key) || !Matches(value, ["roads", key])) continue;
            if (key is "opacity") _fieldsPanel.Children.Add(SliderField(value, ["roads", key], Label(["roads", key])));
            else BuildNode(value, ["roads", key], _fieldsPanel);
        }
    }

    private async Task PublishEmbeddedPreviewAsync(CancellationToken token)
    {
        await _embeddedPreviewGate.WaitAsync(token);
        try
        {
            if (_invalidated || token.IsCancellationRequested) return;
            var snapshot = Snapshot();
            if (snapshot != _lastPublishedTheme)
            {
                var theme = _theme;
                await _workingThemeChanged!(JsonSerializer.SerializeToElement(_working));
                // 已接受的写入不会随等待它的界面任务取消。撤销必须以引擎最后收到的状态为准。
                _lastPublishedTheme = theme == _theme ? snapshot : null;
                if (_invalidated || token.IsCancellationRequested) return;
            }
            _previewStatus.Text = ""; UpdateStatus();
        }
        finally { _embeddedPreviewGate.Release(); }
    }
}
