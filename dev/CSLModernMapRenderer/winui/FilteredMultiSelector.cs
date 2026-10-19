// 提供支持搜索筛选的多项选择控件。
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Markup;
using Microsoft.UI.Xaml.Media;

namespace CSLModernMapWinUI;

internal sealed record SelectorChoice(string Id, string Title, string Group, string Detail,
    bool Available = true, string Color = "#8A8F99")
{
    public Brush Swatch
    {
        get
        {
            var hex = Color.TrimStart('#');
            if (hex.Length != 6 || !uint.TryParse(hex, System.Globalization.NumberStyles.HexNumber, null, out var rgb))
                rgb = 0x8A8F99;
            return new SolidColorBrush(Windows.UI.Color.FromArgb(255, (byte)(rgb >> 16), (byte)(rgb >> 8), (byte)rgb));
        }
    }
}

// 筛选只改变视图，不修改草稿选择；提交由调用方完成。
internal sealed class FilteredMultiSelector : Grid
{
    private readonly SelectorChoice[] _choices;
    private readonly HashSet<string> _selected;
    private readonly HashSet<string> _defaults;
    private SelectorChoice[] _visible = [];
    private bool _syncing;
    private bool? _wide;
    private readonly StackPanel _tools = new() { Spacing = 14 };
    private readonly ScrollViewer _toolPane = new()
    {
        HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
        VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
        HorizontalContentAlignment = HorizontalAlignment.Stretch,
    };
    private readonly Grid _resultPane = new() { RowSpacing = 12 };
    internal AutoSuggestBox Search { get; } = new() { PlaceholderText = UiLanguage.Text("FilteredMultiSelector_Search_F2BAED7171") };
    internal ComboBox GroupFilter { get; } = new() { Header = UiLanguage.Text("FilteredMultiSelector_GroupFilter_BA40014FF4"), HorizontalAlignment = HorizontalAlignment.Stretch };
    internal ComboBox StateFilter { get; } = new() { Header = UiLanguage.Text("FilteredMultiSelector_StateFilter_49EC97BF6E"), HorizontalAlignment = HorizontalAlignment.Stretch };
    internal ListView Results { get; } = new()
    {
        SelectionMode = ListViewSelectionMode.Multiple, IsMultiSelectCheckBoxEnabled = true,
        HorizontalAlignment = HorizontalAlignment.Stretch,
    };
    private readonly TextBlock _summary = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _empty = new() { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(12) };
    internal Button SelectResults { get; } = new() { Content = UiLanguage.Text("FilteredMultiSelector_SelectResults_F3BF69BF3D") };
    internal Button ClearResults { get; } = new() { Content = UiLanguage.Text("FilteredMultiSelector_ClearResults_E0B5912AA9") };
    internal Button Reset { get; } = new() { Content = UiLanguage.Text("FilteredMultiSelector_Reset_7C815CCCB5") };
    public event Action? SelectionChanged;
    public string[] SelectedIds => _choices.Where(c => _selected.Contains(c.Id)).Select(c => c.Id).ToArray();
    public int SelectedCount => _selected.Count;
    public int VisibleCount => _visible.Length;

    public FilteredMultiSelector(IEnumerable<SelectorChoice> choices, IEnumerable<string> selected,
        IEnumerable<string>? defaults = null)
    {
        _choices = choices.ToArray();
        var known = _choices.Select(c => c.Id).ToHashSet();
        _selected = selected.Where(known.Contains).ToHashSet();
        _defaults = (defaults ?? _choices.Where(c => c.Available).Select(c => c.Id)).Where(known.Contains).ToHashSet();
        RowSpacing = 12; ColumnSpacing = 20;
        AutomationProperties.SetName(Search, UiLanguage.Text("FilteredMultiSelector_View_F2BAED7171"));
        AutomationProperties.SetName(GroupFilter, UiLanguage.Text("FilteredMultiSelector_View_BA40014FF4"));
        AutomationProperties.SetName(StateFilter, UiLanguage.Text("FilteredMultiSelector_View_49EC97BF6E"));
        AutomationProperties.SetName(Results, UiLanguage.Text("FilteredMultiSelector_View_D107C0A063"));
        AutomationProperties.SetLiveSetting(_summary, Microsoft.UI.Xaml.Automation.Peers.AutomationLiveSetting.Polite);
        _tools.Children.Add(Search);
        _tools.Children.Add(GroupFilter); _tools.Children.Add(StateFilter);
        GroupFilter.Items.Add(new ComboBoxItem { Content = UiLanguage.Text("FilteredMultiSelector_View_267C4A7A86"), Tag = "" });
        foreach (var group in _choices.Select(c => c.Group).Distinct().Order(StringComparer.CurrentCultureIgnoreCase))
            GroupFilter.Items.Add(new ComboBoxItem { Content = group, Tag = group });
        foreach (var (label, key) in new[] { (UiLanguage.Text("FilteredMultiSelector_View_E3040DFD6B"), "all"), (UiLanguage.Text("FilteredMultiSelector_View_3F4EBC4AAD"), "selected"), (UiLanguage.Text("FilteredMultiSelector_View_1182C5454F"), "unselected") })
            StateFilter.Items.Add(new ComboBoxItem { Content = label, Tag = key });
        GroupFilter.SelectedIndex = StateFilter.SelectedIndex = 0;
        var actions = new StackPanel { Spacing = 8 };
        foreach (var button in new[] { SelectResults, ClearResults, Reset })
        {
            button.HorizontalAlignment = HorizontalAlignment.Stretch;
            actions.Children.Add(button);
        }
        _tools.Children.Add(actions);
        _toolPane.Content = _tools; Children.Add(_toolPane);
        _resultPane.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _resultPane.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        _resultPane.Children.Add(_summary);
        var list = new Grid(); Grid.SetRow(list, 1); _resultPane.Children.Add(list);
        list.Children.Add(Results); list.Children.Add(_empty);
        Children.Add(_resultPane);
        Loaded += (_, _) => ArrangePanes();
        SizeChanged += (_, _) => ArrangePanes();
        ArrangePanes();
        Results.ItemTemplate = (DataTemplate)XamlReader.Load("""
            <DataTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">
              <Grid ColumnSpacing="10" Padding="0,6" HorizontalAlignment="Stretch">
                <Grid.ColumnDefinitions><ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
                <Border Width="12" Height="12" CornerRadius="6" Background="{Binding Swatch}" VerticalAlignment="Top" Margin="0,5,0,0"/>
                <StackPanel Grid.Column="1" Spacing="3">
                  <TextBlock Text="{Binding Title}" TextWrapping="Wrap"/>
                  <TextBlock Text="{Binding Detail}" TextWrapping="Wrap" Foreground="{ThemeResource TextFillColorSecondaryBrush}" FontSize="12"/>
                </StackPanel>
              </Grid>
            </DataTemplate>
            """);
        Results.ContainerContentChanging += (_, args) =>
        {
            if (args.InRecycleQueue || args.Item is not SelectorChoice choice) return;
            args.ItemContainer.IsEnabled = choice.Available;
            args.ItemContainer.HorizontalContentAlignment = HorizontalAlignment.Stretch;
            AutomationProperties.SetName(args.ItemContainer, choice.Title + ", " + choice.Detail);
        };
        Search.TextChanged += (_, args) => { if (args.Reason == AutoSuggestionBoxTextChangeReason.UserInput) Refilter(); };
        GroupFilter.SelectionChanged += (_, _) => Refilter();
        StateFilter.SelectionChanged += (_, _) => Refilter();
        Results.SelectionChanged += (_, args) =>
        {
            if (_syncing) return;
            foreach (var choice in args.RemovedItems.OfType<SelectorChoice>().Where(c => c.Available)) _selected.Remove(choice.Id);
            foreach (var choice in args.AddedItems.OfType<SelectorChoice>().Where(c => c.Available)) _selected.Add(choice.Id);
            if (((StateFilter.SelectedItem as ComboBoxItem)?.Tag as string) != "all") Refilter();
            else UpdateSummary();
            SelectionChanged?.Invoke();
        };
        SelectResults.Click += (_, _) => SelectVisible(true);
        ClearResults.Click += (_, _) => SelectVisible(false);
        Reset.Click += (_, _) =>
        {
            _selected.Clear(); _selected.UnionWith(_defaults);
            Refilter(); SelectionChanged?.Invoke();
        };
        Refilter();
    }

    private void ArrangePanes()
    {
        var wide = ActualWidth >= 600;
        _toolPane.MaxHeight = wide ? double.PositiveInfinity : Math.Max(100, Math.Min(240, ActualHeight * .45));
        if (_wide == wide) return;
        _wide = wide;
        RowDefinitions.Clear(); ColumnDefinitions.Clear();
        ColumnDefinitions.Add(new ColumnDefinition
            { Width = wide ? new GridLength(230) : new GridLength(1, GridUnitType.Star) });
        if (wide)
        {
            ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        }
        else
        {
            RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        }
        SetColumn(_resultPane, wide ? 1 : 0); SetRow(_resultPane, wide ? 0 : 1);
    }

    internal void Refilter()
    {
        var query = Search.Text.Trim();
        var group = (GroupFilter.SelectedItem as ComboBoxItem)?.Tag as string ?? "";
        var state = (StateFilter.SelectedItem as ComboBoxItem)?.Tag as string ?? "all";
        var terms = query.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
        _visible = _choices.Where(c => (group.Length == 0 || c.Group == group) &&
            (state == "all" || _selected.Contains(c.Id) == (state == "selected")) &&
            terms.All(term => $"{c.Title} {c.Group} {c.Id} {c.Detail}".Contains(term, StringComparison.CurrentCultureIgnoreCase))).ToArray();
        SyncList(); UpdateSummary();
    }

    private void SyncList()
    {
        _syncing = true;
        try
        {
            Results.ItemsSource = _visible;
            foreach (var choice in _visible.Where(c => _selected.Contains(c.Id))) Results.SelectedItems.Add(choice);
        }
        finally { _syncing = false; }
    }

    internal void SelectVisible(bool enabled)
    {
        foreach (var choice in _visible.Where(c => c.Available))
            if (enabled) _selected.Add(choice.Id); else _selected.Remove(choice.Id);
        Refilter(); SelectionChanged?.Invoke();
    }

    private void UpdateSummary()
    {
        var hidden = _selected.Count - _visible.Count(c => _selected.Contains(c.Id));
        _summary.Text = string.Format(UiLanguage.Text("FilteredMultiSelector_UpdateSummary_F4ADCBD976"),
            _visible.Length, _choices.Length, _selected.Count, hidden);
        _empty.Text = _choices.Length == 0 ? UiLanguage.Text("FilteredMultiSelector_UpdateSummary_2A6350DD2E") : UiLanguage.Text("FilteredMultiSelector_UpdateSummary_E9B3A9174B");
        _empty.Visibility = _visible.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
        SelectResults.IsEnabled = ClearResults.IsEnabled = _visible.Any(c => c.Available);
        Reset.IsEnabled = _choices.Length > 0;
    }

    internal ContentDialog CreateDialog(XamlRoot root, string title, int minimumSelection = 0)
    {
        // 有限高度的视口允许列表虚拟化，搜索栏和操作区保持固定。
        Height = Math.Clamp(root.Size.Height - 240, 200, 520);
        Width = Math.Max(0, Math.Min(900, root.Size.Width - 128));
        MinWidth = 0; HorizontalAlignment = HorizontalAlignment.Stretch;
        var dialog = new ContentDialog { XamlRoot = root, Title = title, Content = this,
            PrimaryButtonText = UiLanguage.Text("FilteredMultiSelector_CreateDialog_63C73C4730"), CloseButtonText = UiLanguage.Text("FilteredMultiSelector_CreateDialog_2CD0F3BE87"),
            DefaultButton = ContentDialogButton.Primary };
        dialog.Resources["ContentDialogMaxWidth"] = 1000d;
        if (new Windows.UI.ViewManagement.UISettings().AdvancedEffectsEnabled &&
            !new Windows.UI.ViewManagement.AccessibilitySettings().HighContrast)
            dialog.Resources["ContentDialogBackground"] = XamlReader.Load(
                "<AcrylicBrush xmlns=\"http://schemas.microsoft.com/winfx/2006/xaml/presentation\" TintColor=\"{ThemeResource SystemChromeLowColor}\" TintOpacity=\"0.85\" FallbackColor=\"{ThemeResource SystemChromeLowColor}\" />");
        void Validate() => dialog.IsPrimaryButtonEnabled = SelectedCount >= minimumSelection && _choices.Length > 0;
        SelectionChanged += Validate; Validate();
        dialog.Opened += (_, _) => Search.Focus(FocusState.Programmatic);

        return dialog;
    }
}
