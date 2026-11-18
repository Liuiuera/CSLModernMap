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
            foreach (var builtin in new[] { true, false })
            {
                var choices = _metadata.GetProperty("themes").EnumerateArray().Where(item => item.GetProperty("builtin").GetBoolean() == builtin).ToArray();
                if (choices.Length == 0) continue;
                _themeChoice.Items.Add(new ComboBoxItem { Content = UiLanguage.Text(builtin ? "ThemeEditor_Builtins" : "ThemeEditor_Personal"), IsEnabled = false });
                foreach (var choice in choices)
                {
                    var item = new ComboBoxItem { Content = choice.GetProperty("name").GetString(), Tag = choice.GetProperty("id").GetString() };
                    _themeChoice.Items.Add(item);
                    if (!_isNew && (string?)item.Tag == _theme) _themeChoice.SelectedItem = item;
                }
            }
            if (_isNew)
            {
                var draft = new ComboBoxItem { Content = _working["name"]?.GetValue<string>(), Tag = "__draft__" };
                _themeChoice.Items.Add(draft); _themeChoice.SelectedItem = draft;
            }
        }
        finally { _syncingThemes = false; }
    }

    private void LoadDraft(JsonElement draft)
    {
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
        _undoStack.Clear(); _redoStack.Clear(); _operationStatus = "";
        UpdateThemeChoices(); RebuildNavigation(); RebuildFields(); UpdateHistoryButtons(); UpdateStatus(); SchedulePreview(immediate: true);
    }

    private void SetBusy(bool value)
    {
        _busy = value;
        _themeChoice.IsEnabled = _fieldsScroll.IsEnabled = !value;
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
        if (_syncingThemes || _busy || _themeChoice.SelectedItem is not ComboBoxItem { Tag: string next } || next == "__draft__" || (!_isNew && next == _theme)) return;
        if (!await ConfirmCloseAsync()) { UpdateThemeChoices(); return; }
        SetBusy(true);
        try
        {
            await _engine.RequestAsync("setTheme", new { theme = next });
            LoadDraft(await _engine.RequestAsync("getThemeDraft"));
            _applied = true;
            await _owner.ApplyEditorChangesAsync();
        }
        catch (InvalidOperationException error) { UpdateThemeChoices(); Notice(error.Message); }
        finally { SetBusy(false); }
    }

    private async Task NewThemeAsync()
    {
        if (_busy || !await ConfirmCloseAsync()) return;
        var bases = new ComboBox { HorizontalAlignment = HorizontalAlignment.Stretch, Header = UiLanguage.Text("ThemeEditor_Base") };
        foreach (var theme in _metadata.GetProperty("themes").EnumerateArray().Where(item => item.GetProperty("builtin").GetBoolean()))
        {
            var item = new ComboBoxItem { Content = theme.GetProperty("name").GetString(), Tag = theme.GetProperty("id").GetString() };
            bases.Items.Add(item);
            if ((string?)item.Tag == _baseTheme) bases.SelectedItem = item;
        }
        if (bases.SelectedItem is null) bases.SelectedIndex = 0;
        var dialog = new ContentDialog { XamlRoot = _window.Root.XamlRoot, Title = UiLanguage.Text("ThemeEditor_New"), Content = bases,
            PrimaryButtonText = UiLanguage.Text("ThemeEditor_New"), CloseButtonText = UiLanguage.Text("ThemeEditor_Cancel") };
        SetBusy(true);
        ContentDialogResult result;
        try { result = await dialog.ShowAsync(); }
        finally { SetBusy(false); }
        if (result != ContentDialogResult.Primary) return;
        SetBusy(true);
        try { LoadDraft(await _engine.RequestAsync("newThemeDraft", new { baseTheme = (string)((ComboBoxItem)bases.SelectedItem!).Tag! })); }
        catch (InvalidOperationException error) { Notice(error.Message); }
        finally { SetBusy(false); }
    }

    private async Task CopyThemeAsync()
    {
        if (_busy || !FlushInputs()) return;
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
        var dialog = new ContentDialog { XamlRoot = _window.Root.XamlRoot, Title = UiLanguage.Text("ThemeEditor_Name"), Content = content,
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
        if (_busy || !FlushInputs()) return;
        var name = await PromptNameAsync(_working["name"]!.GetValue<string>());
        if (name is null) return;
        Change(["name"], JsonValue.Create(name)); RebuildFields();
        await CommitAsync(nameConfirmed: true);
    }

    private async Task<bool> CommitAsync(bool nameConfirmed = false)
    {
        if (_busy || !FlushInputs()) return false;
        if (_isBuiltin && !nameConfirmed)
        {
            var current = _working["name"]!.GetValue<string>();
            var original = JsonNode.Parse(_saved)!["name"]!.GetValue<string>();
            var name = await PromptNameAsync(current == original ? _metadata.GetProperty("suggestedName").GetString()! : current);
            if (name is null) return false;
            Change(["name"], JsonValue.Create(name)); RebuildFields();
        }
        SetBusy(true);
        try
        {
            var result = await _engine.RequestAsync("commitThemeDraft", new
            { theme = _theme, data = JsonNode.Parse(Snapshot()), @new = _isNew, baseTheme = _baseTheme,
                documentEpoch = _documentEpoch, settingsRevision = _settingsRevision });
            if (_invalidated) return false;
            _applied = true; LoadDraft(result.GetProperty("draft"));
            Notice(UiLanguage.Text("ThemeEditorDialog_CommitAsync_61D9FC644D"), temporary: true);
            try { await _owner.ApplyEditorChangesAsync(); }
            catch (InvalidOperationException error) { Notice(UiLanguage.Text("ThemeEditorDialog_CommitAsync_0C8CCBB74C") + error.Message); }
            return true;
        }
        catch (InvalidOperationException error) { Notice(UiLanguage.Text("ThemeEditorDialog_CommitAsync_D40E2AA10B") + error.Message); return false; }
        finally { SetBusy(false); }
    }

    private async Task ImportThemeAsync()
    {
        if (_busy || !await ConfirmCloseAsync()) return;
        var picker = new FileOpenPicker();
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(_window));
        picker.FileTypeFilter.Add(".yaml"); picker.FileTypeFilter.Add(".yml");
        var file = await picker.PickSingleFileAsync();
        if (file is null) return;
        SetBusy(true);
        try { LoadDraft(await _engine.RequestAsync("importThemeDraft", new { path = file.Path })); }
        catch (InvalidOperationException error) { Notice(error.Message); }
        finally { SetBusy(false); }
    }

    private async Task ExportThemeAsync()
    {
        if (_busy || !FlushInputs()) return;
        var picker = new FileSavePicker();
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(_window));
        picker.FileTypeChoices.Add(UiLanguage.Text("ThemeEditorDialog_SaveAsAsync_E8B51EFA46"), new List<string> { ".yaml" });
        var name = _working["name"]!.GetValue<string>();
        picker.SuggestedFileName = string.Concat(name.Select(c => Path.GetInvalidFileNameChars().Contains(c) ? '_' : c));
        var file = await picker.PickSaveFileAsync();
        if (file is null) return;
        SetBusy(true);
        try { await _engine.RequestAsync("exportThemeDraft", new { path = file.Path, data = JsonNode.Parse(Snapshot()) }); Notice(UiLanguage.Text("ThemeEditor_Exported"), temporary: true); }
        catch (InvalidOperationException error) { Notice(error.Message); }
        finally { SetBusy(false); }
    }
}
