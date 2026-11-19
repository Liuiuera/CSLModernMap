using System.Text.Json;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using RibbonSpace.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private readonly List<(RibbonButton Button, bool Delete)> _themeGalleryCommands = new();

    private void AddThemeGalleryCommands(RibbonGallery gallery)
    {
        foreach (var (key, icon, action) in new (string, Symbol, Func<Task>)[] {
            ("New", Symbol.Add, () => OpenThemeEditorAsync(null, true)),
            ("Edit", Symbol.Edit, EditThemeAsync),
            ("Delete", Symbol.Delete, DeleteGalleryThemeAsync) })
        {
            var button = new RibbonButton { Label = UiLanguage.Text("ThemeEditor_" + key),
                Icon = new FontIcon { Glyph = char.ConvertFromUtf32((int)icon), FontSize = 14, Width = 16, Height = 16 } };
            AutomationProperties.SetAutomationId(button, "theme.gallery." + key.ToLowerInvariant());
            button.Click += async (_, _) => { gallery.CloseDropDown(); await RunAsync(action); };
            gallery.FooterItems.Add(button);
            _themeGalleryCommands.Add((button, key == "Delete"));
        }
    }

    private void UpdateThemeGalleryCommands()
    {
        var editor = _artworkEditor ?? _themeEditor;
        var selected = _artworkEditor?.SelectedThemeId ?? (_snapshot.ValueKind == JsonValueKind.Object
            && _snapshot.TryGetProperty("theme", out var theme) ? theme.GetString() : null);
        foreach (var (button, delete) in _themeGalleryCommands)
            button.IsEnabled = !_engineBusy && editor?.IsBusy != true && (!delete ||
                (editor is not null ? editor.CanDelete : selected?.StartsWith("user_", StringComparison.Ordinal) == true));
    }

    private async Task DeleteGalleryThemeAsync()
    {
        if ((_artworkEditor ?? _themeEditor) is { } editor) { await editor.DeleteAsync(); return; }
        var draft = await _engine.RequestAsync("getThemeDraft");
        if (draft.GetProperty("isBuiltin").GetBoolean() || draft.GetProperty("isNew").GetBoolean()) return;
        if (await ThemeEditorDialog.ConfirmDeleteAsync(Root.XamlRoot, draft) != ContentDialogResult.Primary) return;
        await _engine.RequestAsync("deleteTheme", new { theme = draft.GetProperty("theme").GetString(),
            documentEpoch = draft.GetProperty("documentEpoch").GetInt32(), settingsRevision = draft.GetProperty("settingsRevision").GetString() });
        await ApplyEditorChangesAsync();
    }

    private async Task RefreshThemeThumbnailsAsync(string identity)
    {
        await RefreshSnapshotAsync();
        var themes = _snapshot.GetProperty("themes");
        _artworkEditor?.UpdateGalleryPreviews(themes);
        _themeEditor?.UpdateGalleryPreviews(themes);
        UpdateThemeGalleries();
        var choice = _themeChoices.FirstOrDefault(item => item.Id == identity);
        if (choice is not null)
        {
            _themeChoices[_themeChoices.IndexOf(choice)] = new ThemeGalleryChoice(choice.Id, choice.Name, choice.PreviewId);
            _themeGalleries!.Synchronize(_artworkEditor?.SelectedThemeId ?? _snapshot.GetProperty("theme").GetString());
        }
    }
}
