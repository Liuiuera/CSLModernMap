// 建筑目录保持完整，筛选不改变地图图层和对象身份。
using System.Globalization;
using System.Text;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Markup;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;

namespace CSLModernMapWinUI;

internal sealed record BuildingListItem(int Id, string Name, string NameSource, string Category, string CategoryName, int[] MemberIds, string IconPath)
{
    private BitmapImage? _icon;
    public ImageSource? Icon => IconPath.Length == 0 ? null : _icon ??= new BitmapImage(new Uri(IconPath));
}

internal sealed class BuildingListPanel : Grid
{
    private List<BuildingListItem> _items = new();
    private Dictionary<int, BuildingListItem> _byMember = new();
    private Dictionary<int, string> _memberNames = new();
    private readonly TextBox _search;
    private readonly ComboBox _categories, _sort;
    private readonly CheckBox _gameNames;
    private readonly ListView _list;
    private readonly TextBlock _count, _notice;
    private readonly Button _clear;
    private readonly StackPanel _details = new() { Spacing = 12 };
    private readonly Border _surface;
    private readonly StackPanel _loading = CreateLoadingIndicator();
    private bool _updating;
    private int? _selected;
    private JsonElement? _detailModel, _detailSelection;
    internal Action<int?>? Selected { get; set; }
    internal Action<string>? CategoryChanged { get; set; }
    internal Action<bool>? GameNamesChanged { get; set; }
    internal bool UseGameNames { get; private set; }
    internal string Category => (_categories.SelectedItem as ComboBoxItem)?.Tag as string ?? "";

    internal BuildingListPanel(JsonElement catalog)
    {
        RowSpacing = 16;
        RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        var body = new Grid();
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.4, GridUnitType.Star) });
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        body.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        body.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        body.RowDefinitions.Add(new RowDefinition { Height = new GridLength(0) });
        var titleBar = new Grid { ColumnSpacing = 16 };
        titleBar.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        titleBar.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        titleBar.Children.Add(new TextBlock { Text = UiLanguage.Text("BuildingList_Open"), FontSize = 28,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center });
        _gameNames = new CheckBox { Content = UiLanguage.Text("BuildingList_GameNames"), VerticalAlignment = VerticalAlignment.Center };
        AutomationProperties.SetAutomationId(_gameNames, "BuildingListGameNames");
        Grid.SetColumn(_gameNames, 1); titleBar.Children.Add(_gameNames);
        Children.Add(titleBar);
        var heading = new StackPanel { Spacing = 12, Margin = new Thickness(16, 16, 16, 0) };
        _search = new TextBox { PlaceholderText = UiLanguage.Text("BuildingList_Search") };
        AutomationProperties.SetName(_search, UiLanguage.Text("BuildingList_Search"));
        AutomationProperties.SetAutomationId(_search, "BuildingListSearch");
        heading.Children.Add(_search);
        _categories = new ComboBox { Header = UiLanguage.Text("BuildingList_Category"), HorizontalAlignment = HorizontalAlignment.Stretch };
        AutomationProperties.SetAutomationId(_categories, "BuildingListCategory");
        _sort = new ComboBox { Header = UiLanguage.Text("BuildingList_Sort"), HorizontalAlignment = HorizontalAlignment.Stretch };
        AutomationProperties.SetAutomationId(_sort, "BuildingListSort");
        foreach (var key in new[] { "NameAsc", "NameDesc", "CategoryAsc", "CategoryDesc", "IdAsc", "IdDesc" })
            _sort.Items.Add(new ComboBoxItem { Content = UiLanguage.Text("BuildingList_" + key), Tag = key });
        _sort.SelectedIndex = 0;
        var filters = new Grid { ColumnSpacing = 10 };
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        filters.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        filters.Children.Add(_categories); Grid.SetColumn(_sort, 1); filters.Children.Add(_sort);
        heading.Children.Add(filters);
        _count = SecondaryText("", 12); _count.TextWrapping = TextWrapping.Wrap;
        heading.Children.Add(_count);
        _notice = new TextBlock { Text = UiLanguage.Text("BuildingList_OutsideFilter"), TextWrapping = TextWrapping.Wrap, Visibility = Visibility.Collapsed };
        heading.Children.Add(_notice);
        _clear = new Button { Content = UiLanguage.Text("BuildingList_Clear"), Visibility = Visibility.Collapsed };
        AutomationProperties.SetAutomationId(_clear, "BuildingListClear");
        heading.Children.Add(_clear);
        var columns = (Grid)XamlReader.Load("""
            <Grid xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" ColumnSpacing="12" Margin="12,0">
              <Grid.ColumnDefinitions><ColumnDefinition Width="24"/><ColumnDefinition Width="*"/><ColumnDefinition Width="92"/><ColumnDefinition Width="64"/></Grid.ColumnDefinitions>
            </Grid>
            """);
        foreach (var (column, key) in new[] { (1, "BuildingList_Name"), (2, "BuildingList_Category"), (3, "BuildingList_Id") })
        {
            var label = SecondaryText(UiLanguage.Text(key), 12);
            label.HorizontalAlignment = column == 3 ? HorizontalAlignment.Right : HorizontalAlignment.Left;
            Grid.SetColumn(label, column); columns.Children.Add(label);
        }
        heading.Children.Add(columns);
        body.Children.Add(heading);
        _list = new ListView { SelectionMode = ListViewSelectionMode.Single,
            HorizontalContentAlignment = HorizontalAlignment.Stretch,
            ItemContainerStyle = (Style)XamlReader.Load("""
                <Style xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" TargetType="ListViewItem">
                  <Setter Property="Padding" Value="12,0"/><Setter Property="MinHeight" Value="48"/>
                  <Setter Property="HorizontalContentAlignment" Value="Stretch"/>
                </Style>
                """),
            ItemTemplate = (DataTemplate)XamlReader.Load("""
                <DataTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">
                  <Grid ColumnSpacing="12" Padding="0,10" MinHeight="48">
                    <Grid.ColumnDefinitions><ColumnDefinition Width="24"/><ColumnDefinition Width="*"/><ColumnDefinition Width="92"/><ColumnDefinition Width="64"/></Grid.ColumnDefinitions>
                    <Image Source="{Binding Icon}" Width="24" Height="24" Stretch="Uniform" VerticalAlignment="Center" />
                    <TextBlock Grid.Column="1" Text="{Binding Name}" ToolTipService.ToolTip="{Binding Name}" FontSize="14" TextTrimming="CharacterEllipsis" VerticalAlignment="Center" />
                    <TextBlock Grid.Column="2" Text="{Binding CategoryName}" ToolTipService.ToolTip="{Binding CategoryName}" FontSize="14" Foreground="{ThemeResource TextFillColorSecondaryBrush}" TextTrimming="CharacterEllipsis" VerticalAlignment="Center" />
                    <TextBlock Grid.Column="3" Text="{Binding Id}" FontSize="14" Foreground="{ThemeResource TextFillColorSecondaryBrush}" HorizontalAlignment="Right" VerticalAlignment="Center" />
                  </Grid>
                </DataTemplate>
                """) };
        AutomationProperties.SetName(_list, UiLanguage.Text("BuildingList_Open"));
        AutomationProperties.SetAutomationId(_list, "BuildingListResults");
        _list.Margin = new Thickness(16, 12, 16, 16);
        Grid.SetRow(_list, 1); body.Children.Add(_list);
        var detailScroll = new ScrollViewer { Content = _details, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
            HorizontalContentAlignment = HorizontalAlignment.Stretch, Padding = new Thickness(24) };
        var detailBorder = (Border)XamlReader.Load("""
            <Border xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" BorderThickness="1,0,0,0" BorderBrush="{ThemeResource DividerStrokeColorDefaultBrush}" />
            """);
        detailBorder.Child = detailScroll;
        Grid.SetColumn(detailBorder, 1); Grid.SetRowSpan(detailBorder, 2); body.Children.Add(detailBorder);
        _surface = (Border)XamlReader.Load("""
            <Border xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Background="{ThemeResource LayerFillColorDefaultBrush}" BorderBrush="{ThemeResource DividerStrokeColorDefaultBrush}" BorderThickness="1" CornerRadius="8" />
            """);
        _surface.Child = body; Grid.SetRow(_surface, 1); Children.Add(_surface);
        Grid.SetRow(_loading, 1); Children.Add(_loading);
        SetLoading(false);
        bool? stacked = null;
        SizeChanged += (_, _) =>
        {
            var narrow = ActualWidth < 760;
            if (stacked == narrow) return;
            stacked = narrow;
            body.ColumnDefinitions[1].Width = narrow ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
            body.RowDefinitions[2].Height = narrow ? new GridLength(1, GridUnitType.Star) : new GridLength(0);
            Grid.SetColumn(detailBorder, narrow ? 0 : 1); Grid.SetRow(detailBorder, narrow ? 2 : 0);
            Grid.SetRowSpan(detailBorder, narrow ? 1 : 2);
            detailBorder.BorderThickness = narrow ? new Thickness(0, 1, 0, 0) : new Thickness(1, 0, 0, 0);
        };
        ClearDetails();
        UpdateCatalog(catalog);
        _search.TextChanged += (_, _) => Filter();
        _sort.SelectionChanged += (_, _) => Filter();
        _categories.SelectionChanged += (_, _) => { if (_updating) return; Filter(); CategoryChanged?.Invoke(Category); };
        _gameNames.Checked += (_, _) => { if (!_updating) GameNamesChanged?.Invoke(true); };
        _gameNames.Unchecked += (_, _) => { if (!_updating) GameNamesChanged?.Invoke(false); };
        _list.SelectionChanged += (_, _) =>
        {
            if (_updating) return;
            _selected = (_list.SelectedItem as BuildingListItem)?.Id;
            Selected?.Invoke(_selected);
            UpdateSelection();
        };
        _clear.Click += (_, _) => { _search.Text = ""; _categories.SelectedIndex = 0; Filter(); };
    }

    internal void UpdateCatalog(JsonElement catalog)
    {
        var category = Category;
        _memberNames = catalog.GetProperty("items").EnumerateArray().SelectMany(row => row.GetProperty("memberNames").EnumerateObject())
            .ToDictionary(name => int.Parse(name.Name, CultureInfo.InvariantCulture), name => name.Value.GetString()!);
        _items = catalog.GetProperty("items").EnumerateArray().Select(row => new BuildingListItem(
            row.GetProperty("id").GetInt32(), DisplayName(row.GetProperty("name").GetString()!, row.GetProperty("id").GetInt32()),
            row.GetProperty("nameSource").GetString()!,
            row.GetProperty("category").GetString()!, row.GetProperty("categoryName").GetString()!,
            row.GetProperty("memberIds").EnumerateArray().Select(id => id.GetInt32()).ToArray(),
            row.GetProperty("iconPath").GetString()!))
            .OrderBy(item => item.Name, StringComparer.CurrentCultureIgnoreCase).ThenBy(item => item.Id).ToList();
        _byMember = _items.SelectMany(item => item.MemberIds.Select(id => (id, item))).ToDictionary(pair => pair.id, pair => pair.item);
        UseGameNames = catalog.GetProperty("useGameNames").GetBoolean();
        _updating = true;
        try
        {
            _gameNames.IsChecked = UseGameNames;
            _categories.Items.Clear();
            _categories.Items.Add(new ComboBoxItem { Content = $"{UiLanguage.Text("BuildingList_All")} ({_items.Count:N0})", Tag = "" });
            foreach (var group in _items.GroupBy(item => item.Category).OrderBy(group => group.First().CategoryName, StringComparer.CurrentCulture))
                _categories.Items.Add(new ComboBoxItem { Content = $"{group.First().CategoryName} ({group.Count():N0})", Tag = group.Key });
            _categories.SelectedItem = _categories.Items.OfType<ComboBoxItem>().First(item => (string)item.Tag == category);
        }
        finally { _updating = false; }
        Filter();
        if (_detailModel is { } model && _detailSelection is { } selection) ShowDetails(model, selection);
    }

    internal static string DisplayName(string name, int id) => string.IsNullOrWhiteSpace(name)
        ? $"{UiLanguage.Text("BuildingList_Unnamed")} · {id}" : name;

    internal static StackPanel CreateLoadingIndicator()
    {
        var indicator = new StackPanel { Spacing = 12, HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center };
        indicator.Children.Add(new ProgressRing { IsActive = true, Width = 40, Height = 40,
            HorizontalAlignment = HorizontalAlignment.Center });
        indicator.Children.Add(new TextBlock { Text = UiLanguage.Text("BuildingList_Open") + " · " + UiLanguage.Text("MapTaskState_Status_D04FCBDA73"),
            TextAlignment = TextAlignment.Center, TextWrapping = TextWrapping.Wrap });
        AutomationProperties.SetAutomationId(indicator, "BuildingListLoading");
        return indicator;
    }

    internal void SetLoading(bool loading)
    {
        _loading.Visibility = loading ? Visibility.Visible : Visibility.Collapsed;
        ((ProgressRing)_loading.Children[0]).IsActive = loading;
        _surface.Visibility = loading ? Visibility.Collapsed : Visibility.Visible;
    }

    internal void SynchronizeGameNames()
    {
        _updating = true;
        try { _gameNames.IsChecked = UseGameNames; }
        finally { _updating = false; }
    }

    internal void SetAvailable(bool enabled)
    {
        _search.IsEnabled = _categories.IsEnabled = _sort.IsEnabled = _list.IsEnabled = _clear.IsEnabled = _gameNames.IsEnabled = enabled;
    }

    private static string Normalize(string text) => text.Normalize(NormalizationForm.FormKC).ToUpper(CultureInfo.CurrentCulture);

    private void Filter()
    {
        var query = Normalize(_search.Text.Trim());
        var category = Category;
        var matches = _items.Where(item => (category.Length == 0 || item.Category == category)
            && Normalize(item.Name).Contains(query, StringComparison.Ordinal));
        var ordered = ((_sort.SelectedItem as ComboBoxItem)?.Tag as string) switch
        {
            "NameDesc" => matches.OrderByDescending(item => item.Name, StringComparer.CurrentCultureIgnoreCase).ThenBy(item => item.Id),
            "CategoryAsc" => matches.OrderBy(item => item.CategoryName, StringComparer.CurrentCulture).ThenBy(item => item.Name, StringComparer.CurrentCultureIgnoreCase).ThenBy(item => item.Id),
            "CategoryDesc" => matches.OrderByDescending(item => item.CategoryName, StringComparer.CurrentCulture).ThenBy(item => item.Name, StringComparer.CurrentCultureIgnoreCase).ThenBy(item => item.Id),
            "IdAsc" => matches.OrderBy(item => item.Id),
            "IdDesc" => matches.OrderByDescending(item => item.Id),
            _ => matches.OrderBy(item => item.Name, StringComparer.CurrentCultureIgnoreCase).ThenBy(item => item.Id),
        };
        var filtered = ordered.ToList();
        _updating = true;
        try { _list.ItemsSource = filtered; }
        finally { _updating = false; }
        _count.Text = filtered.Count == 0 ? UiLanguage.Text("BuildingList_Empty")
            : string.Format(CultureInfo.CurrentCulture, UiLanguage.Text("BuildingList_Count"), filtered.Count, _items.Count);
        UpdateSelection();
    }

    internal void SelectBuilding(int? id)
    {
        _selected = id is { } value && _byMember.TryGetValue(value, out var item) ? item.Id : null;
        UpdateSelection();
    }

    private void UpdateSelection()
    {
        var visible = ((IEnumerable<BuildingListItem>)_list.ItemsSource).FirstOrDefault(item => item.Id == _selected);
        _updating = true;
        try { _list.SelectedItem = visible; if (visible is not null) _list.ScrollIntoView(visible); }
        finally { _updating = false; }
        _notice.Visibility = _clear.Visibility = _selected.HasValue && visible is null ? Visibility.Visible : Visibility.Collapsed;
    }

    internal void ClearDetails()
    {
        _detailModel = _detailSelection = null;
        _details.Children.Clear();
        _details.Children.Add(new TextBlock { Text = UiLanguage.Text("BuildingList_Details"), FontSize = 20,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        _details.Children.Add(new TextBlock { Text = UiLanguage.Text("BuildingList_DetailHint"), TextWrapping = TextWrapping.Wrap });
    }

    internal void ShowDetails(JsonElement model, JsonElement selection)
    {
        ClearDetails();
        if (model.GetProperty("objectType").GetString() != "building") return;
        _detailModel = model.Clone(); _detailSelection = selection.Clone();
        var props = selection.GetProperty("properties");
        var id = props.GetProperty("buildingId").GetInt32();
        var item = _byMember[id];
        _details.Children.Clear();
        var header = new Grid { ColumnSpacing = 16, Margin = new Thickness(0, 0, 0, 8) };
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        if (item.Icon is { } icon)
            header.Children.Add(new Image { Source = icon, Width = 40, Height = 40, VerticalAlignment = VerticalAlignment.Center });
        var title = new StackPanel { Spacing = 4 };
        title.Children.Add(new TextBlock { Text = item.Name, FontSize = 20, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true });
        var subtitle = SecondaryText($"{item.CategoryName} · ID {id}"); subtitle.IsTextSelectionEnabled = true;
        title.Children.Add(subtitle);
        Grid.SetColumn(title, 1); header.Children.Add(title); _details.Children.Add(header);
        var locate = new Button { Content = UiLanguage.Text("ObjectInspectorPanel_Render_EEF4D23EB3"),
            Style = (Style)Application.Current.Resources["AccentButtonStyle"], Margin = new Thickness(0, 0, 0, 12) };
        locate.Click += (_, _) => Selected?.Invoke(id);
        _details.Children.Add(locate);
        _details.Children.Add(new TextBlock { Text = UiLanguage.Text("BuildingList_Info"), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        var facts = new List<(string, string)> { (UiLanguage.Text("BuildingList_NameSource"), UiLanguage.Text(item.NameSource switch
            { "custom" => "BuildingList_CustomName", "category" => "BuildingList_OfficialName", "empty" => "BuildingList_NoName", _ => "BuildingList_GameName" })) };
        var official = props.GetProperty("categoryName").GetString();
        if (!string.IsNullOrWhiteSpace(official)) facts.Add((UiLanguage.Text("BuildingList_GameCategory"), official));
        if (props.GetProperty("height_m") is { ValueKind: JsonValueKind.Number } height)
            facts.Add((UiLanguage.Text("BuildingList_Height"), height.GetDouble().ToString("N1", CultureInfo.CurrentCulture) + " m"));
        facts.Add((UiLanguage.Text("BuildingList_Group"), string.Format(CultureInfo.CurrentCulture, UiLanguage.Text("BuildingList_GroupCount"), item.MemberIds.Length)));
        _details.Children.Add(Facts(facts));
        if (selection.TryGetProperty("members", out var members) && members.GetArrayLength() > 0)
        {
            var children = new StackPanel { Spacing = 8 };
            foreach (var member in members.EnumerateArray())
                children.Children.Add(new TextBlock { Text = DisplayName(_memberNames[member.GetProperty("id").GetInt32()], member.GetProperty("id").GetInt32())
                    + $" · ID {member.GetProperty("id").GetInt32()}\n" + member.GetProperty("categoryName").GetString(), TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true });
            _details.Children.Add(new Expander { Header = UiLanguage.Text("BuildingList_Members"), Content = children, IsExpanded = true,
                HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch });
        }
        var technical = model.GetProperty("summary").EnumerateArray().Concat(model.GetProperty("details").EnumerateArray())
            .Concat(model.GetProperty("technical").EnumerateArray()).Select(fact => (fact.GetProperty("label").GetString()!, fact.GetProperty("value").GetString()!));
        _details.Children.Add(new Expander { Header = UiLanguage.Text("ObjectInspectorPanel_Render_2CCACDED44"), Content = Facts(technical),
            HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch });
    }

    private static TextBlock SecondaryText(string value, double size = 14)
    {
        var text = (TextBlock)XamlReader.Load("""
            <TextBlock xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Foreground="{ThemeResource TextFillColorSecondaryBrush}" />
            """);
        text.Text = value; text.FontSize = size; return text;
    }

    private static Grid Facts(IEnumerable<(string Label, string Value)> facts)
    {
        var grid = new Grid { RowSpacing = 12, ColumnSpacing = 16 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(0.8, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.2, GridUnitType.Star) });
        foreach (var (label, value) in facts)
        {
            var row = grid.RowDefinitions.Count; grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            var key = SecondaryText(label); key.TextWrapping = TextWrapping.Wrap;
            var text = new TextBlock { Text = value, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
            Grid.SetRow(key, row); Grid.SetRow(text, row); Grid.SetColumn(text, 1); grid.Children.Add(key); grid.Children.Add(text);
        }
        return grid;
    }
}
