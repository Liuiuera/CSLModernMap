// 构建和更新外观设置控件。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Automation;

namespace CSLModernMapWinUI;

internal static class AppearanceControls
{
    internal static ToggleSwitch Toggle(string title, bool isOn = false) => new()
    {
        Header = title, IsOn = isOn,
        OnContent = UiLanguage.Text("AppearanceControls_Toggle_On"),
        OffContent = UiLanguage.Text("AppearanceControls_Toggle_Off"),
    };

    internal static Windows.UI.Color ParseColor(string value)
    {
        var r = Convert.ToByte(value.Substring(1, 2), 16);
        var g = Convert.ToByte(value.Substring(3, 2), 16);
        var b = Convert.ToByte(value.Substring(5, 2), 16);
        var a = value.Length == 9 ? Convert.ToByte(value.Substring(7, 2), 16) : (byte)255;
        return Microsoft.UI.ColorHelper.FromArgb(a, r, g, b);
    }

    internal static StackPanel ColorContent(string value)
    {
        var panel = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        panel.Children.Add(new Border { Width = 22, Height = 22, CornerRadius = new CornerRadius(4),
            Background = new SolidColorBrush(AppearanceControls.ParseColor(value)) });
        panel.Children.Add(new TextBlock { Text = value, VerticalAlignment = VerticalAlignment.Center });
        return panel;
    }

    internal static FrameworkElement Create(JsonElement field, JsonElement fonts, JsonNode value,
        Action<JsonNode> changed, List<Action> checks, string title, bool compact = false,
        double displayMultiplier = 1)
    {
        var kind = field.GetProperty("kind").GetString();
        if (kind == "toggle")
        {
            var toggle = Toggle(title, value.GetValue<bool>());
            toggle.Toggled += (_, _) => changed(JsonValue.Create(toggle.IsOn)!);
            return toggle;
        }
        var panel = new StackPanel { Spacing = 4 };
        if (kind == "color")
        {
            panel.Children.Add(new TextBlock { Text = title });
            var picker = new ColorPicker { Color = AppearanceControls.ParseColor(value.GetValue<string>()), IsAlphaEnabled = false };
            var flyout = new Flyout { Content = picker };
            var button = new Button { Content = ColorContent(value.GetValue<string>()), Flyout = flyout };
            void UpdateColor()
            {
                var next = $"#{picker.Color.R:X2}{picker.Color.G:X2}{picker.Color.B:X2}";
                button.Content = ColorContent(next); changed(JsonValue.Create(next)!);
            }
            if (compact) picker.ColorChanged += (_, _) => UpdateColor();
            else flyout.Closed += (_, _) => UpdateColor();
            panel.Children.Add(button); return panel;
        }
        if (kind == "number")
        {
            if (compact) return Number(title, value, field.GetProperty("low").GetDouble(),
                field.GetProperty("high").GetDouble(), changed, checks, displayMultiplier);
            var baseline = value.GetValue<double>();
            var low = field.GetProperty("low").GetDouble(); var high = field.GetProperty("high").GetDouble();
            var number = new NumberBox { Header = title, Value = baseline, Minimum = Math.Min(low, baseline),
                Maximum = Math.Max(high, baseline), SmallChange = high < 1 ? .01 : 1,
                SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
            var presets = new ComboBox { PlaceholderText = UiLanguage.Text("AppearanceControls_Create_31F55AB9BD"), HorizontalAlignment = HorizontalAlignment.Stretch };
            foreach (var preset in (high < 1 ? new double[] { 0, .08, .12, .18 } : high <= 12 ? new double[] { 0, 1, 2, 3, 4 } : new double[] { 8, 10, 12, 14, 16, 18, 20, 24, 32 }))
                if (preset >= low && preset <= high) presets.Items.Add(preset);
            presets.SelectionChanged += (_, _) => { if (presets.SelectedItem is double next) number.Value = next; };
            number.ValueChanged += (_, _) =>
            {
                if (number.Value == baseline) changed(value.DeepClone());
                else if (double.IsFinite(number.Value) && number.Value >= low && number.Value <= high)
                    changed(JsonValue.Create(number.Value)!);
            };
            checks.Add(() =>
            {
                if (number.Value == baseline) return;
                if (!double.IsFinite(number.Value) || number.Value < low || number.Value > high)
                    throw new InvalidOperationException(UiLanguage.Format("AppearanceControls_Create_0BB1EFDA65", title, low, high));
                changed(JsonValue.Create(number.Value)!);
            });
            panel.Children.Add(number); panel.Children.Add(presets); return panel;
        }
        var combo = new ComboBox { Header = title, HorizontalAlignment = HorizontalAlignment.Stretch };
        foreach (var item in (kind == "font" ? fonts : field.GetProperty("choices")).EnumerateArray())
        {
            var option = new ComboBoxItem { Content = item.GetProperty("label").GetString(), Tag = JsonNode.Parse(item.GetProperty("value").GetRawText())! };
            combo.Items.Add(option);
            if (JsonNode.DeepEquals((JsonNode)option.Tag, value)) combo.SelectedItem = option;
        }
        if (combo.SelectedItem is null)
        {
            var option = new ComboBoxItem { Content = value.ToString(), Tag = value.DeepClone() };
            combo.Items.Add(option); combo.SelectedItem = option;
        }
        var preview = new TextBlock { Text = UiLanguage.Text("AppearanceControls_Create_C1FFF780E0"), TextWrapping = TextWrapping.Wrap, FontSize = 16 };
        void UpdateFont() { if (kind == "font") preview.FontFamily = new FontFamily(((JsonNode)((ComboBoxItem)combo.SelectedItem).Tag).GetValue<string>()); }
        UpdateFont();
        combo.SelectionChanged += (_, _) =>
        {
            if (combo.SelectedItem is not ComboBoxItem item) return;
            changed(((JsonNode)item.Tag).DeepClone()); UpdateFont();
        };
        panel.Children.Add(combo); if (kind == "font" && !compact) panel.Children.Add(preview); return panel;
    }

    internal static Control Number(string title, JsonNode value, double low, double high,
        Action<JsonNode> changed, List<Action> checks, double displayMultiplier = 1)
    {
        var initial = value.GetValue<double>();
        var number = new NumberBox { Header = title, Value = initial * displayMultiplier,
            Minimum = low * displayMultiplier, Maximum = high * displayMultiplier, SmallChange = .1,
            ValidationMode = NumberBoxValidationMode.Disabled,
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
        double Read()
        {
            if (!double.TryParse(number.Text, out var entered) || !double.IsFinite(entered))
                throw new InvalidOperationException(UiLanguage.Text("Pixel_InvalidScale"));
            var result = entered / displayMultiplier;
            if (result < low || result > high)
                throw new InvalidOperationException(UiLanguage.Format("AppearanceControls_Number_0BB1EFDA65", title, low, high));
            return result;
        }
        number.ValueChanged += (_, _) => {
            var next = number.Value / displayMultiplier;
            if (double.IsFinite(next) && next >= low && next <= high) changed(JsonValue.Create(next)!);
        };
        checks.Add(() => changed(JsonValue.Create(Read())!));
        return number;
    }

}

internal sealed class AppearanceFieldGrid : Grid
{
    internal AppearanceFieldGrid(params FrameworkElement[] fields)
    {
        ColumnSpacing = 16; RowSpacing = 12;
        ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        foreach (var field in fields) Children.Add(field);
        SizeChanged += (_, _) => ArrangeFields();
        ArrangeFields();
    }

    private void ArrangeFields()
    {
        var columns = ActualWidth >= 460 ? 2 : 1;
        ColumnDefinitions[1].Width = columns == 2 ? new GridLength(1, GridUnitType.Star) : new GridLength(0);
        var rows = (Children.Count + columns - 1) / columns;
        while (RowDefinitions.Count < rows) RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        while (RowDefinitions.Count > rows) RowDefinitions.RemoveAt(RowDefinitions.Count - 1);
        for (var index = 0; index < Children.Count; index++)
        {
            var field = (FrameworkElement)Children[index];
            Grid.SetRow(field, index / columns); Grid.SetColumn(field, index % columns);
        }
    }
}
