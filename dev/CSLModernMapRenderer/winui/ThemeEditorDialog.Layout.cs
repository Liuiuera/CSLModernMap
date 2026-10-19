// 组织主题编辑器的页面布局。
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Markup;
using Microsoft.UI.Xaml.Media;

namespace CSLModernMapWinUI;

internal sealed partial class ThemeEditorDialog
{
    private readonly Grid _workspace = new(), _navigationArea = new(), _compactNavigation = new() { ColumnSpacing = 8, Padding = new Thickness(12, 8, 12, 8) };
    private readonly Grid _previewLayout = new() { RowSpacing = 8 }, _footer = new() { ColumnSpacing = 12, RowSpacing = 6, Padding = new Thickness(18, 10, 18, 10) };
    private readonly Grid _header = new() { RowSpacing = 8, ColumnSpacing = 12, Padding = new Thickness(18, 14, 18, 14) };
    private readonly NavigationView _navigation = new()
    {
        PaneDisplayMode = NavigationViewPaneDisplayMode.Left, IsPaneOpen = true,
        OpenPaneLength = 164, CompactPaneLength = 0, IsPaneToggleButtonVisible = false,
        IsBackButtonVisible = NavigationViewBackButtonVisible.Collapsed,
        IsSettingsVisible = false, IsTitleBarAutoPaddingEnabled = false,
    };
    private readonly ComboBox _pageChoice = new() { HorizontalAlignment = HorizontalAlignment.Stretch, FontSize = 14 };
    private readonly Dictionary<string, NavigationViewItem> _navItems = new();
    private readonly Dictionary<string, NavigationViewItem> _navSections = new();
    private readonly TextBlock _source = new() { FontSize = 12, TextTrimming = TextTrimming.CharacterEllipsis };
    private FrameworkElement _editingSurface = null!, _previewSurface = null!;
    private StackPanel _commands = null!;
    private int _layoutMode = -1;

    private static Border Surface(UIElement child, string brush, Thickness? padding = null)
    {
        var border = (Border)XamlReader.Load($"<Border xmlns=\"http://schemas.microsoft.com/winfx/2006/xaml/presentation\" Background=\"{{ThemeResource {brush}}}\" BorderBrush=\"{{ThemeResource DividerStrokeColorDefaultBrush}}\" BorderThickness=\"0,0,1,0\" />");
        border.Child = child; if (padding is { } inset) border.Padding = inset;
        return border;
    }
    private void BuildWorkbench()
    {
        _window.Root.Padding = new Thickness(0); _window.Root.RowSpacing = 0;
        var body = new Grid();
        body.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        body.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        body.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        _source.Text = UiLanguage.Format("ThemeEditorDialog_Layout_BuildWorkbench_B3A22B5C00", _theme); ToolTipService.SetToolTip(_source, _savePath);
        var heading = new StackPanel { Spacing = 4 };
        heading.Children.Add(_source); heading.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_EC9A1CEB6D"), FontSize = 24 });
        _header.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        _header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        _header.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _header.Children.Add(heading);
        _commands = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, VerticalAlignment = VerticalAlignment.Center };
        foreach (var (button, title) in new[] { (_undo, UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_926A50B98E")), (_redo, UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_03717B6F10")), (_restore, UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_AC9EA45E9D")) })
        { Name(button, title); ToolTipService.SetToolTip(button, title); _commands.Children.Add(button); }
        _undo.Click += (_, _) => RestoreHistory(_undoStack, _redoStack);
        _redo.Click += (_, _) => RestoreHistory(_redoStack, _undoStack);
        _restore.Click += (_, _) => RestoreSection();
        var more = new Button { Content = UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_38844B135C") }; Name(more, UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_9DF1CDC60C"));
        var menu = new MenuFlyout();
        var saveAs = new MenuFlyoutItem { Text = UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_7BB3396904") }; saveAs.Click += async (_, _) => await SaveAsAsync();
        var reload = new MenuFlyoutItem { Text = UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_7BDD5CE1E2") }; reload.Click += async (_, _) => await ReloadAsync();
        var raw = new MenuFlyoutItem { Text = UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_BA1125B2AC") }; raw.Click += async (_, _) => await EditJsonAsync();
        menu.Items.Add(saveAs); menu.Items.Add(reload); menu.Items.Add(new MenuFlyoutSeparator()); menu.Items.Add(raw);
        more.Flyout = menu; _commands.Children.Add(more);
        Grid.SetColumn(_commands, 1); _header.Children.Add(_commands); body.Children.Add(_header);
        _compactNavigation.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _compactNavigation.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        Grid.SetRow(_pageChoice, 1); _compactNavigation.Children.Add(_pageChoice);
        _pageChoice.SelectionChanged += (_, _) =>
        {
            if (_pageChoice.SelectedItem is ComboBoxItem item && item.Tag is string key) SelectSection(key);
        };
        Grid.SetRow(_compactNavigation, 1); body.Children.Add(_compactNavigation);
        _navigationArea.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _navigationArea.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        _search.Margin = new Thickness(12, 14, 12, 4); _navigationArea.Children.Add(_search);
        Name(_search, UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_2874151BD3"), "theme.search"); Name(_pageChoice, UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_5E92DE25E6"), "theme.page");
        _search.TextChanged += (_, _) =>
        {
            if (_building || !FlushInputs()) return;
            RebuildNavigation(); RebuildFields();
        };
        Name(_navigation, UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_A3041FFF31"), "theme.navigation");
        _navigation.SelectionChanged += (_, args) =>
        {
            if (args.SelectedItem is NavigationViewItem item && item.Tag is string key) SelectSection(key);
        };
        Grid.SetRow(_navigation, 1); _navigationArea.Children.Add(_navigation);
        var fieldsScroll = new ScrollViewer { Content = _fieldsPanel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, HorizontalContentAlignment = HorizontalAlignment.Stretch };
        _editingSurface = Surface(fieldsScroll, "LayerFillColorDefaultBrush");
        BuildPreviewLayout();
        _previewSurface = Surface(_previewLayout, "ApplicationPageBackgroundThemeBrush", new Thickness(12, 16, 12, 12));
        Grid.SetRow(_workspace, 2); body.Children.Add(_workspace);
        _window.SetBody(body);
        _applyButton = new Button { Content = UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_13381EB64C") };
        _saveButton = new Button { Content = UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_6997A520F0"), Style = (Style)Application.Current.Resources["AccentButtonStyle"] };
        Name(_applyButton, UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_13381EB64C"), "theme.apply"); Name(_saveButton, UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_6997A520F0"), "theme.save");
        _applyButton.Click += async (_, _) => await CommitAsync(null);
        _saveButton.Click += async (_, _) => await CommitAsync(null, true);
        _window.InputCommitTarget = _saveButton;
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8, HorizontalAlignment = HorizontalAlignment.Right };
        actions.Children.Add(_applyButton); actions.Children.Add(_saveButton);
        _footer.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        _footer.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        _footer.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _status.VerticalAlignment = VerticalAlignment.Center; _footer.Children.Add(_status);
        Grid.SetColumn(actions, 1); _footer.Children.Add(actions);
        _window.Root.Children.Remove(_window.Actions);
        var footerSurface = Surface(_footer, "WorkspaceOutputHeaderBrush"); Grid.SetRow(footerSurface, 1);
        _window.Root.Children.Add(footerSurface);
        _workspace.SizeChanged += (_, _) => ArrangeWorkbench();
        _window.Root.Loaded += (_, _) => ArrangeWorkbench();
    }
    private void BuildPreviewLayout()
    {
        for (var i = 0; i < 4; i++) _previewLayout.RowDefinitions.Add(new RowDefinition { Height = i == 2 ? new GridLength(1, GridUnitType.Star) : GridLength.Auto });
        _previewLayout.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Layout_BuildPreviewLayout_07C73EBABF"), FontSize = 14, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        Grid.SetRow(_previewStatus, 1); _previewLayout.Children.Add(_previewStatus);
        Grid.SetRow(_preview, 2); _previewLayout.Children.Add(_preview);
        var sampleBorder = Surface(_samplePanel, "LayerFillColorDefaultBrush", new Thickness(12));
        sampleBorder.BorderThickness = new Thickness(1); sampleBorder.CornerRadius = new CornerRadius(5);
        Grid.SetRow(sampleBorder, 3); _previewLayout.Children.Add(sampleBorder);
    }
    private void ArrangeWorkbench()
    {
        var width = _workspace.ActualWidth;
        var mode = width > 940 ? 2 : width > 640 ? 1 : 0;
        if (_layoutMode == mode) return;
        if (_layoutMode >= 0) FlushInputs();
        _layoutMode = mode;
        _navigation.OpenPaneLength = mode == 2 ? 164 : 150;
        _workspace.Children.Clear(); _workspace.ColumnDefinitions.Clear(); _workspace.RowDefinitions.Clear();
        _previewLayout.ColumnDefinitions.Clear();
        _header.RowDefinitions.Clear(); _header.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _footer.RowDefinitions.Clear(); _footer.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        Grid.SetRow(_commands, 0); Grid.SetColumn(_commands, 1); Grid.SetColumnSpan(_commands, 1);
        var actions = (FrameworkElement)_footer.Children[1];
        Grid.SetRow(actions, 0); Grid.SetColumn(actions, 1); Grid.SetColumnSpan(actions, 1);
        _compactNavigation.Visibility = mode == 0 ? Visibility.Visible : Visibility.Collapsed;
        _navigationArea.Children.Remove(_search); _compactNavigation.Children.Remove(_search);
        if (mode == 0)
        {
            _search.Margin = new Thickness(0, 0, 0, 6); _compactNavigation.Children.Add(_search);
            _header.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            Grid.SetRow(_commands, 1); Grid.SetColumn(_commands, 0); Grid.SetColumnSpan(_commands, 2);
            _footer.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            Grid.SetRow(actions, 1); Grid.SetColumn(actions, 0); Grid.SetColumnSpan(actions, 2);
        }
        else { _search.Margin = new Thickness(12, 14, 12, 4); _navigationArea.Children.Add(_search); }
        _workspace.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        if (mode == 2)
        {
            foreach (var size in new[] { new GridLength(164), new GridLength(1, GridUnitType.Star), new GridLength(306) })
                _workspace.ColumnDefinitions.Add(new ColumnDefinition { Width = size });
            Place(_navigationArea, 0, 0); Place(_editingSurface, 0, 1); Place(_previewSurface, 0, 2);
        }
        else
        {
            if (mode == 1) _workspace.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(150) });
            _workspace.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            _workspace.RowDefinitions.Add(new RowDefinition { Height = new GridLength(.65, GridUnitType.Star) });
            if (mode == 1) { Place(_navigationArea, 0, 0); Grid.SetRowSpan(_navigationArea, 2); }
            Place(_editingSurface, 0, mode == 1 ? 1 : 0); Place(_previewSurface, 1, mode == 1 ? 1 : 0);
        }
        var sample = (FrameworkElement)_previewLayout.Children[3];
        Grid.SetRow(sample, 3); Grid.SetColumn(sample, 0);
        if (mode == 1)
        {
            _previewLayout.ColumnSpacing = 10;
            _previewLayout.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            _previewLayout.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            Grid.SetRow(sample, 2); Grid.SetColumn(sample, 1);
        }
        foreach (var child in _previewLayout.Children.Take(2).OfType<FrameworkElement>()) Grid.SetColumnSpan(child, mode == 1 ? 2 : 1);
        foreach (var update in _responsiveUpdates) update();
        void Place(FrameworkElement element, int row, int column)
        { Grid.SetRow(element, row); Grid.SetColumn(element, column); Grid.SetRowSpan(element, 1); _workspace.Children.Add(element); }
    }
    private void RebuildNavigation()
    {
        _building = true;
        try
        {
            _navigation.MenuItems.Clear(); _navItems.Clear(); _navSections.Clear(); _pageChoice.Items.Clear();
            var sections = new List<string> { "__basic__" };
            sections.AddRange(SectionNames.Keys.Where(key => key != "__basic__" && _working[key] is not null));
            sections.AddRange(_working.Where(pair => pair.Value is JsonObject && !SectionNames.ContainsKey(pair.Key)).Select(pair => pair.Key));
            sections = sections.Where(key => _search.Text.Trim().Length == 0 || SectionMatches(key)).ToList();
            if (!sections.Contains(_section) && sections.Count > 0) _section = sections[0];
            foreach (var key in sections)
                _pageChoice.Items.Add(new ComboBoxItem { Content = SectionLabel(key), Tag = key });
            if (sections.Contains("__basic__")) _navigation.MenuItems.Add(NavItem("__basic__"));
            foreach (var (title, keys) in new[]
            {
                (UiLanguage.Text("ThemeEditorDialog_Layout_RebuildNavigation_AA6507D6F0"), new[] { "canvas", "terrain", "water", "forest", "built_area" }),
                (UiLanguage.Text("ThemeEditorDialog_Layout_RebuildNavigation_2BE8A26742"), new[] { "roads", "buildings", "zone", "land_use", "district", "procedural_objects" }),
                (UiLanguage.Text("ThemeEditorDialog_Layout_RebuildNavigation_57F78BA1F9"), new[] { "railway", "transports" }),
                (UiLanguage.Text("ThemeEditorDialog_Layout_RebuildNavigation_4F0C1E4BF0"), new[] { "labels", "poi_icons", "watermark" }),
                (UiLanguage.Text("ThemeEditorDialog_Layout_RebuildNavigation_E2B12D8F0A"), sections.Where(key => !SectionNames.ContainsKey(key)).ToArray()),
            })
            {
                var available = keys.Where(sections.Contains).ToArray(); if (available.Length == 0) continue;
                var open = _search.Text.Trim().Length > 0 || available.Contains(_section) || _expanded.GetValueOrDefault("nav." + title, true);
                var group = new NavigationViewItem { Content = title, SelectsOnInvoked = false, IsExpanded = open };
                Name(group, title, "theme.navigation.group." + title);
                group.RegisterPropertyChangedCallback(NavigationViewItem.IsExpandedProperty, (_, _) =>
                { if (!_building) _expanded["nav." + title] = group.IsExpanded; });
                foreach (var key in available) { group.MenuItems.Add(NavItem(key)); _navSections[key] = group; }
                _navigation.MenuItems.Add(group);
            }
            if (sections.Count == 0) _navigation.MenuItems.Add(new NavigationViewItem { Content = UiLanguage.Text("ThemeEditorDialog_Layout_RebuildNavigation_9CB6C68C5F"), IsEnabled = false });
            UpdateNavigationSelection();
        }
        finally { _building = false; }
    }
    private NavigationViewItem NavItem(string key)
    {
        var item = new NavigationViewItem { Content = SectionLabel(key), Tag = key };
        Name(item, SectionLabel(key), "theme.nav." + key);
        _navItems[key] = item; return item;
    }
    private void UpdateNavigationSelection()
    {
        var building = _building;
        _building = true;
        if (_navSections.TryGetValue(_section, out var group)) group.IsExpanded = true;
        _navigation.SelectedItem = _navItems.GetValueOrDefault(_section);
        _pageChoice.SelectedItem = _pageChoice.Items.OfType<ComboBoxItem>().FirstOrDefault(item => (string)item.Tag == _section);
        _building = building;
    }
    private static Brush ResourceBrush(string key) => ((Border)XamlReader.Load($"<Border xmlns=\"http://schemas.microsoft.com/winfx/2006/xaml/presentation\" Background=\"{{ThemeResource {key}}}\" />")).Background;
    private async Task EditJsonAsync()
    {
        if (!FlushInputs()) return;
        var raw = new TextBox { Text = _working.ToJsonString(new System.Text.Json.JsonSerializerOptions { WriteIndented = true }),
            AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, Height = Math.Min(390, Math.Max(160, _window.Root.ActualHeight - 180)),
            Width = Math.Min(480, Math.Max(180, _window.Root.ActualWidth - 96)) };
        var errorText = new TextBlock { TextWrapping = TextWrapping.Wrap };
        var panel = new StackPanel { Spacing = 8 }; panel.Children.Add(raw); panel.Children.Add(errorText);
        var dialog = new ContentDialog { Title = UiLanguage.Text("ThemeEditorDialog_Layout_EditJsonAsync_2142FD3F0D"), Content = panel,
            PrimaryButtonText = UiLanguage.Text("ThemeEditorDialog_Layout_EditJsonAsync_9212C5565A"), CloseButtonText = UiLanguage.Text("ThemeEditorDialog_Layout_EditJsonAsync_2CD0F3BE87"), XamlRoot = _window.Root.XamlRoot };
        dialog.PrimaryButtonClick += async (_, args) =>
        {
            var deferral = args.GetDeferral();
            try { await ReplaceDraftAsync(raw.Text); }
            catch (Exception error) when (error is System.Text.Json.JsonException or InvalidOperationException)
            { errorText.Text = error.Message; args.Cancel = true; }
            finally { deferral.Complete(); }
        };
        await dialog.ShowAsync();
    }
}
