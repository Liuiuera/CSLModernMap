// 处理地图设置界面的状态与操作。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private EditorWindow? _mapPoiSettingsWindow;

    private async Task ShowMapPoiSettingsAsync()
    {
        if (_context == "metro") { await ShowMetroSettingsAsync(); return; }
        if (_mapPoiSettingsWindow is { } existing)
        {
            if (_exportWindow is { } export)
            {
                existing.SetOwner(export.OwnerWindow, modal: true);
                await existing.ShowAsync();
            }
            else existing.Activate();
            return;
        }
        if (!_documentEnabled || !_openingEditors.Add("map-poi")) return;
        try
        {
            var generation = _documentGeneration;
            var context = _context;
            var target = context == "map" ? "normal" : context;
            var epoch = _snapshot.GetProperty("documentEpoch").GetInt32();
            var data = await _engine.RequestAsync("getTextIconSettings", new { target });
            bool Current() => !_closing && generation == _documentGeneration && context == _context;
            if (!Current()) return;
            var title = context == "transit" ? UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_5A85707DE5") : UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_A5E8039E52");
            var window = _mapPoiSettingsWindow = new EditorWindow(
                title + " · " + UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_4F0C1E4BF0"), 960, 740, translucent: true,
                owner: _exportWindow?.OwnerWindow ?? this, modal: _exportWindow is not null)
                { ActionsRightAligned = true };
            var original = JsonNode.Parse(data.GetProperty("fields").GetRawText())!.AsObject();
            var fields = (JsonObject)original.DeepClone();
            var visibility = JsonNode.Parse(data.GetProperty("visibility").GetRawText())!.AsObject();
            var textIconsEnabled = data.GetProperty("enabled").GetBoolean();
            var options = new JsonObject();
            var checks = new List<Action>();
            var editableFields = new HashSet<string>();
            var editableOptions = new HashSet<string>();
            var samples = new List<Action>();
            var pages = new List<(string Key, string Title, StackPanel Body)>();
            var selectedPage = "display";
            PoiProfileEditorPanel? editor = null;
            var status = new TextBlock { TextWrapping = TextWrapping.Wrap };
            var navigation = new ListView { SelectionMode = ListViewSelectionMode.Single };
            var compactNavigation = new ComboBox { HorizontalAlignment = HorizontalAlignment.Stretch,
                PlaceholderText = UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_4D8D19C70F"), Visibility = Visibility.Collapsed, Margin = new Thickness(0, 0, 0, 12) };
            AutomationProperties.SetName(navigation, UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_4D8D19C70F"));
            AutomationProperties.SetName(compactNavigation, UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_4D8D19C70F"));
            var scroll = new ScrollViewer { HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Padding = new Thickness(16, 0, 8, 8) };
            var workspace = new Grid { ColumnSpacing = 12 };
            workspace.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            workspace.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            workspace.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(172) });
            workspace.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            Grid.SetRow(navigation, 1); workspace.Children.Add(navigation);
            Grid.SetColumnSpan(compactNavigation, 2); workspace.Children.Add(compactNavigation);
            Grid.SetRow(scroll, 1); Grid.SetColumn(scroll, 1); workspace.Children.Add(scroll);
            var body = new Grid { RowSpacing = 12 };
            body.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            body.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            body.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            body.Children.Add(Help(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_15D45E8CB2")));
            Grid.SetRow(workspace, 1); body.Children.Add(workspace);
            Grid.SetRow(status, 2); body.Children.Add(status);

            void UpdateSamples() { foreach (var update in samples) update(); }
            JsonNode OptionValue(string key) => options[key] ?? JsonNode.Parse(data.GetProperty("options").GetProperty(key).GetRawText())!;
            FrameworkElement Field(string key, string label, StackPanel? scaling = null)
            {
                editableFields.Add(key);
                return AppearanceControls.Create(_snapshot.GetProperty("fieldSchema").GetProperty(key),
                    _snapshot.GetProperty("fonts"), fields[key]!, value => { fields[key] = value; UpdateSamples(); },
                    checks, label, compact: true, displayMultiplier: key == "poi.name_stroke_ratio" ? 100 : 1,
                    scalingControls: scaling);
            }
            FrameworkElement Option(string key, string label)
            {
                editableOptions.Add(key);
                return AppearanceControls.Create(data.GetProperty("optionSchema").GetProperty(key),
                    _snapshot.GetProperty("fonts"), OptionValue(key), value => { options[key] = value; UpdateSamples(); },
                    checks, label, compact: true);
            }
            CheckBox Check(bool value, Action<bool> changed, string name)
            {
                var check = new CheckBox { IsChecked = value, MinWidth = 0,
                    HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
                AutomationProperties.SetName(check, name);
                check.Checked += (_, _) => changed(true); check.Unchecked += (_, _) => changed(false);
                return check;
            }
            CheckBox FieldCheck(string key, string name)
            {
                editableFields.Add(key);
                return Check(fields[key]!.GetValue<bool>(), value => fields[key] = value, name);
            }
            CheckBox OptionCheck(string key, string name)
            {
                editableOptions.Add(key);
                return Check(OptionValue(key).GetValue<bool>(), value => options[key] = value, name);
            }
            void SelectPage(string key)
            {
                var index = pages.FindIndex(page => page.Key == key);
                if (index < 0) return;
                selectedPage = key;
                scroll.Content = pages[index].Body;
                navigation.SelectedIndex = index; compactNavigation.SelectedIndex = index;
                scroll.ChangeView(null, 0, null);
            }
            navigation.SelectionChanged += (_, _) =>
            {
                if (navigation.SelectedItem is ListViewItem item && selectedPage != (string)item.Tag) SelectPage((string)item.Tag);
            };
            compactNavigation.SelectionChanged += (_, _) =>
            {
                if (compactNavigation.SelectedItem is ComboBoxItem item && selectedPage != (string)item.Tag) SelectPage((string)item.Tag);
            };
            void ArrangeWorkspace()
            {
                var narrow = workspace.ActualWidth < 720;
                workspace.ColumnDefinitions[0].Width = new GridLength(narrow ? 0 : 172);
                workspace.ColumnSpacing = narrow ? 0 : 12;
                navigation.Visibility = narrow ? Visibility.Collapsed : Visibility.Visible;
                compactNavigation.Visibility = narrow ? Visibility.Visible : Visibility.Collapsed;
                scroll.Padding = narrow ? new Thickness(0, 0, 8, 8) : new Thickness(16, 0, 8, 8);
            }
            workspace.SizeChanged += (_, _) => ArrangeWorkspace();

            void AddSample(StackPanel panel, string kind)
            {
                var poi = kind == "poi";
                var words = kind == "road" ? UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_A30C326C3B")
                    : kind == "district" ? UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_A96C5858AE") : kind == "park" ? UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_AE4D0CA688") : UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_C8FB9E0A52");
                var textGrid = new Grid { VerticalAlignment = VerticalAlignment.Center };
                var offsets = new[] { (-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1) };
                var outline = offsets.Select(_ => new TextBlock { Text = words, TextWrapping = TextWrapping.Wrap }).ToArray();
                foreach (var text in outline) textGrid.Children.Add(text);
                var sample = new TextBlock { Text = words, TextWrapping = TextWrapping.Wrap };
                textGrid.Children.Add(sample);
                var glyph = new FontIcon { Glyph = "\uE8F1", FontFamily = new FontFamily("Segoe MDL2 Assets") };
                var icon = new Border { Child = glyph, CornerRadius = new CornerRadius(4) };
                var line = new Grid { ColumnSpacing = 12, Padding = new Thickness(12), MinHeight = 72 };
                line.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                line.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                if (poi) line.Children.Add(icon);
                Grid.SetColumn(textGrid, 1); line.Children.Add(textGrid);
                var example = new StackPanel { Spacing = 4 };
                example.Children.Add(Help(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_A908E2D419"))); example.Children.Add(line);
                panel.Children.Add(ExportDesign.Card(example));
                panel.Children.Add(Help(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_C60A74FB5E")));
                void Update()
                {
                    var font = fields[poi ? "poi.font" : $"label.font.{kind}"]!;
                    var size = ReferenceSize(fields[poi ? "poi.name_size" : $"label.size.{kind}"]!);
                    var textBrush = poi ? new SolidColorBrush(Microsoft.UI.Colors.DodgerBlue)
                        : new SolidColorBrush(AppearanceControls.ParseColor(fields[$"label.color.{kind}"]!.GetValue<string>()));
                    var stroke = poi ? size * fields["poi.name_stroke_ratio"]!.GetValue<double>()
                        : ReferenceSize(fields[$"label.stroke_width.{kind}"]!);
                    var strokeBrush = new SolidColorBrush(AppearanceControls.ParseColor(
                        fields[poi ? "poi.name_stroke" : $"label.stroke_color.{kind}"]!.GetValue<string>()));
                    foreach (var text in outline.Append(sample)) { text.FontFamily = new FontFamily(font.GetValue<string>()); text.FontSize = size; }
                    sample.Foreground = textBrush;
                    for (var index = 0; index < outline.Length; index++)
                    {
                        outline[index].Foreground = strokeBrush;
                        outline[index].Visibility = stroke > 0 ? Visibility.Visible : Visibility.Collapsed;
                        outline[index].RenderTransform = new TranslateTransform { X = offsets[index].Item1 * stroke, Y = offsets[index].Item2 * stroke };
                    }
                    if (poi)
                    {
                        const bool colored = true;
                        icon.Width = icon.Height = ReferenceSize(fields["poi.size"]!);
                        glyph.FontSize = icon.Width * .7;
                        icon.Background = colored ? textBrush : new SolidColorBrush(Microsoft.UI.Colors.Transparent);
                        glyph.Foreground = colored ? new SolidColorBrush(Microsoft.UI.Colors.White) : textBrush;
                    }
                }
                samples.Add(Update);
            }

            void Build(bool defaults)
            {
                var previousPage = selectedPage;
                scroll.Content = null; navigation.Items.Clear(); compactNavigation.Items.Clear();
                pages.Clear(); checks.Clear(); samples.Clear(); editableFields.Clear(); editableOptions.Clear();
                status.Text = "";
                StackPanel Page(string key, string label, string heading, string? description = null)
                {
                    var page = new StackPanel { Spacing = 12 };
                    page.Children.Add(ExportDesign.Text(heading, true));
                    if (description is not null) page.Children.Add(Help(description));
                    pages.Add((key, label, page)); return page;
                }
                var display = Page("display", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_DE90186FC6"), UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_DE90186FC6"));
                ToggleSwitch? master = null;
                if (target == "normal")
                {
                    master = AppearanceControls.Toggle(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_E9439034E1"), textIconsEnabled);
                    master.Toggled += (_, _) => textIconsEnabled = master.IsOn;
                    display.Children.Add(master);
                    display.Children.Add(Help(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_106F7928FF")));
                }
                var table = new Grid { RowSpacing = 8, ColumnSpacing = 8 };
                table.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                table.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(64) });
                table.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(48) });
                table.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(48) });
                table.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
                foreach (var (column, label) in new[] { (0, UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_A15E0EC6D5")), (1, UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_CCF2142845")), (2, UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_D44E9B3D3B")) })
                {
                    var heading = Help(label); Grid.SetColumn(heading, column);
                    if (column != 0) heading.HorizontalAlignment = HorizontalAlignment.Center;
                    table.Children.Add(heading);
                }
                var gates = new List<(CheckBox? Icon, CheckBox? Name, bool Attached)>();
                void Row(string label, string description, CheckBox? icon, CheckBox? name, string? stylePage = null, bool attached = false)
                {
                    var row = table.RowDefinitions.Count;
                    table.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
                    var copy = new StackPanel { Spacing = 2 };
                    copy.Children.Add(ExportDesign.Text(label)); copy.Children.Add(Help(description));
                    Grid.SetRow(copy, row); table.Children.Add(copy);
                    foreach (var (column, toggle) in new[] { (1, icon), (2, name) })
                    {
                        FrameworkElement cell = toggle is null ? new TextBlock { Text = "—", HorizontalAlignment = HorizontalAlignment.Center } : toggle;
                        Grid.SetRow(cell, row); Grid.SetColumn(cell, column); table.Children.Add(cell);
                    }
                    if (stylePage is not null)
                    {
                        var style = new Button { Content = UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_C8FFBDF157"), MinWidth = 0, Padding = new Thickness(4, 6, 4, 6),
                            HorizontalAlignment = HorizontalAlignment.Center };
                        AutomationProperties.SetName(style, label + " · " + UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_C8FFBDF157"));
                        style.Click += (_, _) => SelectPage(stylePage);
                        Grid.SetRow(style, row); Grid.SetColumn(style, 3); table.Children.Add(style);
                    }
                    gates.Add((icon, name, attached));
                }
                if (target == "normal")
                {
                    Row(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_8C919EE4A4"), UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_03646B63FA"), null, FieldCheck("label.roads", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_8C919EE4A4")), "road");
                    Row(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_5939962905"), UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_9343017EBD"), null, FieldCheck("label.districts", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_5939962905")), "district");
                }
                foreach (var (group, label, description) in new[]
                {
                    ("transit", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_F7CCD76BF8"), UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_AAE29EF00E")),
                    ("facility", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_C07A256175"), UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_20F4B27E8B")),
                    ("ordinary", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_85155A34A5"), UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_385A7FE267")),
                    ("area", UiLanguage.Text("Poi_Area"), UiLanguage.Text("Poi_AreaDescription")),
                })
                {
                    if (target != "normal" && group == "area") continue;
                    if (target == "transit" && group == "transit") continue;
                    Row(label, description,
                        Check(visibility["icons"]![group]!.GetValue<bool>(), value => visibility["icons"]![group] = value, label),
                        Check(visibility["names"]![group]!.GetValue<bool>(), value => visibility["names"]![group] = value, label + UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_D44E9B3D3B")), "poi", attached: true);
                }
                if (target != "normal")
                    Row(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_F7CCD76BF8"), UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_AAE29EF00E"), OptionCheck("facilities_enabled", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_4C2772DFEA")),
                        null,
                        target == "transit" ? "poi" : null, attached: true);
                Row(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_BFBFD61DB2"), UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_788576AD8B"),
                    target == "normal" ? FieldCheck("transport.stations", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_53E88DD68F")) : OptionCheck("stations", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_53E88DD68F")),
                    target == "normal" ? FieldCheck("transport.station_names", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_B57E2D89DD")) : null);
                display.Children.Add(table);
                if (target == "transit")
                {
                    editableOptions.Add("station_name_placement");
                    var placement = new ComboBox { Header = UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_CF2382056B"), HorizontalAlignment = HorizontalAlignment.Stretch };
                    foreach (var (value, label) in new[] { ("hidden", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_145219E726")), ("station", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_66E80E45EF")), ("icon", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_0A6C1B2384")) })
                    {
                        var item = new ComboBoxItem { Content = label, Tag = value };
                        placement.Items.Add(item);
                        if (OptionValue("station_name_placement").GetValue<string>() == value) placement.SelectedItem = item;
                    }
                    placement.SelectionChanged += (_, _) =>
                    {
                        if (placement.SelectedItem is ComboBoxItem item) options["station_name_placement"] = (string)item.Tag;
                    };
                    display.Children.Add(placement);
                    display.Children.Add(Option("station_name_size", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_C31627B8E1")));
                    display.Children.Add(Help(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_4BA9B35503")));
                }
                Control? roadSource = null;
                Control? buildingSource = null;
                if (target == "normal")
                {
                    roadSource = (Control)Field("road.game_names", UiLanguage.Text("TextIcons_RoadGameNames"));
                    display.Children.Add(roadSource);
                }
                display.Children.Add(Help(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_76D41E4EC7")));
                buildingSource = (Control)Field("poi.game_names", UiLanguage.Text("TextIcons_BuildingGameNames"));
                display.Children.Add(buildingSource);
                display.Children.Add(Help(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_895C96E443")));
                void ApplyDisplayGates()
                {
                    var enabled = target != "normal" || textIconsEnabled;
                    foreach (var (icon, name, attached) in gates)
                    {
                        if (icon is not null) icon.IsEnabled = true;
                        if (name is not null) name.IsEnabled = attached ? icon!.IsChecked == true : enabled;
                    }
                    if (roadSource is not null) roadSource.IsEnabled = enabled;
                    if (buildingSource is not null) buildingSource.IsEnabled = enabled;
                }
                foreach (var (icon, _, attached) in gates)
                    if (attached && icon is not null)
                    {
                        icon.Checked += (_, _) => ApplyDisplayGates(); icon.Unchecked += (_, _) => ApplyDisplayGates();
                    }
                if (master is not null) master.Toggled += (_, _) => ApplyDisplayGates();
                ApplyDisplayGates();

                if (target == "normal")
                    foreach (var (kind, label) in new[] { ("road", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_8C919EE4A4")), ("district", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_5939962905")) })
                    {
                        var page = Page(kind, label, label, UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_DEA33AAC15"));
                        var scaling = new StackPanel { Spacing = 10 };
                        page.Children.Add(new AppearanceFieldGrid(
                            Field($"label.font.{kind}", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_078838DA42")),
                            Field($"label.size.{kind}", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_F9CCBFE15E"), scaling),
                            Field($"label.color.{kind}", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_F21EF4ABA5")),
                            Field($"label.stroke_width.{kind}", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_A0DF88327C"),
                                fields[$"label.stroke_width.{kind}"] is JsonObject ? scaling : null),
                            Field($"label.stroke_color.{kind}", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_D86FD32B7C"))));
                        AddSample(page, kind);
                        scaling.Children.Add(Help(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_4D2A6ECC62")));
                        page.Children.Add(WorkspaceSection.Create(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_EC7FFA2896"), scaling, expanded: false));
                    }
                var poiPage = Page("poi", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_9AB9CE990C"), UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_9AB9CE990C"),
                    UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_AB9506AB75"));
                var poiScaling = new StackPanel { Spacing = 10 };
                poiPage.Children.Add(new AppearanceFieldGrid(Field("poi.set", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_2BCCB49F29")),
                    Field("poi.size", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_CC7213031E"), poiScaling),
                    Field("poi.font", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_6882633176")), Field("poi.name_size", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_F9CCBFE15E"), poiScaling),
                    Field("poi.name_stroke", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_D86FD32B7C")), Field("poi.name_stroke_ratio", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_8373A5D587"))));
                AddSample(poiPage, "poi");
                if (target == "transit")
                    poiPage.Children.Add(Help(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_5B818B9F10")));
                poiScaling.Children.Add(Help(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_4D2A6ECC62")));
                poiPage.Children.Add(WorkspaceSection.Create(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_EC7FFA2896"), poiScaling, expanded: false));
                var merge = Page("merge", UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_4E318B9011"), UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_58D54FC2B1"), UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_7F4BF2E908"));
                editor = new PoiProfileEditorPanel(target, defaults ? data.GetProperty("profileDefaults") : data.GetProperty("profile"),
                    _ => Task.CompletedTask, message => status.Text = message);
                merge.Children.Add(editor.Content);
                foreach (var page in pages)
                {
                    navigation.Items.Add(new ListViewItem { Content = page.Title, Tag = page.Key });
                    compactNavigation.Items.Add(new ComboBoxItem { Content = page.Title, Tag = page.Key });
                }
                UpdateSamples();
                SelectPage(pages.Any(page => page.Key == previousPage) ? previousPage : "display");
                ArrangeWorkspace();
            }

            Build(false); window.SetBody(body);
            window.AddAction(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_BA2E93E730"), () =>
            {
                foreach (var key in editableFields) fields[key] = JsonNode.Parse(data.GetProperty("defaults").GetProperty(key).GetRawText());
                foreach (var key in editableOptions) options[key] = JsonNode.Parse(data.GetProperty("optionDefaults").GetProperty(key).GetRawText());
                visibility = JsonNode.Parse(data.GetProperty("visibilityDefaults").GetRawText())!.AsObject();
                if (target == "normal") textIconsEnabled = true;
                Build(true); return Task.CompletedTask;
            });
            window.AddAction(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_2CD0F3BE87"), () => window.RequestCloseAsync());
            var apply = window.AddAction(UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_63C73C4730"), async () =>
            {
                if (!Current()) { status.Text = UiLanguage.Text("MainWindow_MapSettings_ShowMapPoiSettingsAsync_316B8B9770"); return; }
                try
                {
                    window.CommitFocusedInput();
                    foreach (var check in checks) check();
                    editor?.Commit();
                    var edits = new JsonObject();
                    foreach (var (key, value) in fields)
                        if (!JsonNode.DeepEquals(value, original[key])) edits[key] = value?.DeepClone();
                    await _engine.RequestAsync("setTextIconSettings", new { target, enabled = textIconsEnabled, theme = data.GetProperty("theme").GetString(),
                        fields = edits, profile = editor?.Snapshot ?? JsonNode.Parse(data.GetProperty("profile").GetRawText())!.AsObject(),
                        visibility, options, documentEpoch = epoch });
                    await RefreshLivePreviewAsync(); await ApplyContextAsync();
                    await RefreshSnapshotAsync();
                    await window.RequestCloseAsync();
                }
                catch (Exception error) { status.Text = error.Message; }
            });
            apply.Style = (Style)Application.Current.Resources["AccentButtonStyle"];
            try { await window.ShowAsync(); }
            finally { if (_mapPoiSettingsWindow == window) _mapPoiSettingsWindow = null; }
        }
        finally { _openingEditors.Remove("map-poi"); }

        static TextBlock Help(string text)
        {
            var copy = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap };
            ExportDesign.Secondary(copy); return copy;
        }
        static double ReferenceSize(JsonNode value) => value is JsonObject pair
            ? (pair["fixed"]?.GetValue<double>() ?? 0) + (pair["scaled"]?.GetValue<double>() ?? 0) : value.GetValue<double>();
    }
}
