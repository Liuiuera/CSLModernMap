// 管理主题编辑器的状态与操作。
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Automation;
using Windows.Storage.Pickers;

namespace CSLModernMapWinUI;

internal sealed partial class ThemeEditorDialog
{
    private static readonly Regex HexColor = new("^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$", RegexOptions.Compiled);
    private readonly (string Key, string Label)[] RoadLevels =
        [("highway", UiLanguage.Text("ThemeEditorDialog_RoadLevels_534C553126")), ("main", UiLanguage.Text("ThemeEditorDialog_RoadLevels_F5E3A1B5F2")), ("branch", UiLanguage.Text("ThemeEditorDialog_RoadLevels_109688E695")), ("special", UiLanguage.Text("ThemeEditorDialog_RoadLevels_EDE0519A4C")), ("beautification", UiLanguage.Text("ThemeEditorDialog_RoadLevels_35CDE54CEB"))];
    private readonly (string Key, string Label)[] LabelTypes =
        [("road", UiLanguage.Text("ThemeEditorDialog_LabelTypes_A44C7BA211")), ("district", UiLanguage.Text("ThemeEditorDialog_LabelTypes_4F56683E73")), ("building", UiLanguage.Text("ThemeEditorDialog_LabelTypes_5B46860A8E")), ("park", UiLanguage.Text("ThemeEditorDialog_LabelTypes_E852DCA995")), ("stop", UiLanguage.Text("ThemeEditorDialog_LabelTypes_A59FE62777"))];
    private readonly Dictionary<string, string> SectionNames = new()
    {
        ["__basic__"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_72F87EFABB"), ["canvas"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_3906276FD1"), ["terrain"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_51695AD45A"), ["water"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_207E6D1CF5"),
        ["forest"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_3FDE320BE3"), ["built_area"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_433091EABE"),
        ["zone"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_8592878D7E"), ["land_use"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_D84EBADC9F"), ["district"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_0983E2CCDD"),
        ["roads"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_A44C7BA211"), ["buildings"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_5B46860A8E"), ["procedural_objects"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_87FC97C6B8"),
        ["railway"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_97492A0E5D"), ["transports"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_1AB216CC0D"), ["poi_icons"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_9FC7D0CF1D"),
        ["labels"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_655D4E9C75"), ["watermark"] = UiLanguage.Text("ThemeEditorDialog_SectionNames_AA9F217C81"),
    };
    private readonly MainWindow _owner;
    private readonly EngineClient _engine;
    private readonly EditorWindow _window;
    private JsonElement _metadata;
    private JsonObject _working;
    private string _theme, _savePath, _committed, _saved;
    private string? _settingsRevision;
    private int _documentEpoch;
    private bool _applied, _invalidated, _building, _busy, _flushingPreviewInputs;
    private string _section = "__basic__", _labelType = "road", _selectedRoad = "highway";
    private readonly Stack<string> _undoStack = new(), _redoStack = new();
    private readonly Dictionary<string, Action> _checks = new();
    private readonly HashSet<string> _errors = new();
    private readonly List<Action> _sampleUpdates = new();
    private readonly Dictionary<string, bool> _expanded = new();
    private readonly StackPanel _fieldsPanel = new() { Spacing = 12, Padding = new Thickness(18, 16, 18, 18) };
    private readonly TextBox _search = new() { PlaceholderText = UiLanguage.Text("ThemeEditorDialog__search_2874151BD3"), FontSize = 12 };
    private readonly TextBlock _status = new() { TextWrapping = TextWrapping.Wrap, FontSize = 12 };
    private readonly TextBlock _previewStatus = new() { TextWrapping = TextWrapping.Wrap, FontSize = 12 };
    private readonly Image _preview = new() { Stretch = Stretch.Uniform, HorizontalAlignment = HorizontalAlignment.Stretch, VerticalAlignment = VerticalAlignment.Stretch };
    private readonly StackPanel _samplePanel = new() { Spacing = 8 };
    private readonly Button _undo = new() { Content = new SymbolIcon(Symbol.Undo) }, _redo = new() { Content = new SymbolIcon(Symbol.Redo) };
    private readonly Button _restore = new() { Content = UiLanguage.Text("ThemeEditorDialog__restore_AC9EA45E9D") };
    private Button _applyButton = null!, _saveButton = null!;
    private CancellationTokenSource? _previewDelay, _sampleCancellation;
    private string? _previewRequestId;
    private string _operationStatus = UiLanguage.Text("ThemeEditorDialog__operationStatus_5B4A1BAE39");
    private int _sampleRevision;

    public ThemeEditorDialog(MainWindow owner, EngineClient engine, XamlRoot root, JsonElement draft)
    {
        _owner = owner; _engine = engine; _metadata = draft.Clone();
        _documentEpoch = draft.GetProperty("documentEpoch").GetInt32();
        _settingsRevision = draft.TryGetProperty("settingsRevision", out var revision) ? revision.GetString() : null;
        _theme = draft.GetProperty("theme").GetString()!;
        _savePath = draft.GetProperty("savePath").GetString()!;
        _working = JsonNode.Parse(draft.GetProperty("data").GetRawText())!.AsObject();
        _committed = Snapshot();
        _saved = JsonNode.Parse(draft.GetProperty("savedData").GetRawText())!.ToJsonString();
        _window = new EditorWindow(UiLanguage.Text("ThemeEditorDialog_View_74F45217E6"), 1120, 740, owner: owner);
        BuildWorkbench();
        _window.CanCloseAsync = ConfirmCloseAsync;
        RebuildNavigation(); RebuildFields(); UpdateHistoryButtons(); UpdateStatus();
    }

    public void Activate() => _window.Activate();
    public void FocusSection(string section) => SelectSection(section);
    public Task<bool> RequestCloseAsync() => _window.RequestCloseAsync();
    public async Task<bool> ShowAsync()
    {
        SchedulePreview(immediate: true);
        await _window.ShowAsync();
        CancelPreview(); _sampleCancellation?.Cancel();
        try { await _engine.RequestAsync("cancelThemePreview"); }
        catch (InvalidOperationException error) { StartupTrace.Write("Theme preview close: " + error.Message); }
        return _applied;
    }
    public void Invalidate()
    {
        _invalidated = true; CancelPreview(); _sampleCancellation?.Cancel(); _window.ForceClose();
    }
    private async Task<bool> ConfirmCloseAsync()
    {
        FlushInputs();
        if (_invalidated || (Snapshot() == _saved && _errors.Count == 0)) return true;
        var result = await new ContentDialog { XamlRoot = _window.Root.XamlRoot,
            Title = UiLanguage.Text("ThemeEditorDialog_ConfirmCloseAsync_E7ADF10792"), PrimaryButtonText = UiLanguage.Text("ThemeEditorDialog_ConfirmCloseAsync_A3030BF8F1"), SecondaryButtonText = UiLanguage.Text("ThemeEditorDialog_ConfirmCloseAsync_8BCC3E177F"), CloseButtonText = UiLanguage.Text("ThemeEditorDialog_ConfirmCloseAsync_FD4B9E3B6C") }.ShowAsync();
        if (result == ContentDialogResult.Primary) return await CommitAsync(null, true);
        return result == ContentDialogResult.Secondary;
    }
    private bool FlushInputs()
    {
        foreach (var check in _checks.Values.ToArray()) check();
        return _errors.Count == 0;
    }
    private void SelectSection(string key)
    {
        if (_building || key == _section) return;
        if (!FlushInputs()) { UpdateNavigationSelection(); return; }
        _section = key;
        UpdateNavigationSelection(); RebuildFields(); SchedulePreview();
    }
    private void Change(string[] path, JsonNode? updated)
    {
        if (_building) return;
        JsonNode parent = _working;
        foreach (var part in path.SkipLast(1)) parent = parent is JsonArray array ? array[int.Parse(part)]! : parent[part]!;
        var key = path[^1];
        var old = parent is JsonArray values ? values[int.Parse(key)] : parent[key];
        if (JsonNode.DeepEquals(old, updated)) return;
        _undoStack.Push(Snapshot()); _redoStack.Clear();
        if (parent is JsonArray list) list[int.Parse(key)] = updated?.DeepClone();
        else parent[key] = updated?.DeepClone();
        _operationStatus = UiLanguage.Text("ThemeEditorDialog_Change_285A8966FE");
        UpdateHistoryButtons(); UpdateStatus(); UpdateSamples(); SchedulePreview();
    }
    private void RestoreHistory(Stack<string> source, Stack<string> target)
    {
        if (_busy || !FlushInputs() || source.Count == 0) return;
        target.Push(Snapshot()); _working = JsonNode.Parse(source.Pop())!.AsObject();
        _operationStatus = Snapshot() == _committed ? Snapshot() == _saved ? UiLanguage.Text("ThemeEditorDialog_RestoreHistory_B502823A08") : UiLanguage.Text("ThemeEditorDialog_RestoreHistory_DB576085E9") : UiLanguage.Text("ThemeEditorDialog_RestoreHistory_285A8966FE");
        RebuildNavigation(); RebuildFields(); UpdateHistoryButtons(); UpdateStatus(); SchedulePreview();
    }
    private void RestoreSection()
    {
        if (_busy || !FlushInputs()) return;
        var defaults = JsonNode.Parse(_metadata.GetProperty("defaults").GetRawText())!.AsObject();
        var before = Snapshot();
        void Restore(JsonObject current, JsonObject baseline, string[] path, bool force = false)
        {
            foreach (var (key, value) in current.ToArray())
            {
                var child = path.Append(key).ToArray();
                if (Hidden(child) || value is null) continue;
                if (value is JsonObject obj && !IsPair(obj))
                {
                    Restore(obj, baseline[key] as JsonObject ?? new JsonObject(), child, force || MatchesField(child));
                    continue;
                }
                if (value is JsonArray array && baseline[key] is JsonArray originalArray)
                {
                    if (force || MatchesField(child))
                    {
                        var restored = new JsonArray();
                        for (var index = 0; index < originalArray.Count; index++)
                        {
                            if (index < array.Count && array[index] is JsonObject item && originalArray[index] is JsonObject originalItem)
                            {
                                var copy = (JsonObject)item.DeepClone();
                                Restore(copy, originalItem, child.Append(index.ToString()).ToArray(), true);
                                restored.Add(copy);
                            }
                            else restored.Add(originalArray[index]?.DeepClone());
                        }
                        current[key] = restored;
                        continue;
                    }
                    for (var index = 0; index < Math.Min(array.Count, originalArray.Count); index++)
                    {
                        var itemPath = child.Append(index.ToString()).ToArray();
                        if (array[index] is JsonObject item && originalArray[index] is JsonObject originalItem)
                            Restore(item, originalItem, itemPath);
                        else if (array[index] is { } itemValue && Matches(itemValue, itemPath)) array[index] = originalArray[index]?.DeepClone();
                    }
                    continue;
                }
                if (value is JsonObject pair && IsPair(pair) && baseline[key] is JsonObject originalPair)
                { Restore(pair, originalPair, child, force || MatchesField(child)); continue; }
                if (!force && !Matches(value, child)) continue;
                if (baseline.TryGetPropertyValue(key, out var original)) current[key] = original?.DeepClone();
            }
        }
        if (_section == "__basic__")
        {
            foreach (var key in new[] { "name", "description" })
                if (_working[key] is { } value && Matches(value, [key]))
                {
                    if (defaults.TryGetPropertyValue(key, out var original)) _working[key] = original?.DeepClone();
                    else _working.Remove(key);
                }
        }
        else if (_working[_section] is JsonObject section) Restore(section, defaults[_section] as JsonObject ?? new JsonObject(), [_section]);
        if (before == Snapshot()) return;
        _undoStack.Push(before); _redoStack.Clear(); _operationStatus = UiLanguage.Text("ThemeEditorDialog_RestoreSection_153601830D");
        RebuildFields(); UpdateHistoryButtons(); UpdateStatus(); SchedulePreview();
    }
    private async Task ReplaceDraftAsync(string json)
    {
        var parsed = JsonNode.Parse(json) as JsonObject ?? throw new JsonException(UiLanguage.Text("ThemeEditorDialog_ReplaceDraftAsync_F1DC302FE8"));
        var result = await _engine.RequestAsync("describeThemeDraft", new { data = parsed });
        var next = JsonNode.Parse(result.GetProperty("data").GetRawText())!.AsObject();
        if (JsonNode.DeepEquals(next, _working)) return;
        _undoStack.Push(Snapshot()); _redoStack.Clear(); _working = next;
        var metadata = JsonNode.Parse(_metadata.GetRawText())!.AsObject();
        metadata["fields"] = JsonNode.Parse(result.GetProperty("fields").GetRawText());
        _metadata = JsonSerializer.SerializeToElement(metadata);
        _operationStatus = UiLanguage.Text("ThemeEditorDialog_ReplaceDraftAsync_285A8966FE");
        RebuildNavigation(); RebuildFields(); UpdateHistoryButtons(); UpdateStatus(); SchedulePreview();
    }
    private async Task ReloadAsync()
    {
        if (_busy || !await ConfirmCloseAsync()) return;
        try
        {
            var draft = await _engine.RequestAsync("getThemeDraft");
            CancelPreview(); _sampleCancellation?.Cancel();
            _metadata = draft.Clone(); _documentEpoch = draft.GetProperty("documentEpoch").GetInt32();
            _settingsRevision = draft.GetProperty("settingsRevision").GetString();
            _theme = draft.GetProperty("theme").GetString()!; _savePath = draft.GetProperty("savePath").GetString()!;
            _working = JsonNode.Parse(draft.GetProperty("data").GetRawText())!.AsObject();
            _committed = Snapshot(); _undoStack.Clear(); _redoStack.Clear();
            _saved = JsonNode.Parse(draft.GetProperty("savedData").GetRawText())!.ToJsonString();
            _operationStatus = UiLanguage.Text("ThemeEditorDialog_ReloadAsync_EC7E90FAA2");
            _source.Text = UiLanguage.Format("ThemeEditorDialog_ReloadAsync_B3A22B5C00", _theme);
            RebuildNavigation(); RebuildFields(); UpdateHistoryButtons(); UpdateStatus(); SchedulePreview(immediate: true);
        }
        catch (InvalidOperationException error) { _operationStatus = UiLanguage.Text("ThemeEditorDialog_ReloadAsync_328AF35A09") + error.Message; UpdateStatus(); }
    }
    // 校验并提交当前主题草稿，按需将结果保存到指定文件。
    private async Task<bool> CommitAsync(string? path, bool persist = false)
    {
        if (_busy || !FlushInputs()) return false;
        _busy = true; _applyButton.IsEnabled = _saveButton.IsEnabled = false;
        UpdateHistoryButtons();
        try
        {
            var submitted = Snapshot();
            var result = await _engine.RequestAsync("commitThemeDraft", new
            { theme = _theme, data = JsonNode.Parse(submitted), savePath = path, persist, documentEpoch = _documentEpoch, settingsRevision = _settingsRevision });
            if (_invalidated) return false;
            _applied = result.GetProperty("applied").GetBoolean();
            _settingsRevision = result.GetProperty("settingsRevision").GetString();
            if (_applied)
            {
                _committed = submitted;
                var saved = result.GetProperty("saved").GetBoolean();
                if (saved) _saved = submitted;
                if (path is not null) _savePath = path;
                _operationStatus = Snapshot() != submitted ? UiLanguage.Text("ThemeEditorDialog_CommitAsync_58AD70BDD1") : saved ? UiLanguage.Text("ThemeEditorDialog_CommitAsync_61D9FC644D") : UiLanguage.Text("ThemeEditorDialog_CommitAsync_DB576085E9");
                UpdateStatus();
                try { await _owner.ApplyEditorChangesAsync(); }
                catch (InvalidOperationException error) { _operationStatus += UiLanguage.Text("ThemeEditorDialog_CommitAsync_0C8CCBB74C") + error.Message; UpdateStatus(); }
            }
            return _applied;
        }
        catch (InvalidOperationException error) { _operationStatus = (persist || path is not null ? UiLanguage.Text("ThemeEditorDialog_CommitAsync_D40E2AA10B") : UiLanguage.Text("ThemeEditorDialog_CommitAsync_757737699B")) + error.Message; UpdateStatus(); return false; }
        finally { _busy = false; _applyButton.IsEnabled = _saveButton.IsEnabled = true; UpdateHistoryButtons(); }
    }
    private async Task SaveAsAsync()
    {
        if (!FlushInputs()) return;
        var picker = new FileSavePicker();
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(_window));
        picker.FileTypeChoices.Add(UiLanguage.Text("ThemeEditorDialog_SaveAsAsync_E8B51EFA46"), new List<string> { ".yaml" }); picker.SuggestedFileName = _theme;
        var file = await picker.PickSaveFileAsync();
        if (file is not null) await CommitAsync(file.Path, true);
    }
    private string Snapshot() => _working.ToJsonString();
    private void UpdateHistoryButtons()
    { _undo.IsEnabled = !_busy && _undoStack.Count > 0; _redo.IsEnabled = !_busy && _redoStack.Count > 0; _restore.IsEnabled = !_busy; }
    private void UpdateStatus() => _status.Text = _errors.Count > 0 ? UiLanguage.Text("ThemeEditorDialog_UpdateStatus_94B3E39B5C") : _operationStatus;
    private string SectionLabel(string key) => SectionNames.GetValueOrDefault(key, key);
    private static bool IsPair(JsonObject obj) => obj.ContainsKey("fixed") && obj.ContainsKey("scaled");
    private static Brush BrushFor(string value) => new SolidColorBrush(AppearanceControls.ParseColor(value));
    private static void Name(DependencyObject control, string name, string? id = null)
    { AutomationProperties.SetName(control, name); if (id is not null) AutomationProperties.SetAutomationId(control, id); }
}
