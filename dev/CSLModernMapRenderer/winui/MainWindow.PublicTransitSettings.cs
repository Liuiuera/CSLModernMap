// 处理公共交通设置的界面交互。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Input;
using Windows.Globalization.NumberFormatting;
using Windows.System;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private string _publicTransitSettingsPage = "lines";
    private Action? _refreshPublicTransitSettings;
    private Action<string>? _selectPublicTransitPage;

    private void ShowPublicTransitSettings(JsonElement schema)
    {
        var page = new StackPanel { Spacing = 12 };
        SettingsPanel.Children.Add(page);
        SettingsHeader.Visibility = Visibility.Visible;
        OverviewButton.Visibility = Visibility.Collapsed;
        var generation = _documentGeneration;
        bool Current() => !_closing && generation == _documentGeneration && _context == "transit"
            && SettingsPanel.Children.Contains(page);
        var syncing = false;
        var controls = new List<(Control Control, Action Read, Func<bool> Enabled)>();
        var sections = new Dictionary<string, StackPanel>();
        var tabs = new Dictionary<string, ToggleButton>();
        Task Write(string key, object value) => _engine.RequestAsync("setTransitOption", new { metro = false, key, value });

        void Bind(Control control, Action read, Func<bool>? enabled = null)
        {
            controls.Add((control, read, enabled ?? (() => true)));
            read();
        }
        TextBlock Help(string text)
        {
            var copy = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, FontSize = 12 };
            ExportDesign.Secondary(copy); return copy;
        }
        StackPanel Section(StackPanel parent, string title)
        {
            var body = new StackPanel { Spacing = 6 };
            body.Children.Add(new TextBlock { Text = title, FontSize = 14,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
            parent.Children.Add(body); return body;
        }
        void Pair(StackPanel parent, FrameworkElement left, FrameworkElement right)
        {
            var row = new Grid { ColumnSpacing = 8 };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.Children.Add(left); Grid.SetColumn(right, 1); row.Children.Add(right); parent.Children.Add(row);
        }
        CheckBox Check(string key, string label, string? child = null)
        {
            var box = new CheckBox { Content = new TextBlock { Text = label, TextWrapping = TextWrapping.Wrap },
                MinWidth = 0, HorizontalAlignment = HorizontalAlignment.Stretch };
            Bind(box, () => box.IsChecked = ReadContextToggle(false, key, child));
            box.Checked += Changed; box.Unchecked += Changed;
            async void Changed(object sender, RoutedEventArgs args)
            {
                if (syncing || !Current()) return;
                var value = box.IsChecked == true;
                await QueueContextWriteAsync(false, child is null ? () => Write(key, value)
                    : () => WriteContextChildAsync(false, key, child, value), Current);
            }
            return box;
        }
        FrameworkElement Number(string key, string label, Func<bool>? enabled = null,
            bool slider = false, double multiplier = 1, double step = 1)
        {
            var descriptor = schema.GetProperty(key);
            var minimum = descriptor.GetProperty("low").GetDouble() * multiplier;
            var maximum = descriptor.GetProperty("high").GetDouble() * multiplier;
            var validationMessage = string.Format(UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_0BB1EFDA65"),
                label, minimum, maximum);
            var input = new NumberBox { Minimum = minimum, Maximum = maximum, SmallChange = step,
                MinWidth = 0, HorizontalAlignment = HorizontalAlignment.Stretch,
                ValidationMode = NumberBoxValidationMode.Disabled,
                SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
            AutomationProperties.SetName(input, label);
            var saved = ContextOptions(false).GetProperty(key).GetDouble() * multiplier;
            Slider? range = slider ? new Slider { Minimum = minimum, Maximum = maximum,
                StepFrequency = step, VerticalAlignment = VerticalAlignment.Center } : null;
            if (range is not null) AutomationProperties.SetName(range, label);
            Bind(input, () =>
            {
                input.Value = saved = ContextOptions(false).GetProperty(key).GetDouble() * multiplier;
                if (range is not null) range.Value = input.Value;
            }, enabled);
            async Task Commit(TextBox? editor = null)
            {
                if (syncing || !Current()) return;
                var amount = editor is null ? input.Value
                    : ((INumberParser)input.NumberFormatter).ParseDouble(editor.Text.Trim()) ?? double.NaN;
                if (!double.IsFinite(amount) || amount < minimum || amount > maximum)
                {
                    OperationError.Severity = InfoBarSeverity.Warning;
                    OperationError.Message = validationMessage;
                    OperationError.IsOpen = true;
                    return;
                }
                if (OperationError.Message == validationMessage) OperationError.IsOpen = false;
                if (amount == saved) return;
                input.Value = saved = amount;
                var value = amount / multiplier;
                await QueueContextWriteAsync(false, () => Write(key, value), Current);
            }
            input.LostFocus += async (_, args) => await Commit(args.OriginalSource as TextBox);
            // NumberBox 在按键抬起时确认回车并标记事件已处理。
            input.AddHandler(UIElement.KeyUpEvent, new KeyEventHandler(async (_, args) =>
            {
                if (args.Key == VirtualKey.Enter) await Commit(args.OriginalSource as TextBox);
            }), handledEventsToo: true);
            if (range is null) { input.Header = label; return input; }
            input.Width = 76;
            Bind(range, () => range.Value = input.Value, enabled);
            range.ValueChanged += (_, args) => { if (!syncing) input.Value = args.NewValue; };
            input.ValueChanged += (_, _) =>
            {
                if (!syncing && double.IsFinite(input.Value) && input.Value >= minimum && input.Value <= maximum)
                    range.Value = input.Value;
            };
            range.PointerCaptureLost += async (_, _) => await Commit();
            range.LostFocus += async (_, _) => await Commit();
            var row = new Grid { ColumnSpacing = 8 };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            row.Children.Add(range); Grid.SetColumn(input, 1); row.Children.Add(input);
            var field = new StackPanel { Spacing = 2 };
            field.Children.Add(new TextBlock { Text = label, TextWrapping = TextWrapping.Wrap });
            field.Children.Add(row); return field;
        }
        ComboBox Choice(string label, (string Value, string Label)[] choices, Func<string> read,
            Func<string, Task> write, Func<bool>? enabled = null)
        {
            var choice = new ComboBox { Header = label.Length == 0 ? null : label, MinWidth = 0,
                HorizontalAlignment = HorizontalAlignment.Stretch };
            foreach (var (value, title) in choices) choice.Items.Add(new ComboBoxItem
            {
                Content = new TextBlock { Text = title, TextWrapping = TextWrapping.Wrap }, Tag = value,
            });
            Bind(choice, () => choice.SelectedItem = choice.Items.Cast<ComboBoxItem>().Single(item => (string)item.Tag == read()), enabled);
            choice.SelectionChanged += async (_, _) =>
            {
                if (syncing || !Current() || choice.SelectedItem is not ComboBoxItem item || (string)item.Tag == read()) return;
                var value = (string)item.Tag;
                await QueueContextWriteAsync(false, () => write(value), Current);
            };
            return choice;
        }

        var heading = new Grid { ColumnSpacing = 8 };
        heading.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        heading.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        heading.Children.Add(new TextBlock { Text = UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_1AB216CC0D"), FontSize = 18,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center,
            TextTrimming = TextTrimming.CharacterEllipsis });
        var overview = new Button { Content = UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_4D805272C6"), MinWidth = 0, Padding = new Thickness(8, 4, 8, 4) };
        overview.Click += (_, _) => ShowMapOverview(); Grid.SetColumn(overview, 1); heading.Children.Add(overview);
        SettingsHeader.Children.Add(heading);
        var navigation = new Grid { ColumnSpacing = 4 };
        SettingsHeader.Children.Add(navigation);
        foreach (var (key, title) in new[] { ("lines", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_223B24B821")), ("stations", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_A59FE62777")), ("base", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_4E162D443D")) })
        {
            navigation.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var tab = new ToggleButton { Content = title, MinWidth = 0,
                HorizontalAlignment = HorizontalAlignment.Stretch };
            Grid.SetColumn(tab, tabs.Count); navigation.Children.Add(tab); tabs[key] = tab;
            var section = new StackPanel { Spacing = 14 }; sections[key] = section; page.Children.Add(section);
            tab.Click += (_, _) => SelectPage(key);
        }
        void SelectPage(string key)
        {
            _publicTransitSettingsPage = key;
            foreach (var (name, section) in sections) section.Visibility = name == key ? Visibility.Visible : Visibility.Collapsed;
            foreach (var (name, tab) in tabs) tab.IsChecked = name == key;
            SettingsScroll.ChangeView(null, 0, null);
        }
        _selectPublicTransitPage = SelectPage;

        var lines = sections["lines"];
        var routes = Section(lines, UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_9B5EF0621D"));
        Pair(routes, Check("layers", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_223B24B821"), "lines"), Check("line_label_enabled", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_2035432E8A")));
        routes.Children.Add(Number("line_width", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_9127985776"), () => ReadContextToggle(false, "layers", "lines"), slider: true, step: .5));
        routes.Children.Add(Choice(UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_8FB38E6289"), new[] { ("game", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_F6FDD989E6")), ("classic", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_AE7AD41B8A")), ("soft", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_B26912E11B")) },
            () => ContextOptions(false).GetProperty("line_color_mode").GetString()!, value => Write("line_color_mode", value)));
        var lineNames = Section(lines, UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_2035432E8A"));
        Pair(lineNames, Number("line_label_size", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_E8A2BF34A8"), () => ReadContextToggle(false, "line_label_enabled")),
            Number("line_label_interval", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_07C030A101"), () => ReadContextToggle(false, "line_label_enabled"), step: 100));
        var types = new StackPanel { Spacing = 6 };
        var choose = new Button { Content = UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_B0A8E4535B"), HorizontalAlignment = HorizontalAlignment.Stretch };
        choose.Click += async (_, _) => await RunAsync(ShowTransitLinesAsync);
        types.Children.Add(choose); AddLineSelectionSummary(types, false);
        Bind(choose, () => { });
        AddTransitTypes(types, false, Current);
        lines.Children.Add(WorkspaceSection.Create(UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_0C42752747"), types, expanded: true));

        var stations = sections["stations"];
        var symbols = Section(stations, UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_34DDE01FDF"));
        Pair(symbols, Check("facilities_enabled", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_4C2772DFEA")), Check("stations", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_53E88DD68F")));
        symbols.Children.Add(Help(UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_7D37BDEA69")));
        Pair(symbols, Number("facility_icon_size", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_7AEB540769"), () => ReadContextToggle(false, "facilities_enabled")),
            Number("station_size", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_CF587BF248"), () => ReadContextToggle(false, "stations")));
        var names = Section(stations, UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_CF2382056B"));
        var placement = Choice("", new[] { ("hidden", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_145219E726")), ("station", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_66E80E45EF")), ("icon", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_0A6C1B2384")) },
            () => ContextOptions(false).GetProperty("station_name_placement").GetString()!, value => Write("station_name_placement", value));
        AutomationProperties.SetName(placement, UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_CF2382056B")); names.Children.Add(placement);
        names.Children.Add(Number("station_name_size", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_C31627B8E1"),
            () => ContextOptions(false).GetProperty("station_name_placement").GetString() != "hidden"));
        names.Children.Add(Help(UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_4BA9B35503")));
        names.Children.Add(Help(UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_528766B879")));
        var merge = new StackPanel { Spacing = 6 };
        merge.Children.Add(Check("merge_enabled", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_C5F015E8F7")));
        merge.Children.Add(Number("merge_range", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_8CC740C6CC"), () => ReadContextToggle(false, "merge_enabled")));
        merge.Children.Add(Help(UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_AB6B7CE9E1")));
        stations.Children.Add(WorkspaceSection.Create(UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_DC263CCCA0"), merge, expanded: false));
        var density = new StackPanel { Spacing = 6 };
        density.Children.Add(Choice(UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_306B1272E7"), new[] { ("all", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_12CA9D6069")), ("auto", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_4BD5559194")) },
            () => ContextOptions(false).GetProperty("facility_density").GetString()!, value => Write("facility_density", value),
            () => ReadContextToggle(false, "facilities_enabled")));
        density.Children.Add(Number("facility_min_zoom", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_4E94217596"), () => ReadContextToggle(false, "facilities_enabled")));
        density.Children.Add(Help(UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_F5748AF1D4")));
        stations.Children.Add(WorkspaceSection.Create(UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_1C194CAECF"), density, expanded: false));
        var appearance = new Button { Content = new TextBlock { Text = UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_3D44BCC3EC"), TextWrapping = TextWrapping.Wrap },
            HorizontalAlignment = HorizontalAlignment.Stretch };
        appearance.Click += async (_, _) => await RunAsync(ShowMapPoiSettingsAsync);
        Bind(appearance, () => { }, () => ContextOptions(false).GetProperty("station_name_placement").GetString() == "icon");
        stations.Children.Add(appearance);

        var basis = sections["base"];
        var background = Section(basis, UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_607FF5AA08"));
        background.Children.Add(Check("base_road", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_A44C7BA211")));
        var places = Section(basis, UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_2E934D4999"));
        places.Children.Add(Choice(UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_9FC7D0CF1D"), new[] { ("hidden", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_145219E726")), ("dim", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_61F8F663B0")), ("visible", UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_55F4EFF3C2")) },
            () => ContextOptions(false).GetProperty("ordinary_pois").GetString()!, value => Write("ordinary_pois", value)));
        places.Children.Add(Help(UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_A0B055E1E7")));
        var poiSettings = new Button { Content = UiLanguage.Text("MainWindow_PublicTransitSettings_ShowPublicTransitSettings_6A58CC1963"), HorizontalAlignment = HorizontalAlignment.Stretch };
        poiSettings.Click += async (_, _) => await RunAsync(ShowMapPoiSettingsAsync); places.Children.Add(poiSettings);

        _refreshPublicTransitSettings = () =>
        {
            if (!Current()) return;
            syncing = true;
            try
            {
                foreach (var (control, read, enabled) in controls)
                {
                    control.IsEnabled = _documentEnabled && enabled();
                    if (!HasFocusedField(control)) read();
                }
            }
            finally { syncing = false; }
        };
        _refreshPublicTransitSettings(); SelectPage(_publicTransitSettingsPage);
    }

    private async Task ShowTransitStationSettingsAsync()
    {
        await ShowTransitSettingsAsync(); _selectPublicTransitPage?.Invoke("stations");
    }
}
