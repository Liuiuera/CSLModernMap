// 管理主窗口的地图总览显示。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private StackPanel? _overview, _inspectorHome;
    private ComboBox? _overviewTheme;
    private readonly Dictionary<string, TextBlock> _overviewStats = new();
    private bool _updatingOverview;

    private Button OverviewCommand(string id, string label, Func<Task>? run = null, string? iconId = null)
    {
        var button = new Button { Content = label, HorizontalAlignment = HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Left };
        if (iconId is not null)
        {
            button.Content = new StackPanel
            {
                Orientation = Orientation.Horizontal, Spacing = 8,
                Children =
                {
                    new Viewbox { Width = 16, Height = 16, Child = RibbonIcons.Create(iconId) },
                    new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center },
                },
            };
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(button, label);
        }
        button.Click += async (_, _) => await ExecuteCommandAsync(id);
        if (run is not null) RegisterCommand(id, run, true, button);
        else _commands[id].Controls.Add(button);
        return button;
    }

    private static StackPanel OverviewSection(StackPanel parent, string title, bool emphasizeHeader = false)
    {
        var content = new StackPanel { Spacing = 10 };
        parent.Children.Add(WorkspaceSection.Create(title, content, emphasizeHeader: emphasizeHeader));
        return content;
    }

    private void ShowMapOverview()
    {
        ClearLayerPanel(SettingsPanel);
        if (_overview is null)
        {
            _overview = new StackPanel { Spacing = 16 };
            _overview.Children.Add(new TextBlock { Text = UiLanguage.Text("MainWindow_Overview_ShowMapOverview_41AB2E0ECB"), FontSize = 24, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
            _overview.Children.Add(new TextBlock { Text = UiLanguage.Text("MainWindow_Overview_ShowMapOverview_24300AC2FE"), TextWrapping = TextWrapping.Wrap });
            var map = OverviewSection(_overview, UiLanguage.Text("MainWindow_Overview_ShowMapOverview_41AB2E0ECB"));
            _overviewTheme = new ComboBox { Header = UiLanguage.Text("MainWindow_Overview_ShowMapOverview_788DB1CFEC"), HorizontalAlignment = HorizontalAlignment.Stretch };
            _overviewTheme.SelectionChanged += async (_, _) =>
            {
                if (!_updatingOverview && _overviewTheme.SelectedItem is ComboBoxItem item)
                    await SafeRequestAsync("setTheme", new { theme = (string)item.Tag });
            };
            map.Children.Add(_overviewTheme);
            map.Children.Add(OverviewCommand("style.edit_theme", UiLanguage.Text("MainWindow_Overview_ShowMapOverview_6F1C3CB04A")));
            var stats = OverviewSection(_overview, UiLanguage.Text("MainWindow_Overview_ShowMapOverview_DCC3E7A6FA"));
            foreach (var (key, label, glyph) in new[] { ("name", UiLanguage.Text("MainWindow_Overview_ShowMapOverview_ED84A1665F"), "\uEC06"), ("buildings", UiLanguage.Text("MainWindow_Overview_ShowMapOverview_5B46860A8E"), "\uE80F"), ("road_length", UiLanguage.Text("MainWindow_Overview_ShowMapOverview_BDA7B29CF3"), "\uE816"),
                ("transit_length", UiLanguage.Text("MainWindow_Overview_ShowMapOverview_B553EA09A3"), "\uE806"), ("built_area", UiLanguage.Text("MainWindow_Overview_ShowMapOverview_66ED35BB59"), "\uE822"),
                ("districts", UiLanguage.Text("MainWindow_Overview_ShowMapOverview_BE41515DE2"), "\uE81E"), ("regions", UiLanguage.Text("MainWindow_Overview_ShowMapOverview_3595B7C819"), "\uE707") })
            {
                var row = new Grid { ColumnSpacing = 8 };
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                var icon = new FontIcon { Glyph = glyph, FontFamily = new FontFamily("Segoe MDL2 Assets"),
                    FontSize = 16, Width = 20, VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(0, 2, 0, 0) };
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetAccessibilityView(icon,
                    Microsoft.UI.Xaml.Automation.Peers.AccessibilityView.Raw);
                row.Children.Add(icon);
                var value = new TextBlock { Text = label + "：—", TextWrapping = TextWrapping.Wrap,
                    VerticalAlignment = VerticalAlignment.Center };
                Grid.SetColumn(value, 1); row.Children.Add(value); stats.Children.Add(row);
                _overviewStats[key] = value; value.Tag = label;
            }
        }
        SettingsPanel.Children.Add(_overview);
        RefreshMapOverview();
        UpdateCommandStates();
    }

    private void RefreshMapOverview()
    {
        if (_overview is null || _snapshot.ValueKind != JsonValueKind.Object) return;
        if (HasFocusedField(_overview)) return;
        _updatingOverview = true;
        try
        {
            if (_overviewTheme!.Items.Count == 0)
                foreach (var theme in _snapshot.GetProperty("themes").EnumerateArray())
                    _overviewTheme.Items.Add(new ComboBoxItem { Content = theme.GetProperty("name").GetString(), Tag = theme.GetProperty("id").GetString() });
            foreach (ComboBoxItem item in _overviewTheme.Items)
                if ((string)item.Tag == _snapshot.GetProperty("theme").GetString()) _overviewTheme.SelectedItem = item;
            var info = _snapshot.GetProperty("cityInfo");
            // 六项统计由引擎算好下发；未装城市时不编数字。
            var stats = _snapshot.TryGetProperty("stats", out var node) ? node : default;
            var ready = stats.ValueKind == JsonValueKind.Object
                        && stats.TryGetProperty("available", out var flag) && flag.GetBoolean();
            foreach (var (key, control) in _overviewStats)
            {
                var text = "—";
                if (key == "name")
                    text = info.TryGetProperty("name", out var city) && city.GetString() is { Length: > 0 } name ? name : "—";
                else if (ready) text = key switch
                {
                    "buildings" => stats.GetProperty("buildings").GetInt32().ToString("N0"),
                    "road_length" => (stats.GetProperty("roadMeters").GetDouble() / 1000).ToString("0.0") + " km",
                    "transit_length" => (stats.GetProperty("transitMeters").GetDouble() / 1000).ToString("0.0") + " km",
                    "built_area" => (stats.GetProperty("builtArea").GetDouble() / 1_000_000).ToString("0.00") + " km²",
                    "districts" => stats.GetProperty("districts").GetInt32().ToString("N0"),
                    "regions" => stats.GetProperty("regions").GetInt32().ToString("N0"),
                    _ => "—",
                };
                control.Text = control.Tag + "：" + text;
            }
        }
        finally { _updatingOverview = false; }
    }

    private void ShowInspectorHome()
    {
        _inspectorRequest++;
        _inspectorSelection = null;
        _objectInspector = null;
        ClearLayerPanel(InspectorPanel);
        if (_inspectorHome is null)
        {
            _inspectorHome = new StackPanel { Spacing = 12 };
            _inspectorHome.Children.Add(new TextBlock { Text = UiLanguage.Text("MainWindow_Overview_ShowInspectorHome_1CDED5B3FD"), FontSize = 22,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
            _inspectorHome.Children.Add(new TextBlock
            {
                Text = UiLanguage.Text("MainWindow_Overview_ShowInspectorHome_F3945D0F03"),
                TextWrapping = TextWrapping.Wrap,
            });
            var explore = OverviewSection(_inspectorHome, UiLanguage.Text("MainWindow_Overview_ShowInspectorHome_98CEB57FD8"));
            explore.Children.Add(OverviewCommand("view.transit", UiLanguage.Text("MainWindow_Overview_ShowInspectorHome_5A85707DE5"), iconId: "view.transit"));
            explore.Children.Add(OverviewCommand("view.metro", UiLanguage.Text("MainWindow_Overview_ShowInspectorHome_FA5D23884E"), iconId: "view.metro"));
            explore.Children.Add(OverviewCommand("map.layer_settings", UiLanguage.Text("MainWindow_Overview_ShowInspectorHome_F2926EB3BB"), () =>
            {
                Ribbon.SelectedTab = _layersTab;
                return Task.CompletedTask;
            }, iconId: "layer.built_area"));
            explore.Children.Add(OverviewCommand("style.edit_theme", UiLanguage.Text("MainWindow_Overview_ShowInspectorHome_74F45217E6"), iconId: "style.edit_theme"));
            var output = OverviewSection(_inspectorHome, UiLanguage.Text("MainWindow_Overview_ShowInspectorHome_FB04ADDB4C"), emphasizeHeader: true);
            var export = OverviewCommand("map.export", UiLanguage.Text("MainWindow_Overview_ShowInspectorHome_76420433F2"));
            export.Style = (Style)Application.Current.Resources["AccentButtonStyle"];
            output.Children.Add(export);
        }
        InspectorPanel.Children.Add(_inspectorHome);
        UpdateCommandStates();
    }
}
