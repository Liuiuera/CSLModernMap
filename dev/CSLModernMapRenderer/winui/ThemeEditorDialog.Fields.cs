// 构建主题编辑器的字段控件。
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

internal sealed partial class ThemeEditorDialog
{
    private readonly List<Action> _responsiveUpdates = new();
    private JsonElement? Field(string[] path)
    {
        if (_metadata.GetProperty("fields").TryGetProperty(string.Join('.', path), out var field)) return field;
        var wildcard = string.Join('.', path.Select(part => int.TryParse(part, out _) ? "*" : part));
        if (_metadata.GetProperty("fields").TryGetProperty(wildcard, out field)) return field;
        return null;
    }
    private string Label(string[] path)
    {
        if (path[^1] == "fixed") return UiLanguage.Text("ThemeEditorDialog_Fields_Label_803A1B3A33");
        if (path[^1] == "scaled") return UiLanguage.Text("ThemeEditorDialog_Fields_Label_606D5B2CC1");
        return Field(path) is { } field ? field.GetProperty("label").GetString()! : path[^1].Replace('_', ' ');
    }
    private bool Hidden(string[] path)
    {
        if (path.Length > 1 && path[0] == "terrain" && path[1] == "ground") return true;
        if (Field(path) is { } field && field.TryGetProperty("hidden", out var hidden) && hidden.GetBoolean()) return true;
        if (path[0] == "canvas" && path[^1] is "width" or "height") return true;
        if (path[0] == "buildings" && path.Length > 1 && path[1] is "residential" or "commercial" or "industrial" or "office" or "shadow") return true;
        return path.Length > 2 && path[0] == "terrain" && path[1] == "hillshade" && path[^1] is "mode" or "facet_size_meters" or "facet_jitter";
    }
    private bool MatchesField(string[] path)
    {
        if (Hidden(path)) return false;
        var query = _search.Text.Trim();
        if (query.Length == 0 || SectionLabel(path[0]).Contains(query, StringComparison.OrdinalIgnoreCase)) return true;
        return string.Join('.', path).Contains(query, StringComparison.OrdinalIgnoreCase) || Label(path).Contains(query, StringComparison.OrdinalIgnoreCase);
    }
    private bool Matches(JsonNode node, string[] path)
    {
        if (Hidden(path)) return false;
        if (MatchesField(path)) return true;
        var query = _search.Text.Trim();
        if (node is JsonObject obj) return obj.Any(pair => pair.Value is { } value && Matches(value, path.Append(pair.Key).ToArray()));
        if (node is JsonArray array) return array.Select((value, index) => (value, index)).Any(item => item.value is { } value && Matches(value, path.Append(item.index.ToString()).ToArray()));
        return node.ToJsonString().Contains(query, StringComparison.OrdinalIgnoreCase);
    }
    private bool SectionMatches(string key) => key == "__basic__"
        ? new[] { "name", "description" }.Any(name => _working[name] is { } value && Matches(value, [name])) || SectionLabel(key).Contains(_search.Text.Trim(), StringComparison.OrdinalIgnoreCase)
        : _working[key] is { } node && Matches(node, [key]);
    private void RebuildFields()
    {
        _building = true; _sampleCancellation?.Cancel(); _sampleRevision++;
        _textSample = null; _textSampleStatus = null;
        _checks.Clear(); _errors.Clear(); _sampleUpdates.Clear(); _responsiveUpdates.Clear(); _fieldsPanel.Children.Clear();
        try
        {
            if (!SectionMatches(_section))
            {
                _fieldsPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Fields_RebuildFields_9CB6C68C5F"), FontSize = 20 });
                var clear = new Button { Content = UiLanguage.Text("ThemeEditorDialog_Fields_RebuildFields_EE32F25F70") }; clear.Click += (_, _) => _search.Text = "";
                _fieldsPanel.Children.Add(clear); return;
            }
            _fieldsPanel.Children.Add(new TextBlock { Text = SectionLabel(_section), FontSize = 20, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
            _fieldsPanel.Children.Add(new TextBlock { Text = PageDescription(), FontSize = 12, TextWrapping = TextWrapping.Wrap, Opacity = .8 });
            ToolTipService.SetToolTip(_restore, _search.Text.Trim().Length == 0 ? UiLanguage.Text("ThemeEditorDialog_Fields_RebuildFields_AFF535F598") : UiLanguage.Text("ThemeEditorDialog_Fields_RebuildFields_04016999A1"));
            _restore.Content = _search.Text.Trim().Length == 0 ? UiLanguage.Text("ThemeEditorDialog_Fields_RebuildFields_AC9EA45E9D") : UiLanguage.Text("ThemeEditorDialog_Fields_RebuildFields_3B5E729BF4");
            if (_section == "__basic__") BuildBasicPage();
            else if (_search.Text.Trim().Length > 0 && !SectionLabel(_section).Contains(_search.Text.Trim(), StringComparison.OrdinalIgnoreCase))
                BuildNode(_working[_section]!, [_section], _fieldsPanel);
            else if (_section == "roads") BuildRoadsPage();
            else if (_section == "terrain") BuildTerrainPage();
            else if (_section == "labels") BuildLabelsPage();
            else BuildOtherPage();
        }
        finally { BuildSamples(); _building = false; UpdateStatus(); UpdateSamples(); }
    }
    private string PageDescription() => _section switch
    {
        "roads" => UiLanguage.Text("ThemeEditorDialog_Fields_PageDescription_5986B685A1"),
        "terrain" => UiLanguage.Text("ThemeEditorDialog_Fields_PageDescription_388AAAEA79"),
        "labels" => UiLanguage.Text("ThemeEditorDialog_Fields_PageDescription_3F6F438320"),
        "__basic__" => UiLanguage.Text("ThemeEditorDialog_Fields_PageDescription_7245F659EA"),
        "railway" => UiLanguage.Text("ThemeEditorDialog_Fields_PageDescription_E4131EFDCA"),

        _ => UiLanguage.Text("ThemeEditorDialog_Fields_PageDescription_8714276423"),
    };
    private void BuildNode(JsonNode node, string[] path, StackPanel panel)
    {
        if (!Matches(node, path)) return;
        if (node is JsonObject obj && !IsPair(obj))
        {
            foreach (var (key, child) in obj)
            {
                if (child is null || Hidden(path.Append(key).ToArray())) continue;
                var childPath = path.Append(key).ToArray();
                if (!Matches(child, childPath)) continue;
                if (child is JsonObject inner && !IsPair(inner))
                {
                    var contents = new StackPanel { Spacing = 10 };
                    BuildNode(inner, childPath, contents); AddGroup(Label(childPath), contents, panel, string.Join('.', childPath));
                }
                else panel.Children.Add(MakeField(child, childPath));
            }
        }
        else panel.Children.Add(MakeField(node, path));
    }
    private void AddGroup(string title, StackPanel contents, StackPanel panel, string key)
    {
        if (contents.Children.Count == 0) return;
        var expanded = _search.Text.Trim().Length > 0 || _expanded.GetValueOrDefault(key, false);
        var expander = WorkspaceSection.Create(title, contents, expanded: expanded);
        expander.Expanding += (_, _) => _expanded[key] = true;
        expander.Collapsed += (_, _) => _expanded[key] = false;
        panel.Children.Add(expander);
    }
    private FrameworkElement MakeField(JsonNode node, string[] path, string? title = null, bool showLabel = true)
    {
        var label = title ?? Label(path); var key = string.Join('.', path); var field = Field(path);
        var kind = field is { } info && info.TryGetProperty("kind", out var k) ? k.GetString() : null;
        var panel = new StackPanel { Spacing = 4, HorizontalAlignment = HorizontalAlignment.Stretch };
        var error = (TextBlock)Microsoft.UI.Xaml.Markup.XamlReader.Load("<TextBlock xmlns=\"http://schemas.microsoft.com/winfx/2006/xaml/presentation\" FontSize=\"12\" TextWrapping=\"Wrap\" Visibility=\"Collapsed\" Foreground=\"{ThemeResource SystemFillColorCriticalBrush}\" />");
        void Error(string? message)
        { error.Text = message ?? ""; error.Visibility = message is null ? Visibility.Collapsed : Visibility.Visible;
            if (message is null) _errors.Remove(key); else _errors.Add(key); UpdateStatus(); }
        if (showLabel) panel.Children.Add(new TextBlock { Text = label, TextWrapping = TextWrapping.Wrap });
        if (node is JsonObject objectValue && !IsPair(objectValue))
            BuildNode(objectValue, path, panel);
        else if (node is JsonObject pair && IsPair(pair))
        {
            var fixedPart = MakeField(pair["fixed"]!, path.Append("fixed").ToArray(), UiLanguage.Text("ThemeEditorDialog_Fields_MakeField_986E6B84D7"));
            var scaledPart = MakeField(pair["scaled"]!, path.Append("scaled").ToArray(), UiLanguage.Text("ThemeEditorDialog_Fields_MakeField_606D5B2CC1"));
            panel.Children.Add(PairRow(fixedPart, scaledPart));
            panel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Fields_MakeField_CF663DC217"), FontSize = 12, TextWrapping = TextWrapping.Wrap, Opacity = .8 });
            foreach (var (extra, value) in pair.Where(pair => pair.Key is not "fixed" and not "scaled"))
                if (value is not null) { var extras = new StackPanel { Spacing = 8 }; BuildNode(value, path.Append(extra).ToArray(), extras); AddGroup(Label(path.Append(extra).ToArray()), extras, panel, key + "." + extra); }
        }
        else if (node is JsonArray array)
        {
            if (kind == "ramp" || path[^1] is "elevation_colors" or "mountain_colors") BuildRamp(array, path, panel);
            else if (kind == "types" || key == "transports.enabled") BuildTypes(array, path, panel);
            else if (array.All(value => value is JsonValue) || array.Count == 0) BuildList(array, path, panel, kind);
            else
            {
                var input = new TextBox { Text = array.ToJsonString(new JsonSerializerOptions { WriteIndented = true }), AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 100 };
                Name(input, label + UiLanguage.Text("ThemeEditorDialog_Fields_MakeField_E381B63F7F"), key);
                void Commit()
                { try { if (JsonNode.Parse(input.Text) is not JsonArray next) throw new JsonException(UiLanguage.Text("ThemeEditorDialog_Fields_MakeField_870D5FE5D4")); Error(null); Change(path, next); }
                    catch (JsonException exception) { Error(exception.Message); } }
                input.LostFocus += (_, _) => Commit(); _checks[key] = Commit; panel.Children.Add(input);
                input.TextChanged += (_, _) => InvalidateInputPreview();
            }
        }
        else
        {
            using var document = JsonDocument.Parse(node.ToJsonString()); var value = document.RootElement.Clone();
            if (value.ValueKind is JsonValueKind.True or JsonValueKind.False)
            {
                if (showLabel) panel.Children.Clear();
                var toggle = AppearanceControls.Toggle(label, value.GetBoolean()); Name(toggle, label, key);
                toggle.Toggled += (_, _) => Change(path, JsonValue.Create(toggle.IsOn)); panel.Children.Add(toggle);
            }
            else if (value.ValueKind == JsonValueKind.Number)
            {
                var low = field is { } f && f.TryGetProperty("low", out var lo) ? lo.GetDouble() : path[^1] == "threshold" ? 0 : 0;
                var high = field is { } g && g.TryGetProperty("high", out var hi) ? hi.GetDouble() : path[^1] == "threshold" ? 3000 : 100000;
                var step = field is { } h && h.TryGetProperty("step", out var st) ? st.GetDouble() : value.TryGetInt64(out _) ? 1 : .1;
                if (path[^1] is "fixed" or "scaled" && Field(path[..^1]) is { } parent)
                { low = 0; high = parent.GetProperty("high").GetDouble(); step = parent.GetProperty("step").GetDouble(); }
                var initial = value.GetDouble();
                var number = new NumberBox { Value = initial, SmallChange = step, Minimum = Math.Min(low, initial), Maximum = Math.Max(high, initial),
                    ValidationMode = NumberBoxValidationMode.Disabled, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Hidden,
                    MinWidth = 55, HorizontalAlignment = HorizontalAlignment.Stretch };
                Name(number, label, key);
                var edited = false; var focused = false;
                number.GotFocus += (_, _) => focused = true;
                void CommitValue(double next)
                {
                    if ((next < low || next > high) && next != initial) { Error(UiLanguage.Format("ThemeEditorDialog_Fields_MakeField_66FE53367D", low, high)); return; }
                    Error(null); edited = false;
                    Change(path, step >= 1 && value.TryGetInt64(out _) ? JsonValue.Create((long)Math.Round(next)) : JsonValue.Create(next));
                }
                void Commit()
                {
                    if (!edited) return;
                    if (!double.TryParse(number.Text, NumberStyles.Float, CultureInfo.CurrentCulture, out var next) || !double.IsFinite(next))
                    { Error(label + UiLanguage.Text("ThemeEditorDialog_Fields_MakeField_6A2DEA0DF7")); return; }
                    CommitValue(next);
                }
                number.LostFocus += (_, _) => { Commit(); focused = false; };
                number.ValueChanged += (_, args) =>
                {
                    // 模板初始化可能在文本为空时触发 ValueChanged。
                    if (_building || !double.IsFinite(args.NewValue) || args.NewValue == Numeric(Node(path)!)) return;
                    CommitValue(args.NewValue);
                };
                number.RegisterPropertyChangedCallback(NumberBox.TextProperty, (_, _) =>
                {
                    if (_building || !focused) return;
                    edited = !double.TryParse(number.Text, NumberStyles.Float, CultureInfo.CurrentCulture, out var entered) || entered != Numeric(Node(path)!);
                    if (!edited) Error(null);
                    InvalidateInputPreview();
                });
                _checks[key] = Commit; panel.Children.Add(number);
                if (showLabel && field is { } n && n.TryGetProperty("hint", out var hint))
                    panel.Children.Add(new TextBlock { Text = hint.GetString(), FontSize = 12, Opacity = .7, TextWrapping = TextWrapping.Wrap });
            }
            else if (field is { } choicesField && choicesField.TryGetProperty("choices", out var choices) && choices.GetArrayLength() > 0)
            {
                var combo = new ComboBox { HorizontalAlignment = HorizontalAlignment.Stretch };
                foreach (var choice in choices.EnumerateArray())
                {
                    var item = new ComboBoxItem { Content = choice.GetProperty("label").GetString(), Tag = choice.GetProperty("value").GetRawText() };
                    combo.Items.Add(item); if ((string)item.Tag == value.GetRawText()) combo.SelectedItem = item;
                }
                if (combo.SelectedItem is null)
                { var item = new ComboBoxItem { Content = value.ToString() + UiLanguage.Text("ThemeEditorDialog_Fields_MakeField_D9E21007A0"), Tag = value.GetRawText() }; combo.Items.Add(item); combo.SelectedItem = item; }
                Name(combo, label, key);
                combo.SelectionChanged += (_, _) => { if (combo.SelectedItem is ComboBoxItem item) Change(path, JsonNode.Parse((string)item.Tag)); };
                panel.Children.Add(combo);
            }
            else if (value.ValueKind == JsonValueKind.String && (kind == "color" || HexColor.IsMatch(value.GetString()!)))
                panel.Children.Add(ColorField(value.GetString()!, path, label, Error));
            else
            {
                var input = new TextBox { Text = value.ValueKind == JsonValueKind.String ? value.GetString() : value.GetRawText(),
                    AcceptsReturn = path[^1] == "description", TextWrapping = TextWrapping.Wrap, MinHeight = path[^1] == "description" ? 76 : 32 };
                Name(input, label, key); input.LostFocus += (_, _) => Change(path, JsonValue.Create(input.Text));
                _checks[key] = () => Change(path, JsonValue.Create(input.Text)); panel.Children.Add(input);
                input.TextChanged += (_, _) => InvalidateInputPreview();
            }
        }
        panel.Children.Add(error); return panel;
    }
    private FrameworkElement ColorField(string initial, string[] path, string label, Action<string?> error)
    {
        var key = string.Join('.', path);
        var row = new Grid { ColumnSpacing = 6 };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(26) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var hex = new TextBox { Text = initial, FontSize = 12, Padding = new Thickness(5, 4, 5, 4), MinWidth = 0 };
        var precise = new TextBox { Text = initial, Header = "HEX", MaxLength = 9 };
        var swatch = new Button { Width = 26, Height = 32, Padding = new Thickness(3), Background = BrushFor(initial) };
        var picker = new ColorPicker { Color = AppearanceControls.ParseColor(initial), IsAlphaEnabled = initial.Length == 9,
            IsHexInputVisible = false, IsColorSliderVisible = true, IsColorChannelTextInputVisible = false };
        var flyoutBody = new StackPanel { Spacing = 8 }; flyoutBody.Children.Add(picker); flyoutBody.Children.Add(precise);
        swatch.Flyout = new Flyout { Content = flyoutBody };
        Name(hex, label + " HEX", key); Name(precise, label + UiLanguage.Text("ThemeEditorDialog_Fields_ColorField_B5827E9067")); Name(swatch, UiLanguage.Text("ThemeEditorDialog_Fields_ColorField_C11330B852") + label, key + ".picker"); ToolTipService.SetToolTip(swatch, UiLanguage.Text("ThemeEditorDialog_Fields_ColorField_C11330B852") + label);
        var syncing = false;
        void Publish(string text)
        {
            if (!HexColor.IsMatch(text)) { error(UiLanguage.Text("ThemeEditorDialog_Fields_ColorField_1E991AA480")); return; }
            error(null); syncing = true;
            hex.Text = precise.Text = text; swatch.Background = BrushFor(text); picker.Color = AppearanceControls.ParseColor(text);
            syncing = false; Change(path, JsonValue.Create(text));
        }
        picker.ColorChanged += (_, args) =>
        { if (syncing || _building) return; var c = args.NewColor; Publish(initial.Length == 9 ? $"#{c.R:X2}{c.G:X2}{c.B:X2}{c.A:X2}" : $"#{c.R:X2}{c.G:X2}{c.B:X2}"); };
        hex.LostFocus += (_, _) => Publish(hex.Text); precise.LostFocus += (_, _) => Publish(precise.Text);
        hex.TextChanged += (_, _) => { if (!syncing) InvalidateInputPreview(); };
        precise.TextChanged += (_, _) => { if (!syncing) InvalidateInputPreview(); };
        _checks[key] = () => Publish(precise.FocusState != FocusState.Unfocused ? precise.Text : hex.Text);
        row.Children.Add(swatch); Grid.SetColumn(hex, 1); row.Children.Add(hex);
        void Layout() { hex.Visibility = row.ActualWidth is > 0 and < 100 ? Visibility.Collapsed : Visibility.Visible; }
        row.SizeChanged += (_, _) => Layout(); _responsiveUpdates.Add(Layout);
        return row;
    }
    private FrameworkElement PairRow(FrameworkElement first, FrameworkElement second)
    {
        var grid = new Grid { ColumnSpacing = 12, RowSpacing = 8 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.Children.Add(first); grid.Children.Add(second); Grid.SetColumn(second, 1);
        void Layout()
        {
            var narrow = grid.ActualWidth is > 0 and < 330;
            grid.RowDefinitions.Clear(); grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            if (narrow) grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            Grid.SetColumnSpan(first, narrow ? 2 : 1); Grid.SetColumnSpan(second, narrow ? 2 : 1);
            Grid.SetColumn(second, narrow ? 0 : 1); Grid.SetRow(second, narrow ? 1 : 0);
        }
        grid.SizeChanged += (_, _) => Layout(); _responsiveUpdates.Add(Layout); return grid;
    }
    private void BuildTypes(JsonArray array, string[] path, StackPanel panel)
    {
        var choices = Field(path)!.Value.GetProperty("choices");
        var values = array.Select(node => node!.ToJsonString()).ToArray();
        var entries = new List<(CheckBox Check, string Raw)>();
        foreach (var option in choices.EnumerateArray()) Add(option.GetProperty("label").GetString()!, option.GetProperty("value").GetRawText());
        foreach (var value in values.Where(raw => entries.All(entry => entry.Raw != raw))) Add(JsonNode.Parse(value)!.ToString() + UiLanguage.Text("ThemeEditorDialog_Fields_BuildTypes_F2CF29F7F4"), value);
        void Add(string name, string raw)
        {
            var check = new CheckBox { Content = name, IsChecked = values.Contains(raw), FontSize = 14 }; Name(check, name, string.Join('.', path) + "." + JsonNode.Parse(raw));
            entries.Add((check, raw)); panel.Children.Add(check);
            check.Click += (_, _) =>
            {
                var selected = entries.Where(entry => entry.Check.IsChecked == true).Select(entry => entry.Raw).ToHashSet();
                var next = new JsonArray();
                foreach (var current in values.Where(selected.Contains)) next.Add(JsonNode.Parse(current));
                foreach (var current in entries.Select(entry => entry.Raw).Where(raw => selected.Contains(raw) && !values.Contains(raw))) next.Add(JsonNode.Parse(current));
                Change(path, next);
            };
        }
    }
    private void BuildList(JsonArray array, string[] path, StackPanel panel, string? kind)
    {
        for (var i = 0; i < array.Count; i++)
        {
            var index = i; var item = array[i]!;
            var row = new Grid { ColumnSpacing = 8 };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            row.Children.Add(MakeField(item, path.Append(i.ToString()).ToArray(), Label(path) + " " + (i + 1), showLabel: false));
            var remove = new Button { Content = "×", Width = 32, Padding = new Thickness(0) }; Name(remove, UiLanguage.Text("ThemeEditorDialog_Fields_BuildList_2F9DAA8289") + Label(path) + " " + (i + 1));
            AutomationProperties.SetAutomationId(remove, string.Join('.', path) + ".remove." + i);
            remove.Click += (_, _) => { if (!FlushInputs()) return; var next = (JsonArray)Node(path)!.DeepClone(); next.RemoveAt(index); Change(path, next); RebuildFields(); };
            Grid.SetColumn(remove, 1); row.Children.Add(remove); panel.Children.Add(row);
        }
        var add = new Button { Content = UiLanguage.Text("ThemeEditorDialog_Fields_BuildList_45CF7C95BF") + Label(path) };
        Name(add, UiLanguage.Text("ThemeEditorDialog_Fields_BuildList_7A8A11EAD5") + Label(path), string.Join('.', path) + ".add");
        add.Click += async (_, _) =>
        {
            if (!FlushInputs()) return;
            var input = new TextBox { Header = kind == "colors" ? UiLanguage.Text("ThemeEditorDialog_Fields_BuildList_AA2CA3DEE7") : kind == "numbers" ? UiLanguage.Text("ThemeEditorDialog_Fields_BuildList_C5500A58E0") : UiLanguage.Text("ThemeEditorDialog_Fields_BuildList_CDA1D55C52") };
            var message = new TextBlock { TextWrapping = TextWrapping.Wrap }; var body = new StackPanel { Spacing = 8 }; body.Children.Add(input); body.Children.Add(message);
            var dialog = new ContentDialog { XamlRoot = _window.Root.XamlRoot, Title = UiLanguage.Text("ThemeEditorDialog_Fields_BuildList_7A8A11EAD5") + Label(path), Content = body, PrimaryButtonText = UiLanguage.Text("ThemeEditorDialog_Fields_BuildList_7A8A11EAD5"), CloseButtonText = UiLanguage.Text("ThemeEditorDialog_Fields_BuildList_2CD0F3BE87") };
            JsonNode? added = null;
            dialog.PrimaryButtonClick += (_, args) =>
            {
                if (kind == "colors" && !HexColor.IsMatch(input.Text)) { message.Text = UiLanguage.Text("ThemeEditorDialog_Fields_BuildList_ECFAA25904"); args.Cancel = true; }
                else if (kind == "numbers")
                { if (!double.TryParse(input.Text, out var n) || !double.IsFinite(n)) { message.Text = UiLanguage.Text("ThemeEditorDialog_Fields_BuildList_D1E6C6F018"); args.Cancel = true; } else added = JsonValue.Create(n); }
                else added = JsonValue.Create(input.Text);
            };
            if (await dialog.ShowAsync() == ContentDialogResult.Primary && added is not null)
            { var next = (JsonArray)Node(path)!.DeepClone(); next.Add(added); Change(path, next); RebuildFields(); }
        };
        panel.Children.Add(add);
    }
    private JsonNode? Node(string[] path)
    { JsonNode? value = _working; foreach (var part in path) value = value is JsonArray array ? array[int.Parse(part)] : value?[part]; return value; }
    private void InvalidateInputPreview()
    {
        if (_building || _flushingPreviewInputs) return;
        CancelPreview(); _sampleCancellation?.Cancel(); _sampleRevision++;
        _previewStatus.Text = UiLanguage.Text("ThemeEditorDialog_Fields_InvalidateInputPreview_F0E4729FC1");
        SchedulePreview();
    }
}
