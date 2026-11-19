// 主题选择、个人主题保存与文件交换。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage.Pickers;

namespace CSLModernMapWinUI;

internal sealed partial class ThemeEditorDialog
{
    private bool _syncingThemes;
    private readonly DispatcherTimer _noticeTimer = new() { Interval = TimeSpan.FromSeconds(3) };

    private void UpdateThemeChoices()
    {
        _syncingThemes = true;
        try
        {
            _themeChoice.Items.Clear();
            foreach (var builtin in new[] { false, true })
            {
                var choices = _metadata.GetProperty("themes").EnumerateArray().Where(item => item.GetProperty("builtin").GetBoolean() == builtin).ToArray();
                if (choices.Length == 0 && (builtin || !_isNew)) continue;
                var title = UiLanguage.Text(builtin ? "ThemeEditor_Builtins" : "ThemeEditor_Personal");
                var heading = new ComboBoxItem { Content = new TextBlock { Text = title, FontSize = 12,
                    FontWeight = Microsoft.UI.Text.FontWeights.SemiBold }, IsEnabled = false };
                Name(heading, title, builtin ? "theme.group.presets" : "theme.group.personal");
                _themeChoice.Items.Add(heading);
                foreach (var choice in choices)
                {
                    var item = new ComboBoxItem { Content = choice.GetProperty("name").GetString(), Tag = choice.GetProperty("id").GetString() };
                    _themeChoice.Items.Add(item);
                    if (!_isNew && (string?)item.Tag == _theme) _themeChoice.SelectedItem = item;
                }
                if (!builtin && _isNew)
                {
                    var draft = new ComboBoxItem { Content = _working["name"]?.GetValue<string>(), Tag = "__draft__" };
                    _themeChoice.Items.Add(draft); _themeChoice.SelectedItem = draft;
                }
            }
        }
        finally { _syncingThemes = false; }
    }

    private void LoadDraft(JsonElement draft, bool publishPreview = true)
    {
        if (_invalidated || draft.GetProperty("documentEpoch").GetInt32() != _documentEpoch) return;
        _noticeTimer.Stop(); CancelPreview(); _sampleCancellation?.Cancel(); _sampleRevision++;
        _metadata = draft.Clone();
        _documentEpoch = draft.GetProperty("documentEpoch").GetInt32();
        _settingsRevision = draft.GetProperty("settingsRevision").GetString();
        _theme = draft.GetProperty("theme").GetString()!;
        _baseTheme = draft.GetProperty("baseTheme").GetString();
        _isNew = draft.GetProperty("isNew").GetBoolean();
        _isBuiltin = draft.GetProperty("isBuiltin").GetBoolean();
        _working = JsonNode.Parse(draft.GetProperty("data").GetRawText())!.AsObject();
        _saved = JsonNode.Parse(draft.GetProperty("savedData").GetRawText())!.ToJsonString();
        _undoStack.Clear(); _redoStack.Clear(); _operationStatus = ""; _pendingInput = false; _lastPublishedTheme = null;
        if (_roadLevel is not null && _working["roads"]?[_roadLevel] is not JsonObject) _roadLevel = null;
        UpdateThemeChoices(); RebuildNavigation(); RebuildFields(); UpdateHistoryButtons(); UpdateStatus();
        if (publishPreview || !IsEmbedded) SchedulePreview(immediate: !IsEmbedded);
    }

    private void SetBusy(bool value)
    {
        _busy = value;
        _themeChoice.IsEnabled = _embeddedProperties.IsEnabled = _navigation.IsEnabled = !value && !_invalidated;
        if (!IsEmbedded) _fieldsScroll.IsEnabled = !value && !_invalidated;
        foreach (var command in _commands.Children.OfType<Control>()) command.IsEnabled = !value;
        UpdateHistoryButtons(); UpdateStatus();
    }

    private void Notice(string text, bool temporary = false)
    {
        _noticeTimer.Stop(); _operationStatus = text; UpdateStatus();
        if (!temporary) return;
        _noticeTimer.Tick -= ClearNotice; _noticeTimer.Tick += ClearNotice; _noticeTimer.Start();
    }

    private void ClearNotice(object? sender, object args)
    { _noticeTimer.Stop(); _operationStatus = ""; UpdateStatus(); }

    private async Task SelectThemeAsync()
    {
        if (_invalidated || _syncingThemes || _busy || _themeChoice.SelectedItem is not ComboBoxItem { Tag: string next } || next == "__draft__" || (!_isNew && next == _theme)) return;
        await LoadThemeAsync(next);
    }

    public async Task<bool> LoadThemeAsync(string themeId)
    {
        if (_invalidated || _busy) return false;
        if (!_isNew && themeId == _theme) return true;
        if (!_metadata.GetProperty("themes").EnumerateArray().Any(theme => theme.GetProperty("id").GetString() == themeId)) return false;
        if (!await ConfirmCloseAsync()) { UpdateThemeChoices(); return false; }
        if (_invalidated) return false;
        SetBusy(true);
        try
        {
            if (IsEmbedded)
            {
                CancelPreview();
                var draft = await _engine.RequestAsync("getThemeDraft", new { theme = themeId, documentEpoch = _documentEpoch });
                if (_invalidated || draft.GetProperty("documentEpoch").GetInt32() != _documentEpoch) return false;
                if (draft.GetProperty("theme").GetString() != themeId)
                    throw new InvalidOperationException(UiLanguage.Text("ThemeEditor_ThemeSwitchUnavailable"));
                LoadDraft(draft);
                return true;
            }
            await _engine.RequestAsync("setTheme", new { theme = themeId });
            LoadDraft(await _engine.RequestAsync("getThemeDraft"));
            _applied = true;
            await _owner.ApplyEditorChangesAsync();
            return true;
        }
        catch (InvalidOperationException error) { UpdateThemeChoices(); Notice(error.Message); return false; }
        finally { SetBusy(false); }
    }

    private async Task NewThemeAsync()
    {
        if (_invalidated || _busy || !await ConfirmCloseAsync()) return;
        SetBusy(true);
        string? selected;
        try { selected = await ChooseBaseAsync(DialogRoot, _metadata.GetProperty("themes"), _baseTheme); }
        finally { SetBusy(false); }
        if (_invalidated || selected is null) return;
        SetBusy(true);
        try { LoadDraft(await _engine.RequestAsync("newThemeDraft", new { baseTheme = selected })); }
        catch (InvalidOperationException error) { Notice(error.Message); }
        finally { SetBusy(false); }
    }

    internal static async Task<string?> ChooseBaseAsync(XamlRoot root, JsonElement themes, string? baseTheme)
    {
        var bases = new ComboBox { HorizontalAlignment = HorizontalAlignment.Stretch, Header = UiLanguage.Text("ThemeEditor_Base") };
        foreach (var theme in themes.EnumerateArray().Where(item => item.GetProperty("builtin").GetBoolean()))
        {
            var item = new ComboBoxItem { Content = theme.GetProperty("name").GetString(), Tag = theme.GetProperty("id").GetString() };
            bases.Items.Add(item);
            if ((string?)item.Tag == baseTheme) bases.SelectedItem = item;
        }
        if (bases.SelectedItem is null) bases.SelectedIndex = 0;
        var dialog = new ContentDialog { XamlRoot = root, Title = UiLanguage.Text("ThemeEditor_New"), Content = bases,
            PrimaryButtonText = UiLanguage.Text("ThemeEditor_New"), CloseButtonText = UiLanguage.Text("ThemeEditor_Cancel") };
        return await dialog.ShowAsync() == ContentDialogResult.Primary ? (string)((ComboBoxItem)bases.SelectedItem!).Tag! : null;
    }

    internal static async Task<ContentDialogResult> ConfirmDeleteAsync(XamlRoot root, JsonElement draft) => await new ContentDialog
    {
        XamlRoot = root, Title = UiLanguage.Text("ThemeEditor_Delete"),
        Content = UiLanguage.Format("ThemeEditor_DeleteConfirm", draft.GetProperty("data").GetProperty("name").GetString(),
            draft.GetProperty("themes").EnumerateArray().First(item => item.GetProperty("id").GetString() ==
                (draft.GetProperty("baseTheme").GetString() ?? "modern")).GetProperty("name").GetString()),
        PrimaryButtonText = UiLanguage.Text("ThemeEditor_Delete"), CloseButtonText = UiLanguage.Text("ThemeEditor_Cancel"),
        DefaultButton = ContentDialogButton.Close,
    }.ShowAsync();

    public async Task DeleteAsync()
    {
        if (!CanDelete || !await ConfirmCloseAsync() || !CanDelete) return;
        SetBusy(true); CancelPreview();
        if (IsEmbedded) await _embeddedPreviewGate.WaitAsync();
        try
        {
            if (await ConfirmDeleteAsync(DialogRoot, _metadata) != ContentDialogResult.Primary || _invalidated) return;
            var result = await _engine.RequestAsync("deleteTheme", new { theme = _theme,
                documentEpoch = _documentEpoch, settingsRevision = _settingsRevision });
            _applied = true;
            LoadDraft(result.GetProperty("draft"));
            await _owner.ApplyEditorChangesAsync();
        }
        catch (InvalidOperationException error) { Notice(error.Message); }
        finally { if (IsEmbedded) _embeddedPreviewGate.Release(); SetBusy(false); }
    }

    private async Task CopyThemeAsync()
    {
        if (_invalidated || _busy || !FlushInputs()) return;
        var data = JsonNode.Parse(Snapshot());
        SetBusy(true);
        try { LoadDraft(await _engine.RequestAsync("copyThemeDraft", new { data, baseTheme = _baseTheme })); }
        catch (InvalidOperationException error) { Notice(error.Message); }
        finally { SetBusy(false); }
    }

    private async Task<string?> PromptNameAsync(string suggested)
    {
        var input = new TextBox { Text = suggested, MaxLength = 64, Header = UiLanguage.Text("ThemeEditor_Name") };
        var errorText = new TextBlock { TextWrapping = TextWrapping.Wrap };
        var content = new StackPanel { Spacing = 8 }; content.Children.Add(input); content.Children.Add(errorText);
        var dialog = new ContentDialog { XamlRoot = DialogRoot, Title = UiLanguage.Text("ThemeEditor_Name"), Content = content,
            PrimaryButtonText = UiLanguage.Text("ThemeEditor_Save"), CloseButtonText = UiLanguage.Text("ThemeEditor_Cancel") };
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            var deferral = args.GetDeferral();
            try { await _engine.RequestAsync("validateThemeName", new { name = input.Text, theme = !_isNew && !_isBuiltin ? _theme : null }); }
            catch (InvalidOperationException error) { errorText.Text = error.Message; args.Cancel = true; }
            finally { deferral.Complete(); }
        };
        SetBusy(true);
        try { return await dialog.ShowAsync() == ContentDialogResult.Primary ? input.Text.Trim() : null; }
        finally { SetBusy(false); }
    }

    private async Task RenameThemeAsync()
    {
        if (_invalidated || _busy || !FlushInputs()) return;
        var name = await PromptNameAsync(_working["name"]!.GetValue<string>());
        if (_invalidated || name is null) return;
        Change(["name"], JsonValue.Create(name)); RebuildFields();
        await CommitAsync(nameConfirmed: true);
    }

    private async Task<bool> CommitAsync(bool nameConfirmed = false)
    {
        if (_invalidated || _busy || !FlushInputs()) return false;
        if (IsEmbedded && _owner.TransitDraftChanged && !HasChanges)
        {
            await _owner.SaveTransitDraftOptionsAsync();
            UpdateStatus();
            return true;
        }
        if (_isBuiltin && !nameConfirmed)
        {
            var current = _working["name"]!.GetValue<string>();
            var original = JsonNode.Parse(_saved)!["name"]!.GetValue<string>();
            var name = await PromptNameAsync(current == original ? _metadata.GetProperty("suggestedName").GetString()! : current);
            if (_invalidated || name is null) return false;
            Change(["name"], JsonValue.Create(name)); RebuildFields();
        }
        var artworkStarted = System.Diagnostics.Stopwatch.GetTimestamp();
        CancelPreview();
        SetBusy(true);
        if (IsEmbedded) await _embeddedPreviewGate.WaitAsync();
        try
        {
            if (_invalidated) return false;
            var result = await _engine.RequestAsync("commitThemeDraft", new
            { theme = _theme, data = JsonNode.Parse(Snapshot()), @new = _isNew, baseTheme = _baseTheme,
                documentEpoch = _documentEpoch, settingsRevision = _settingsRevision, artwork = IsEmbedded,
                importToken = _metadata.TryGetProperty("importToken", out var token) ? token.GetString() : null });
            if (_invalidated || result.GetProperty("draft").GetProperty("documentEpoch").GetInt32() != _documentEpoch) return false;
            if (IsEmbedded && _owner.IsTransitDraft) await _owner.SaveTransitDraftOptionsAsync();
            _applied = true; LoadDraft(result.GetProperty("draft"), publishPreview: false);
            if (IsEmbedded) { _lastPublishedTheme = Snapshot(); _previewStatus.Text = ""; }
            Notice(UiLanguage.Text("ThemeEditorDialog_CommitAsync_61D9FC644D"), temporary: true);
            try { await _owner.ApplyEditorChangesAsync(artworkStarted); }
            catch (InvalidOperationException error) { Notice(UiLanguage.Text("ThemeEditorDialog_CommitAsync_0C8CCBB74C") + error.Message); }
            return true;
        }
        catch (InvalidOperationException error) { Notice(UiLanguage.Text("ThemeEditorDialog_CommitAsync_D40E2AA10B") + error.Message); return false; }
        finally
        {
            if (IsEmbedded) _embeddedPreviewGate.Release();
            SetBusy(false);
        }
    }

    private async Task ImportThemeAsync()
    {
        if (_invalidated || _busy || !await ConfirmCloseAsync()) return;
        var picker = new FileOpenPicker();
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(PickerOwner));
        picker.FileTypeFilter.Add(".yaml"); picker.FileTypeFilter.Add(".yml");
        var file = await picker.PickSingleFileAsync();
        if (_invalidated || file is null) return;
        SetBusy(true);
        try { LoadDraft(await _engine.RequestAsync("importThemeDraft", new { path = file.Path })); }
        catch (InvalidOperationException error) { Notice(error.Message); }
        finally { SetBusy(false); }
    }

    public async Task ReviewPresetUpdateAsync()
    {
        if (_invalidated || _busy || _isNew || !await ConfirmCloseAsync()) return;
        SetBusy(true);
        try
        {
            await _owner.ShowThemeUpdateAsync(includeHandled: true, theme: _theme);
            LoadDraft(await _engine.RequestAsync("getThemeDraft", new { theme = _theme }));
        }
        catch (InvalidOperationException error) { Notice(error.Message); }
        finally { SetBusy(false); }
    }

    private async Task ExportThemeAsync()
    {
        if (_invalidated || _busy || !FlushInputs()) return;
        var picker = new FileSavePicker();
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(PickerOwner));
        picker.FileTypeChoices.Add(UiLanguage.Text("ThemeEditorDialog_SaveAsAsync_E8B51EFA46"), new List<string> { ".yaml" });
        var name = _working["name"]!.GetValue<string>();
        picker.SuggestedFileName = string.Concat(name.Select(c => Path.GetInvalidFileNameChars().Contains(c) ? '_' : c));
        var file = await picker.PickSaveFileAsync();
        if (_invalidated || file is null) return;
        SetBusy(true);
        try { await _engine.RequestAsync("exportThemeDraft", new { path = file.Path, data = JsonNode.Parse(Snapshot()) }); Notice(UiLanguage.Text("ThemeEditor_Exported"), temporary: true); }
        catch (InvalidOperationException error) { Notice(error.Message); }
        finally { SetBusy(false); }
    }
}
