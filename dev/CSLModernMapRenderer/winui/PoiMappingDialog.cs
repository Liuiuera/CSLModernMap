// 提供 POI 映射编辑对话框。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace CSLModernMapWinUI;

internal sealed class PoiMappingDialog
{
    private readonly EngineClient _engine;
    private readonly EditorWindow _window;
    private readonly ComboBox _game = new() { MinWidth = 100 };
    private readonly ComboBox _mappingFilter = new() { MinWidth = 130 };
    private readonly TextBox _search = new() { PlaceholderText = Text("Search") };
    private readonly ListView _entriesView = new() { SelectionMode = ListViewSelectionMode.Single };
    private readonly TextBlock _name = new() { FontSize = 20, TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _nativeId = new() { TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
    private readonly ToggleSwitch _display = new() { Header = Text("Display"), OnContent = Text("Visible"), OffContent = Text("Hidden") };
    private readonly TextBlock _status = new() { TextWrapping = TextWrapping.Wrap };
    private readonly Dictionary<string, Image> _images = new();
    private readonly Dictionary<string, TextBlock> _filenames = new();
    private readonly Dictionary<string, string> _imageUrls = new();
    private readonly HashSet<string> _dirty = new();
    private JsonObject _entries;
    private readonly JsonElement _libraries;
    private readonly string _token;
    private string? _selected;
    private bool _loading, _closed, _saved, _invalidated;
    private int _imageRevision;
    public Func<Task>? Changed { get; set; }

    private static string Text(string key) => UiLanguage.Text("PoiDirect_" + key);

    public PoiMappingDialog(EngineClient engine, XamlRoot root, JsonElement data)
    {
        _engine = engine;
        _entries = JsonNode.Parse(data.GetProperty("entries").GetRawText())!.AsObject();
        _libraries = data.GetProperty("libraries").Clone();
        _token = data.GetProperty("token").GetString()!;
        _window = new EditorWindow(Text("Title"), 1080, 740);
        _window.AddAction(Text("Save"), async () => { if (await SaveAsync()) await _window.RequestCloseAsync(); });
        _window.AddAction(Text("Restore"), () => { RestoreDefaults(); return Task.CompletedTask; });
        _window.AddAction(Text("Cancel"), async () => { await _window.RequestCloseAsync(); });
        foreach (var (label, value) in new[] { (Text("AllGames"), ""), ("Cities: Skylines", "cs1"), ("Cities: Skylines II", "cs2") })
            _game.Items.Add(new ComboBoxItem { Content = label, Tag = value });
        foreach (var (label, value) in new[] { (Text("AllMappings"), ""), (Text("Mapped"), "mapped"), (Text("Unmapped"), "unmapped") })
            _mappingFilter.Items.Add(new ComboBoxItem { Content = label, Tag = value });
        _game.SelectedIndex = _mappingFilter.SelectedIndex = 0;
        var filters = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        filters.Children.Add(_game); filters.Children.Add(_mappingFilter);
        var left = new Grid { RowSpacing = 8 };
        left.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        left.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        left.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        left.Children.Add(filters); Grid.SetRow(_search, 1); left.Children.Add(_search);
        Grid.SetRow(_entriesView, 2); left.Children.Add(_entriesView);
        var detail = new StackPanel { Spacing = 16, Padding = new Thickness(12, 0, 0, 0) };
        detail.Children.Add(_name); detail.Children.Add(_nativeId);
        var iconRows = new Grid { ColumnSpacing = 16 };
        iconRows.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        int column = 0;
        foreach (var library in new[] { "cn_icons" })
        {
            var label = new TextBlock { Text = "CSLModernMap POI", FontSize = 16 };
            var image = new Image { Width = 76, Height = 76, Stretch = Stretch.Uniform };
            var filename = new TextBlock { TextWrapping = TextWrapping.Wrap, HorizontalAlignment = HorizontalAlignment.Center };
            _images[library] = image; _filenames[library] = filename;
            var buttonContent = new StackPanel { Spacing = 8, Padding = new Thickness(8) };
            buttonContent.Children.Add(image); buttonContent.Children.Add(filename);
            var button = new Button { Content = buttonContent, HorizontalAlignment = HorizontalAlignment.Stretch };
            var flyout = BuildImagePicker(library);
            button.Flyout = flyout;
            button.IsEnabled = false;
            _entriesView.SelectionChanged += (_, _) => button.IsEnabled = _entriesView.SelectedItem is not null;
            var panel = new StackPanel { Spacing = 8 };
            panel.Children.Add(label); panel.Children.Add(button);
            Grid.SetColumn(panel, column++); iconRows.Children.Add(panel);
        }
        detail.Children.Add(iconRows); detail.Children.Add(_display);
        detail.Children.Add(new TextBlock { Text = Text("EmptyHelp"), TextWrapping = TextWrapping.Wrap });
        var body = new Grid { RowSpacing = 12 };
        body.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        body.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        body.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var warnings = new TextBlock { Text = string.Join("\n", data.GetProperty("warnings").EnumerateArray().Select(value => value.GetString())), TextWrapping = TextWrapping.Wrap };
        body.Children.Add(warnings);
        var panels = new ResponsiveEditorPanels((Text("Mapping"), left), (Text("ImagesDisplay"), new ScrollViewer { Content = detail }));
        Grid.SetRow(panels, 1); body.Children.Add(panels);
        Grid.SetRow(_status, 2); body.Children.Add(_status); _window.SetBody(body);
        _game.SelectionChanged += (_, _) => RebuildEntries();
        _mappingFilter.SelectionChanged += (_, _) => RebuildEntries();
        _search.TextChanged += (_, _) => RebuildEntries();
        _entriesView.SelectionChanged += (_, _) => SelectEntry();
        _display.Toggled += (_, _) =>
        {
            if (_loading || _selected is null) return;
            var entry = _entries[_selected]!.AsObject();
            if (entry["display"]!.GetValue<bool>() == _display.IsOn) return;
            entry["display"] = _display.IsOn; MarkDirty(_selected);
        };
        RebuildEntries();
    }

    private Flyout BuildImagePicker(string library)
    {
        var flyout = new Flyout();
        var search = new TextBox { PlaceholderText = Text("ImageSearch") };
        var grid = new GridView { SelectionMode = ListViewSelectionMode.Single, MaxHeight = 360, IsItemClickEnabled = true };
        var clear = new Button { Content = Text("NoImage") };
        var panel = new StackPanel { Spacing = 8, Width = 350 };
        panel.Children.Add(search); panel.Children.Add(clear); panel.Children.Add(grid);
        flyout.Content = panel;
        void Populate()
        {
            grid.Items.Clear();
            foreach (var item in _libraries.GetProperty(library).EnumerateArray())
            {
                string file = item.GetProperty("file").GetString()!;
                string name = item.GetProperty("name").GetString()!;
                string url = item.GetProperty("url").GetString()!;
                _imageUrls[library + "/" + file] = url;
                if (!(file + " " + name).Contains(search.Text, StringComparison.OrdinalIgnoreCase)) continue;
                var image = new Image { Width = 48, Height = 48, Stretch = Stretch.Uniform };
                var content = new StackPanel { Width = 90, Spacing = 4 };
                content.Children.Add(image);
                content.Children.Add(new TextBlock { Text = name, TextTrimming = TextTrimming.CharacterEllipsis, HorizontalAlignment = HorizontalAlignment.Center });
                var choice = new GridViewItem { Content = content, Tag = file };
                ToolTipService.SetToolTip(choice, file); grid.Items.Add(choice);
                _ = PreviewImages.SetAsync(image, url, () => !_closed);
            }
        }
        search.TextChanged += (_, _) => Populate();
        grid.ItemClick += (_, args) => { SetImage(library, (string)((GridViewItem)args.ClickedItem).Tag); flyout.Hide(); };
        clear.Click += (_, _) => { SetImage(library, ""); flyout.Hide(); };
        Populate();
        return flyout;
    }

    private void RebuildEntries()
    {
        string game = (string)((ComboBoxItem)_game.SelectedItem).Tag;
        string filter = (string)((ComboBoxItem)_mappingFilter.SelectedItem).Tag;
        var selected = _selected;
        _entriesView.Items.Clear();
        foreach (var (key, value) in _entries)
        {
            var entry = value!.AsObject();
            bool mapped = entry["icons"]!.AsObject().Any(pair => !string.IsNullOrEmpty(pair.Value!.GetValue<string>()));
            if (game.Length > 0 && entry["game"]!.GetValue<string>() != game) continue;
            if ((filter == "mapped" && !mapped) || (filter == "unmapped" && mapped)) continue;
            string title = entry["name"]!.GetValue<string>();
            if (!(title + " " + key + " " + entry["names"]!.ToJsonString()).Contains(_search.Text, StringComparison.OrdinalIgnoreCase)) continue;
            var text = new TextBlock { Text = title + "\n" + key, TextWrapping = TextWrapping.Wrap, MaxWidth = 450 };
            var item = new ListViewItem { Content = text, Tag = key };
            _entriesView.Items.Add(item);
            if (key == selected) _entriesView.SelectedItem = item;
        }
        if (_entriesView.SelectedItem is null && _entriesView.Items.Count > 0) _entriesView.SelectedIndex = 0;
        if (_entriesView.Items.Count == 0) { _selected = null; SelectEntry(); }
        _status.Text = UiLanguage.Format("PoiDirect_Count", _entriesView.Items.Count, _dirty.Count);
    }

    private void SelectEntry()
    {
        _selected = (_entriesView.SelectedItem as ListViewItem)?.Tag as string;
        _loading = true;
        try
        {
            var entry = _selected is null ? null : _entries[_selected]!.AsObject();
            _name.Text = entry?["name"]?.GetValue<string>() ?? "";
            _nativeId.Text = _selected ?? "";
            _display.IsEnabled = entry is not null;
            _display.IsOn = entry?["display"]?.GetValue<bool>() ?? false;
            int revision = ++_imageRevision;
            foreach (string library in _images.Keys)
            {
                string file = entry?["icons"]?[library]?.GetValue<string>() ?? "";
                _filenames[library].Text = file.Length > 0 ? file : Text("NoImage");
                _images[library].Source = null;
                if (_imageUrls.TryGetValue(library + "/" + file, out string? url))
                    _ = PreviewImages.SetAsync(_images[library], url, () => !_closed && revision == _imageRevision);
            }
        }
        finally { _loading = false; }
    }

    private void SetImage(string library, string file)
    {
        if (_selected is null) return;
        var icons = _entries[_selected]!["icons"]!.AsObject();
        if (icons[library]!.GetValue<string>() == file) return;
        icons[library] = file; MarkDirty(_selected); SelectEntry();
    }
    private void MarkDirty(string key)
    {
        _dirty.Add(key);
        _status.Text = UiLanguage.Format("PoiDirect_Count", _entriesView.Items.Count, _dirty.Count);
    }
    private void RestoreDefaults()
    {
        foreach (var (key, value) in _entries)
        {
            var entry = value!.AsObject(); var defaults = entry["default"]!.AsObject();
            if (JsonNode.DeepEquals(entry["icons"], defaults["icons"]) && JsonNode.DeepEquals(entry["display"], defaults["display"])) continue;
            entry["icons"] = defaults["icons"]!.DeepClone(); entry["display"] = defaults["display"]!.DeepClone(); MarkDirty(key);
        }
        RebuildEntries(); SelectEntry();
    }
    private async Task<bool> SaveAsync()
    {
        if (_dirty.Count == 0) return true;
        try
        {
            var changes = new JsonObject();
            foreach (string key in _dirty)
                changes[key] = new JsonObject { ["icons"] = _entries[key]!["icons"]!.DeepClone(), ["display"] = _entries[key]!["display"]!.DeepClone() };
            await _engine.RequestAsync("savePoiMapping", new { token = _token, changes });
            _saved = true; _dirty.Clear();
            if (Changed is not null) await Changed();
            return true;
        }
        catch (Exception error) { _status.Text = error.Message; return false; }
    }
    public void Activate() => _window.Activate();
    public Task<bool> RequestCloseAsync() => _window.RequestCloseAsync();
    public void Invalidate() { _invalidated = true; _closed = true; _window.ForceClose(); }
    public async Task<bool> ShowAsync() { await _window.ShowAsync(); _closed = true; return _saved && !_invalidated; }
}
