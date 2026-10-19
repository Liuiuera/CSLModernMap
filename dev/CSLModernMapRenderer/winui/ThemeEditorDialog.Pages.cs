// 构建主题编辑器的设置页面。
using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace CSLModernMapWinUI;

internal sealed partial class ThemeEditorDialog
{
    private void BuildBasicPage()
    {
        foreach (var key in new[] { "name", "description" })
            if (_working[key] is { } value && Matches(value, [key])) _fieldsPanel.Children.Add(MakeField(value, [key]));
        _fieldsPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildBasicPage_63CC58D88D") + _savePath, FontSize = 12, TextWrapping = TextWrapping.Wrap, Opacity = .8 });
        _fieldsPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildBasicPage_A1D94D780D"), FontSize = 12, TextWrapping = TextWrapping.Wrap });
        var saveAs = new Button { Content = UiLanguage.Text("ThemeEditorDialog_Pages_BuildBasicPage_7BB3396904") }; saveAs.Click += async (_, _) => await SaveAsAsync(); _fieldsPanel.Children.Add(saveAs);
        foreach (var (key, value) in _working.Where(pair => pair.Value is not JsonObject && pair.Key is not "name" and not "description"))
            if (value is not null && Matches(value, [key])) _fieldsPanel.Children.Add(MakeField(value, [key]));
    }
    private void BuildRoadsPage()
    {
        var roads = _working["roads"]!.AsObject();
        _fieldsPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_3001DE3658"), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        var table = new Grid { ColumnSpacing = 8, RowSpacing = 6 };
        foreach (var width in new[] { new GridLength(76), new GridLength(1, GridUnitType.Star), new GridLength(1, GridUnitType.Star), new GridLength(65) })
            table.ColumnDefinitions.Add(new ColumnDefinition { Width = width });
        table.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var headers = new[] { UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_FBE666F1E0"), UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_05FB7CBA47"), UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_ADCB808036"), UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_1D68EDF7EB") };
        for (var c = 0; c < 4; c++) { var header = new TextBlock { Text = headers[c], FontSize = 12 }; Grid.SetColumn(header, c); table.Children.Add(header); }
        var borderFields = new StackPanel { Spacing = 10 };
        var rowIndex = 0;
        foreach (var (key, title) in RoadLevels)
        {
            if (roads[key] is not JsonObject cfg) continue;
            var row = ++rowIndex; table.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            var name = new StackPanel { Spacing = 5 };
            name.Children.Add(new TextBlock { Text = title, FontSize = 14 });
            var line = RoadLine(key, 40); name.Children.Add(line);
            var choose = new Button { Content = name, Padding = new Thickness(0, 7, 0, 7), HorizontalContentAlignment = HorizontalAlignment.Left,
                Style = (Style)Application.Current.Resources["SubtleButtonStyle"] };
            Name(choose, UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_13D61FEA9F") + title); choose.Click += (_, _) => { _selectedRoad = key; UpdateSamples(); };
            Grid.SetRow(choose, row); table.Children.Add(choose);
            for (var c = 1; c < 4; c++)
            {
                var attr = c == 1 ? "fill" : c == 2 ? "border" : "min_fill_px";
                FrameworkElement control = cfg[attr] is { } value ? MakeField(value, ["roads", key, attr], title + headers[c], showLabel: false)
                    : new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_80A57E03F0"), FontSize = 12, TextWrapping = TextWrapping.Wrap };
                control.VerticalAlignment = VerticalAlignment.Center; Grid.SetRow(control, row); Grid.SetColumn(control, c); table.Children.Add(control);
            }
            if (cfg["border_width"] is { } border) borderFields.Children.Add(MakeField(border, ["roads", key, "border_width"], title + UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_C0AA8919A8")));
            foreach (var (extra, value) in cfg.Where(pair => pair.Key is not "fill" and not "border" and not "min_fill_px" and not "border_width"))
                if (value is not null) BuildNode(value, ["roads", key, extra], borderFields);
        }
        _fieldsPanel.Children.Add(table);
        _fieldsPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_512DF348F2"), FontSize = 12, TextWrapping = TextWrapping.Wrap, Opacity = .8 });
        if (roads["width_scale"] is { } scale)
        {
            _fieldsPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_37AE912BEC"), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
            _fieldsPanel.Children.Add(SliderField(scale, ["roads", "width_scale"], UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_E9C68028D9")));
            _fieldsPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_01EEE2E647"), FontSize = 12 });
        }
        AddGroup(UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_593A6AFD98"), borderFields, _fieldsPanel, "roads.border_width");
        var elevated = new StackPanel { Spacing = 10 }; var extras = new StackPanel { Spacing = 10 };
        foreach (var (key, value) in roads)
        {
            if (value is null || key == "width_scale" || RoadLevels.Any(level => level.Key == key)) continue;
            var target = key.Contains("elevated") || key.Contains("tunnel") || key is "bridge" or "tunnel" ? elevated : extras;
            if (value is JsonObject obj) { var content = new StackPanel { Spacing = 8 }; BuildNode(obj, ["roads", key], content); AddGroup(Label(["roads", key]), content, target, "roads." + key); }
            else BuildNode(value, ["roads", key], target);
        }
        AddGroup(UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_C2AE96D4A6"), elevated, _fieldsPanel, "roads.elevated"); AddGroup(UiLanguage.Text("ThemeEditorDialog_Pages_BuildRoadsPage_A646E3934A"), extras, _fieldsPanel, "roads.extras");
    }
    private FrameworkElement RoadLine(string level, double width)
    {
        var line = new Border { Width = width, CornerRadius = new CornerRadius(1), HorizontalAlignment = HorizontalAlignment.Left };
        void Update()
        {
            if (_working["roads"]?[level] is not JsonObject cfg) return;
            if (cfg["fill"] is { } fill) line.Background = BrushFor(fill.GetValue<string>());
            if (cfg["border"] is { } border) line.BorderBrush = BrushFor(border.GetValue<string>());
            var borderWidth = cfg["border_width"] is { } bw ? SizeAt(bw, 1) : 1;
            var minimum = cfg["min_fill_px"] is { } minFill ? Numeric(minFill) : 0;
            var scale = Numeric(_working["roads"]!["width_scale"]!);
            line.Height = Math.Max(.5 + 16 * scale, minimum) + borderWidth;
            line.BorderThickness = new Thickness(borderWidth / 2);
        }
        _sampleUpdates.Add(Update); Update(); return line;
    }
    private static double SizeAt(JsonNode value, double scale) => value is JsonObject pair
        ? (pair["fixed"] is { } fixedPart ? Numeric(fixedPart) : 0) + (pair["scaled"] is { } scaledPart ? Numeric(scaledPart) * scale : 0) : Numeric(value);
    private static double Numeric(JsonNode value) => value.AsValue().TryGetValue<double>(out var number) ? number : value.GetValue<long>();
    private FrameworkElement SliderField(JsonNode value, string[] path, string title)
    {
        var panel = new StackPanel { Spacing = 5 }; panel.Children.Add(new TextBlock { Text = title });
        var field = Field(path);
        var slider = new Slider { Minimum = field is { } f ? f.GetProperty("low").GetDouble() : 0,
            Maximum = field is { } h ? h.GetProperty("high").GetDouble() : 5,
            StepFrequency = field is { } s ? s.GetProperty("step").GetDouble() : .01, Value = Numeric(value), MinWidth = 0 };
        var exact = MakeField(value, path, title, showLabel: false); exact.Width = 80;
        Name(slider, title, string.Join('.', path) + ".slider");
        var row = new Grid { ColumnSpacing = 12 }; row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) }); row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.Children.Add(slider); Grid.SetColumn(exact, 1); row.Children.Add(exact); panel.Children.Add(row);
        var syncing = false;
        slider.ValueChanged += (_, _) => { if (!syncing) Change(path, JsonValue.Create(slider.Value)); };
        _sampleUpdates.Add(() => { syncing = true; slider.Value = Numeric(Node(path)!); syncing = false;
            if (exact is StackPanel stack && stack.Children.OfType<NumberBox>().FirstOrDefault() is { } box && box.Value != slider.Value)
            { var building = _building; _building = true; box.Value = slider.Value; _building = building; } });
        return panel;
    }
    private void BuildTerrainPage()
    {
        var terrain = _working["terrain"]!.AsObject();
        if (terrain["elevation_mode"] is { } mode) _fieldsPanel.Children.Add(MakeField(mode, ["terrain", "elevation_mode"], UiLanguage.Text("ThemeEditorDialog_Pages_BuildTerrainPage_7A439491B8")));
        if (terrain["elevation_colors"] is JsonArray ramp)
        {
            _fieldsPanel.Children.Add(RampBand(["terrain", "elevation_colors"]));
            var ends = new Grid(); ends.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) }); ends.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            ends.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildTerrainPage_4C204FB7E5"), FontSize = 12 }); var high = new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildTerrainPage_99A62ED651"), FontSize = 12 }; Grid.SetColumn(high, 1); ends.Children.Add(high); _fieldsPanel.Children.Add(ends);
            BuildRamp(ramp, ["terrain", "elevation_colors"], _fieldsPanel);
        }
        if (terrain["hillshade"] is JsonObject shade)
        {
            if (shade["enabled"] is { } enabled) _fieldsPanel.Children.Add(MakeField(enabled, ["terrain", "hillshade", "enabled"], UiLanguage.Text("ThemeEditorDialog_Pages_BuildTerrainPage_8D2D98C22C")));
            if (shade["intensity"] is { } intensity) _fieldsPanel.Children.Add(SliderField(intensity, ["terrain", "hillshade", "intensity"], UiLanguage.Text("ThemeEditorDialog_Pages_BuildTerrainPage_9D46E5EF25")));
            var detail = new StackPanel { Spacing = 10 };
            foreach (var (key, value) in shade)
                if (value is not null && key is not "enabled" and not "intensity" && !Hidden(["terrain", "hillshade", key])) BuildNode(value, ["terrain", "hillshade", key], detail);
            AddGroup(UiLanguage.Text("ThemeEditorDialog_Pages_BuildTerrainPage_DC3967095C"), detail, _fieldsPanel, "terrain.hillshade.detail");
        }
        var other = new StackPanel { Spacing = 10 };
        foreach (var (key, value) in terrain)
            if (value is not null && key is not "elevation_mode" and not "elevation_colors" and not "hillshade" && !Hidden(["terrain", key]))
                BuildNode(value, ["terrain", key], other);
        AddGroup(UiLanguage.Text("ThemeEditorDialog_Pages_BuildTerrainPage_BB3B667B97"), other, _fieldsPanel, "terrain.other");
    }
    private Border RampBand(string[] path)
    {
        var band = new Border { Height = 22, CornerRadius = new CornerRadius(3) };
        Name(band, UiLanguage.Text("ThemeEditorDialog_Pages_RampBand_727FCFB70E"));
        void Update()
        {
            var stops = Node(path)!.AsArray().OfType<JsonObject>().OrderBy(stop => Numeric(stop["threshold"]!)).ToArray();
            if (stops.Length == 0) { band.Background = ResourceBrush("ControlFillColorDefaultBrush"); return; }
            var low = Numeric(stops[0]["threshold"]!); var high = Numeric(stops[^1]["threshold"]!);
            var brush = new LinearGradientBrush { StartPoint = new Windows.Foundation.Point(0, .5), EndPoint = new Windows.Foundation.Point(1, .5) };
            var mode = _working["terrain"]?["elevation_mode"]?.GetValue<string>();
            var continuous = mode is "continuous" or "gradient" or "linear";
            for (var i = 0; i < stops.Length; i++)
            {
                var position = high > low ? (Numeric(stops[i]["threshold"]!) - low) / (high - low) : 0;
                if (!continuous && i > 0) brush.GradientStops.Add(new GradientStop { Offset = position, Color = AppearanceControls.ParseColor(stops[i - 1]["color"]!.GetValue<string>()) });
                brush.GradientStops.Add(new GradientStop { Offset = position, Color = AppearanceControls.ParseColor(stops[i]["color"]!.GetValue<string>()) });
            }
            band.Background = brush;
        }
        _sampleUpdates.Add(Update); Update(); return band;
    }
    private void BuildRamp(JsonArray array, string[] path, StackPanel panel)
    {
        var header = new Grid { ColumnSpacing = 8 }; header.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(88) }); header.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        header.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildRamp_034F44D573"), FontSize = 12 }); var color = new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildRamp_070A8016FC"), FontSize = 12 }; Grid.SetColumn(color, 1); header.Children.Add(color); panel.Children.Add(header);
        for (var i = 0; i < array.Count; i++)
        {
            var index = i; var stop = array[i]!.AsObject();
            var row = new Grid { ColumnSpacing = 8 };
            foreach (var size in new[] { new GridLength(88), new GridLength(1, GridUnitType.Star), new GridLength(32) }) row.ColumnDefinitions.Add(new ColumnDefinition { Width = size });
            row.Children.Add(MakeField(stop["threshold"]!, path.Append(i.ToString()).Append("threshold").ToArray(), UiLanguage.Text("ThemeEditorDialog_Pages_BuildRamp_81BE47F750"), showLabel: false));
            var value = MakeField(stop["color"]!, path.Append(i.ToString()).Append("color").ToArray(), UiLanguage.Text("ThemeEditorDialog_Pages_BuildRamp_859A7D647B"), showLabel: false); Grid.SetColumn(value, 1); row.Children.Add(value);
            var remove = new Button { Content = "×", Width = 32, Padding = new Thickness(0) }; Name(remove, UiLanguage.Text("ThemeEditorDialog_Pages_BuildRamp_8FF6AC7DDB") + (i + 1));
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(remove, string.Join('.', path) + ".remove." + i);
            remove.Click += (_, _) => { if (!FlushInputs()) return; var next = (JsonArray)Node(path)!.DeepClone(); next.RemoveAt(index); Change(path, next); RebuildFields(); };
            Grid.SetColumn(remove, 2); row.Children.Add(remove); panel.Children.Add(row);
        }
        var add = new Button { Content = UiLanguage.Text("ThemeEditorDialog_Pages_BuildRamp_6972FCEAB2"), Style = (Style)Application.Current.Resources["SubtleButtonStyle"] };
        Name(add, UiLanguage.Text("ThemeEditorDialog_Pages_BuildRamp_6F12F4E988"), string.Join('.', path) + ".add");
        add.Click += async (_, _) =>
        {
            if (!FlushInputs()) return;
            var height = new TextBox { Header = UiLanguage.Text("ThemeEditorDialog_Pages_BuildRamp_81BE47F750") }; var hex = new TextBox { Header = UiLanguage.Text("ThemeEditorDialog_Pages_BuildRamp_AA2CA3DEE7") };
            var body = new StackPanel { Spacing = 8 }; var message = new TextBlock { TextWrapping = TextWrapping.Wrap }; body.Children.Add(height); body.Children.Add(hex); body.Children.Add(message);
            var dialog = new ContentDialog { XamlRoot = _window.Root.XamlRoot, Title = UiLanguage.Text("ThemeEditorDialog_Pages_BuildRamp_6F12F4E988"), Content = body, PrimaryButtonText = UiLanguage.Text("ThemeEditorDialog_Pages_BuildRamp_7A8A11EAD5"), CloseButtonText = UiLanguage.Text("ThemeEditorDialog_Pages_BuildRamp_2CD0F3BE87") };
            double threshold = 0;
            var thresholdField = Field(path.Append("0").Append("threshold").ToArray());
            var low = thresholdField is { } lo ? lo.GetProperty("low").GetDouble() : -12000;
            var high = thresholdField is { } hi ? hi.GetProperty("high").GetDouble() : 12000;
            dialog.PrimaryButtonClick += (_, args) =>
            { if (!double.TryParse(height.Text, out threshold) || !double.IsFinite(threshold) || threshold < low || threshold > high || !HexColor.IsMatch(hex.Text))
                { message.Text = UiLanguage.Format("ThemeEditorDialog_Pages_BuildRamp_C217CD91ED", low, high); args.Cancel = true; } };
            if (await dialog.ShowAsync() == ContentDialogResult.Primary)
            { var next = (JsonArray)Node(path)!.DeepClone(); next.Add(new JsonObject { ["threshold"] = threshold, ["color"] = hex.Text }); Change(path, next); RebuildFields(); }
        };
        panel.Children.Add(add);
    }
    private void BuildLabelsPage()
    {
        var labels = _working["labels"]!.AsObject();
        var available = LabelTypes.Where(type => labels[type.Key] is JsonObject).ToArray();
        if (!available.Any(type => type.Key == _labelType) && available.Length > 0) _labelType = available[0].Key;
        var objects = new Grid { ColumnSpacing = 6, RowSpacing = 6 };
        for (var i = 0; i < 5; i++) objects.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        for (var i = 0; i < available.Length; i++)
        {
            var (key, title) = available[i]; var button = new Microsoft.UI.Xaml.Controls.Primitives.ToggleButton { Content = title, IsChecked = key == _labelType, Padding = new Thickness(8, 4, 8, 4) };
            Name(button, UiLanguage.Text("ThemeEditorDialog_Pages_BuildLabelsPage_0518365699") + title + UiLanguage.Text("ThemeEditorDialog_Pages_BuildLabelsPage_1D0FD5F933"), "theme.label." + key);
            button.Click += (_, _) => { if (!FlushInputs()) { button.IsChecked = key == _labelType; return; } _labelType = key; RebuildFields(); };
            Grid.SetColumn(button, i); objects.Children.Add(button);
        }
        objects.SizeChanged += (_, _) =>
        {
            var narrow = objects.ActualWidth is > 0 and < 300;
            objects.RowDefinitions.Clear(); objects.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            if (narrow) objects.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            for (var i = 0; i < objects.Children.Count; i++) { Grid.SetColumn((FrameworkElement)objects.Children[i], narrow ? i % 3 : i); Grid.SetRow((FrameworkElement)objects.Children[i], narrow ? i / 3 : 0); }
        };
        _fieldsPanel.Children.Add(objects);
        if (labels[_labelType] is not JsonObject cfg) return;
        var sample = new Image { Stretch = Stretch.Uniform, MaxHeight = 100, HorizontalAlignment = HorizontalAlignment.Stretch, VerticalAlignment = VerticalAlignment.Center };
        var sampleStatus = new TextBlock { FontSize = 12, TextWrapping = TextWrapping.Wrap };
        var sampleSurface = Surface(sample, "ControlFillColorSecondaryBrush", new Thickness(8)); sampleSurface.MinHeight = 86; sampleSurface.CornerRadius = new CornerRadius(5);
        _fieldsPanel.Children.Add(sampleSurface); _fieldsPanel.Children.Add(sampleStatus);
        _textSample = sample; _textSampleStatus = sampleStatus;
        if (cfg["font"] is { } font) _fieldsPanel.Children.Add(MakeField(font, ["labels", _labelType, "font"], UiLanguage.Text("ThemeEditorDialog_Pages_BuildLabelsPage_078838DA42")));
        AddPairedFields("size", UiLanguage.Text("ThemeEditorDialog_Pages_BuildLabelsPage_52826B6A04"), "color", UiLanguage.Text("ThemeEditorDialog_Pages_BuildLabelsPage_F21EF4ABA5"));
        if (cfg["stroke"] is not null || cfg["stroke_width"] is not null)
        { _fieldsPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildLabelsPage_3CC1BA50ED"), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold }); AddPairedFields("stroke", UiLanguage.Text("ThemeEditorDialog_Pages_BuildLabelsPage_D86FD32B7C"), "stroke_width", UiLanguage.Text("ThemeEditorDialog_Pages_BuildLabelsPage_C0AA8919A8")); }
        _fieldsPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildLabelsPage_1EFBE6AAF4"), FontSize = 12, TextWrapping = TextWrapping.Wrap, Opacity = .8 });
        if (_labelType is "building" or "stop")
            _fieldsPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildLabelsPage_858D9AE639"), FontSize = 12, TextWrapping = TextWrapping.Wrap, Opacity = .8 });
        var extra = new StackPanel { Spacing = 10 };
        foreach (var (key, value) in cfg) if (value is not null && key is not "font" and not "size" and not "color" and not "stroke" and not "stroke_width") BuildNode(value, ["labels", _labelType, key], extra);
        foreach (var (key, value) in labels) if (value is not null && !LabelTypes.Any(type => type.Key == key)) BuildNode(value, ["labels", key], extra);
        AddGroup(UiLanguage.Text("ThemeEditorDialog_Pages_BuildLabelsPage_AB402539D4"), extra, _fieldsPanel, "labels.extra");
        void AddPairedFields(string first, string firstTitle, string second, string secondTitle)
        {
            var left = cfg[first] is { } a ? MakeField(a, ["labels", _labelType, first], firstTitle) : null;
            var right = cfg[second] is { } b ? MakeField(b, ["labels", _labelType, second], secondTitle) : null;
            if (left is not null && right is not null) _fieldsPanel.Children.Add(PairRow(left, right));
            else if (left is not null || right is not null) _fieldsPanel.Children.Add((left ?? right)!);
        }
    }
    private void BuildOtherPage()
    {
        if (_working[_section] is not JsonObject section) { BuildNode(_working[_section]!, [_section], _fieldsPanel); return; }
        var common = _section switch
        {
            "canvas" => new[] { "background" }, "water" => new[] { "color", "border", "border_width" }, "forest" => new[] { "color" },
            "built_area" => new[] { "enabled", "color" }, "buildings" => new[] { "color_mode", "default", "border", "border_width" },
            "zone" => new[] { "enabled", "border", "border_width", "fill", "fill_alpha" },
            "district" => new[] { "enabled", "border", "border_width", "fill_alpha", "fill_colors" },
            "land_use" => new[] { "enabled", "detail", "alpha", "colors" }, "procedural_objects" => new[] { "colors", "border", "border_width" },
            "railway" => new[] { "line", "dash" }, "transports" => new[] { "enabled", "auto_color", "width" },
            "poi_icons" => new[] { "icon_size", "name_size", "bg_circle", "bg_circle_size", "font" },
            "watermark" => new[] { "enabled", "position", "content" }, _ => Array.Empty<string>(),
        };
        foreach (var key in common)
        {
            if (section[key] is not { } value || Hidden([_section, key])) continue;
            if (_section == "watermark" && key == "content" && value is JsonObject content)
            {
                _fieldsPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Pages_BuildOtherPage_6A57028967") });
                foreach (var (entry, enabled) in content)
                {
                    if (enabled is JsonValue v && v.TryGetValue<bool>(out var on))
                    { var check = new CheckBox { Content = Label(["watermark", "content", entry]), IsChecked = on }; Name(check, check.Content.ToString()!, "watermark.content." + entry); check.Click += (_, _) => Change(["watermark", "content", entry], JsonValue.Create(check.IsChecked == true)); _fieldsPanel.Children.Add(check); }
                    else if (enabled is not null) BuildNode(enabled, ["watermark", "content", entry], _fieldsPanel);
                }
            }
            else if (value is JsonObject obj && !IsPair(obj)) BuildNode(obj, [_section, key], _fieldsPanel);
            else _fieldsPanel.Children.Add(MakeField(value, [_section, key], _section == "railway" ? key == "line" ? UiLanguage.Text("ThemeEditorDialog_Pages_BuildOtherPage_D47A6B6A29") : UiLanguage.Text("ThemeEditorDialog_Pages_BuildOtherPage_FECDADDF86") : null));
        }
        var extra = new StackPanel { Spacing = 10 };
        foreach (var (key, value) in section) if (value is not null && !common.Contains(key)) BuildNode(value, [_section, key], extra);
        AddGroup(_section == "watermark" ? UiLanguage.Text("ThemeEditorDialog_Pages_BuildOtherPage_06CD245060") : UiLanguage.Text("ThemeEditorDialog_Pages_BuildOtherPage_72064D684F"), extra, _fieldsPanel, _section + ".extra");
    }
}
