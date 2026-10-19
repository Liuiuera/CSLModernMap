// 显示对象属性并提供样式编辑入口。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace CSLModernMapWinUI;

// 类型化检查器不把配置键变成可编辑文本框。
internal sealed class ObjectInspectorPanel : StackPanel
{
    private JsonElement _model;
    private readonly Func<string, string, object?, string, Task<JsonElement>> _write;
    private readonly Func<bool> _current;
    private readonly Action<string> _openTheme;
    private readonly Action _clear;
    private readonly Action _locate;
    private readonly Dictionary<string, bool> _expanded;
    private readonly List<Control> _editors = new();
    private readonly TextBlock _status = new() { TextWrapping = TextWrapping.Wrap };
    private bool _syncing, _busy, _available = true;
    private int _renderGeneration;
    internal JsonElement Model => _model;

    internal ObjectInspectorPanel(JsonElement model,
        Func<string, string, object?, string, Task<JsonElement>> write,
        Func<bool> current, Action<string> openTheme, Action clear, Action locate,
        Dictionary<string, bool>? expansionState = null)
    {
        _model = model.Clone(); _write = write; _current = current;
        _openTheme = openTheme; _clear = clear; _locate = locate;
        _expanded = expansionState ?? new();
        Spacing = 8;
        Loaded += (_, _) => Children.OfType<FrameworkElement>().FirstOrDefault()?.StartBringIntoView();
        Render();
    }

    private static TextBlock Note(string value) => new()
    { Text = value, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };


    private void Status(string message)
    {
        _status.Text = message;
        _status.Visibility = string.IsNullOrEmpty(message) ? Visibility.Collapsed : Visibility.Visible;
    }

    internal void Refresh(JsonElement model)
    {
        if (_busy || !_current()) return;
        _model = model.Clone(); Render();
    }

    internal void SetAvailable(bool enabled)
    {
        _available = enabled;
        foreach (var editor in _editors)
            editor.IsEnabled = enabled && !_busy && editor.Tag is not false;
    }

    private void Render()
    {
        _renderGeneration++;
        _syncing = true;
        try
        {
            Children.Clear(); _editors.Clear();
            Children.Add(new TextBlock { Text = _model.GetProperty("title").GetString(), FontSize = 20,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap,
                IsTextSelectionEnabled = true });
            Children.Add(Note(_model.GetProperty("typeLabel").GetString()!));
            var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            var locate = new Button { Content = UiLanguage.Text("ObjectInspectorPanel_Render_EEF4D23EB3") };
            locate.Click += (_, _) => { if (_current()) _locate(); };
            var clear = new Button { Content = UiLanguage.Text("ObjectInspectorPanel_Render_74966E20DF") };
            clear.Click += (_, _) => { if (_current()) _clear(); };
            actions.Children.Add(locate); actions.Children.Add(clear); Children.Add(actions);
            var history = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            foreach (var (key, label, property) in new[] { ("undoInspector", UiLanguage.Text("ObjectInspectorPanel_Render_926A50B98E"), "canUndo"), ("redoInspector", UiLanguage.Text("ObjectInspectorPanel_Render_03717B6F10"), "canRedo") })
            {
                var available = _model.TryGetProperty(property, out var can) && can.GetBoolean();
                var button = new Button { Content = label, Tag = available, IsEnabled = available };
                button.Click += async (_, _) => await CommitAsync(key, "", null);
                _editors.Add(button); history.Children.Add(button);
            }
            Children.Add(history);
            foreach (var fact in _model.GetProperty("summary").EnumerateArray())
            {
                var value = fact.GetProperty("value").GetString()!;
                if (fact.TryGetProperty("kind", out var factKind) && factKind.GetString() == "color" && value.StartsWith('#'))
                {
                    var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
                    row.Children.Add(Note(fact.GetProperty("label").GetString() + "："));
                    var swatch = new Border { Width = 32, Height = 18, Background = new SolidColorBrush(AppearanceControls.ParseColor(value)), BorderThickness = new Thickness(1), BorderBrush = new SolidColorBrush(Microsoft.UI.Colors.Gray) };
                    ToolTipService.SetToolTip(swatch, value);
                    AutomationProperties.SetName(swatch, value);
                    row.Children.Add(swatch); Children.Add(row);
                }
                else Children.Add(Note(fact.GetProperty("label").GetString() + "：" + value));
            }
            foreach (var notice in _model.GetProperty("notices").EnumerateArray()) Children.Add(Note(notice.GetString()!));
            Status(""); Children.Add(_status);
            foreach (var section in _model.GetProperty("sections").EnumerateArray())
            {
                var key = section.GetProperty("key").GetString()!;
                var body = new StackPanel { Spacing = 8 };
                body.Children.Add(Note(section.GetProperty("scope").GetString()!));
                foreach (var field in section.GetProperty("fields").EnumerateArray()) AddField(body, field);
                if (section.TryGetProperty("canReset", out var canReset) && canReset.GetBoolean())
                {
                    var reset = new Button { Content = UiLanguage.Text("ObjectInspectorPanel_Render_A4B438D7DA") };
                    ToolTipService.SetToolTip(reset, UiLanguage.Text("ObjectInspectorPanel_Render_9576B834B5"));
                    _editors.Add(reset);
                    reset.Click += async (_, _) => await CommitAsync("resetInspectorGroup", key, null);
                    body.Children.Add(reset);
                }
                AddSection(key, section.GetProperty("title").GetString()!, body,
                    section.GetProperty("expanded").GetBoolean());
            }
            if (_model.GetProperty("sections").GetArrayLength() > 0)
            {
                var complete = new Button { Content = UiLanguage.Text("ObjectInspectorPanel_Render_33A982C362"), HorizontalAlignment = HorizontalAlignment.Stretch };
                complete.Click += (_, _) => { if (_current()) _openTheme(_model.GetProperty("themeSection").GetString()!); };
                Children.Add(complete);
            }
            AddFacts("details", UiLanguage.Text("ObjectInspectorPanel_Render_B931CFD44E"));
            AddFacts("technical", UiLanguage.Text("ObjectInspectorPanel_Render_2CCACDED44"));
        }
        finally { _syncing = false; }
    }

    private void AddSection(string key, string label, UIElement body, bool expanded)
    {
        var section = WorkspaceSection.Create(label, body, _expanded.GetValueOrDefault(key, expanded));
        section.Expanding += (_, _) => _expanded[key] = true;
        section.Collapsed += (_, _) => _expanded[key] = false;
        Children.Add(section);
    }

    private void AddFacts(string key, string title)
    {
        var facts = _model.GetProperty(key);
        if (facts.GetArrayLength() == 0) return;
        var body = new StackPanel { Spacing = 6 };
        foreach (var fact in facts.EnumerateArray())
            body.Children.Add(Note(fact.GetProperty("label").GetString() + "：" + fact.GetProperty("value").GetString()));
        AddSection(key, title + $" ({facts.GetArrayLength()})", body, false);
    }

    private async Task CommitAsync(string action, string key, object? value)
    {
        if (_syncing || _busy || !_current()) return;
        _busy = true;
        foreach (var editor in _editors) editor.IsEnabled = false;
        try
        {
            var result = await _write(action, key, value, _model.GetProperty("settingsRevision").GetString()!);
            if (!_current()) return;
            _model = result.Clone(); Render();
            Status(UiLanguage.Text("ObjectInspectorPanel_CommitAsync_7A3DBE6E2C"));
        }
        catch (Exception error)
        {
            if (!_current()) return;
            // 冲突后从当前设置刷新，而不是重试过期输入。
            try { _model = (await _write("getObjectInspector", "", null, "")).Clone(); Render(); }
            catch { /* 保留最后可读的模型；原始错误已显示。 */ }
            Status(error.Message);
        }
        finally { _busy = false; if (_current()) SetAvailable(_available); }
    }

    private void AddField(StackPanel body, JsonElement field)
    {
        var generation = _renderGeneration;
        var key = field.GetProperty("key").GetString()!;
        Task SaveField(object value) => generation == _renderGeneration
            ? CommitAsync("setInspectorField", key, value) : Task.CompletedTask;
        var label = field.GetProperty("label").GetString()!;
        var kind = field.GetProperty("kind").GetString();
        var value = field.GetProperty("value");
        var unit = field.TryGetProperty("unit", out var rawUnit) ? rawUnit.GetString() : "";
        var header = label + (string.IsNullOrEmpty(unit) ? "" : $" ({unit})");
        Control editor;
        if (kind == "color")
        {
            body.Children.Add(Note(label));
            var original = AppearanceControls.ParseColor(value.GetString()!);
            var picker = new ColorPicker { Color = original, IsAlphaEnabled = true,
                IsColorSliderVisible = true, IsHexInputVisible = true, MaxWidth = 280 };
            var swatch = new Border { Width = 38, Height = 22, CornerRadius = new CornerRadius(3),
                Background = new SolidColorBrush(original), BorderThickness = new Thickness(1),
                BorderBrush = new SolidColorBrush(Microsoft.UI.Colors.Gray) };
            var content = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
            content.Children.Add(swatch); content.Children.Add(Note(UiLanguage.Text("ObjectInspectorPanel_AddField_A6A4FC849E")));
            var flyout = new Flyout { Content = picker };
            var button = new Button { Content = content, Flyout = flyout, HorizontalAlignment = HorizontalAlignment.Stretch };
            AutomationProperties.SetName(button, label + ": " + value.GetString());
            flyout.Closed += async (_, _) =>
            {
                var color = picker.Color;
                if (color != original)
                    await SaveField($"#{color.R:X2}{color.G:X2}{color.B:X2}" + (color.A == 255 ? "" : $"{color.A:X2}"));
            };
            editor = button;
        }
        else if (kind == "toggle")
        {
            var toggle = AppearanceControls.Toggle(label, value.GetBoolean());
            toggle.Toggled += async (_, _) => await SaveField(toggle.IsOn);
            editor = toggle;
        }
        else if (kind == "number" && unit == "%")
        {
            var current = value.GetDouble();
            var slider = new Slider { Header = header, Minimum = field.GetProperty("low").GetDouble(),
                Maximum = field.GetProperty("high").GetDouble(), Value = current, StepFrequency = 1 };
            slider.Header = label + $" · {current:0}%";
            slider.ValueChanged += (_, _) => slider.Header = label + $" · {slider.Value:0}%";
            // 在松开或失去焦点时提交，不在拖动每一步提交。
            async Task Save() { if (slider.Value != current) await SaveField(slider.Value); }
            slider.PointerCaptureLost += async (_, _) => await Save();
            slider.LostFocus += async (_, _) => await Save();
            slider.KeyUp += async (_, args) => { if (args.Key is Windows.System.VirtualKey.Left or Windows.System.VirtualKey.Right or Windows.System.VirtualKey.Up or Windows.System.VirtualKey.Down) await Save(); };
            editor = slider;
        }
        else if (kind == "number")
        {
            var current = value.GetDouble();
            var number = new NumberBox { Header = header, Value = current,
                Minimum = field.GetProperty("low").GetDouble(), Maximum = field.GetProperty("high").GetDouble(),
                SmallChange = unit == "px" ? .5 : 1, SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
            async Task Save()
            {
                if (!double.IsFinite(number.Value)) { Status(UiLanguage.Text("ObjectInspectorPanel_AddField_44DEE0CA2E")); return; }
                if (number.Value != current) await SaveField(number.Value);
            }
            var delay = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(250) };
            delay.Tick += async (_, _) => { delay.Stop(); await Save(); };
            number.ValueChanged += (_, _) => { if (!_syncing) { delay.Stop(); delay.Start(); } };
            number.LostFocus += async (_, _) => await Save();
            number.KeyUp += async (_, args) => { if (args.Key == Windows.System.VirtualKey.Enter) await Save(); };
            editor = number;
        }
        else if (kind is "choice" or "font")
        {
            var combo = new ComboBox { Header = label, HorizontalAlignment = HorizontalAlignment.Stretch };
            var choices = kind == "font" ? _model.GetProperty("fonts") : field.GetProperty("choices");
            foreach (var choice in choices.EnumerateArray())
            {
                var item = new ComboBoxItem { Content = choice.GetProperty("label").GetString(), Tag = choice.GetProperty("value").Clone() };
                if (kind == "font") item.FontFamily = new FontFamily(choice.GetProperty("value").GetString()!);
                combo.Items.Add(item);
                if (((JsonElement)item.Tag).ToString() == value.ToString()) combo.SelectedItem = item;
            }
            if (combo.SelectedItem is null && kind == "font")
            {
                var item = new ComboBoxItem { Content = value.GetString(), Tag = value.Clone() };
                combo.Items.Add(item); combo.SelectedItem = item;
            }
            combo.SelectionChanged += async (_, _) =>
            { if (combo.SelectedItem is ComboBoxItem item) await SaveField(((JsonElement)item.Tag).Clone()); };
            editor = combo;
        }
        else
        {
            body.Children.Add(Note(label + " · " + UiLanguage.Text("ObjectInspectorPanel_AddField_E804B20C40")));
            return;
        }
        editor.Tag = field.GetProperty("enabled").GetBoolean();
        editor.IsEnabled = editor.Tag is not false;
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(editor, key); _editors.Add(editor); body.Children.Add(editor);
    }
}
