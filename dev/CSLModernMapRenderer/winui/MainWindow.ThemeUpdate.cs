using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.UI;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private async Task ShowThemeUpdateAsync()
    {
        if (!_snapshot.TryGetProperty("themeUpdate", out var notice) || notice.ValueKind != JsonValueKind.Object) return;
        var body = new StackPanel { Spacing = 16, MaxWidth = 460 };
        body.Children.Add(new TextBlock { Text = string.Format(UiLanguage.Text("ThemeUpdate_Intro"), notice.GetProperty("name").GetString()), TextWrapping = TextWrapping.Wrap });
        var changes = notice.GetProperty("changes").EnumerateArray().ToArray();
        foreach (var change in changes.Take(3)) body.Children.Add(ThemeChangeRow(change));
        if (changes.Length > 3)
        {
            var rest = new StackPanel { Spacing = 14 };
            foreach (var change in changes.Skip(3)) rest.Children.Add(ThemeChangeRow(change));
            body.Children.Add(new Expander { Header = UiLanguage.Text("ThemeUpdate_AllChanges"), Content = rest, HorizontalAlignment = HorizontalAlignment.Stretch });
        }
        var preserved = notice.GetProperty("preserved").EnumerateArray().ToArray();
        if (preserved.Length > 0)
        {
            var kept = new StackPanel { Spacing = 8 };
            kept.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeUpdate_Kept"), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
            foreach (var item in preserved.Take(3))
            {
                var line = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
                line.Children.Add(ThemeUpdateValue(item.GetProperty("after"), item.GetProperty("afterLabel")));
                line.Children.Add(new TextBlock { Text = item.GetProperty("label").GetString() ?? UiLanguage.Text("ThemeUpdate_Custom"), TextWrapping = TextWrapping.Wrap, MaxWidth = 320, VerticalAlignment = VerticalAlignment.Center });
                kept.Children.Add(line);
            }
            if (preserved.Length > 3) kept.Children.Add(new TextBlock { Text = string.Format(UiLanguage.Text("ThemeUpdate_MoreKept"), preserved.Length - 3), TextWrapping = TextWrapping.Wrap });
            body.Children.Add(kept);
        }
        body.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeUpdate_Archived"), TextWrapping = TextWrapping.Wrap });
        var dialog = new ContentDialog
        {
            XamlRoot = Root.XamlRoot, Title = UiLanguage.Text("ThemeUpdate_Title"),
            Content = new ScrollViewer { Content = body, MaxHeight = 480, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled },
            PrimaryButtonText = UiLanguage.Text("ThemeUpdate_Continue"), CloseButtonText = UiLanguage.Text("ThemeUpdate_UsePrevious"),
            DefaultButton = ContentDialogButton.Primary,
        };
        // Esc 继续使用已合并的主题；只有点“切换旧版”才切换。
        var usePrevious = false;
        dialog.CloseButtonClick += (_, _) => usePrevious = true;
        await dialog.ShowAsync();
        await _engine.RequestAsync("acknowledgeThemeUpdate", new { usePrevious });
        await RefreshSnapshotAsync();
        if (usePrevious && _snapshot.GetProperty("hasDocument").GetBoolean()) await RefreshLivePreviewAsync();
    }

    private static StackPanel ThemeChangeRow(JsonElement change)
    {
        var row = new StackPanel { Spacing = 6 };
        row.Children.Add(new TextBlock { Text = change.GetProperty("label").GetString() ?? UiLanguage.Text("ThemeUpdate_Custom"), TextWrapping = TextWrapping.Wrap });
        var values = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        values.Children.Add(ThemeUpdateValue(change.GetProperty("before"), change.GetProperty("beforeLabel")));
        values.Children.Add(new FontIcon { Glyph = "\uE72A", FontSize = 12, VerticalAlignment = VerticalAlignment.Center });
        values.Children.Add(ThemeUpdateValue(change.GetProperty("after"), change.GetProperty("afterLabel")));
        row.Children.Add(values);
        if (change.GetProperty("conflict").GetBoolean())
            row.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeUpdate_Conflict"), FontSize = 12, TextWrapping = TextWrapping.Wrap });
        return row;
    }

    private static FrameworkElement ThemeUpdateValue(JsonElement value, JsonElement label)
    {
        var panel = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, MaxWidth = 195 };
        if (value.ValueKind == JsonValueKind.String && value.GetString() is { } color && ThemeUpdateColor(color) is { } brush)
        {
            panel.Children.Add(new Border { Width = 24, Height = 18, CornerRadius = new CornerRadius(3), Background = brush });
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(panel, color);
            return panel;
        }
        if (value.ValueKind == JsonValueKind.Array && value.GetArrayLength() > 0 && value[0].ValueKind == JsonValueKind.Object && value[0].TryGetProperty("color", out _))
        {
            var ramp = new StackPanel { Orientation = Orientation.Horizontal };
            foreach (var stop in value.EnumerateArray())
                ramp.Children.Add(new Border { Width = 100.0 / value.GetArrayLength(), Height = 18, Background = ThemeUpdateColor(stop.GetProperty("color").GetString()!) });
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(ramp, UiLanguage.Text("ThemeUpdate_ColorRamp"));
            return ramp;
        }
        var text = label.ValueKind == JsonValueKind.String ? label.GetString() : value.ValueKind switch
        {
            JsonValueKind.Null => UiLanguage.Text("ThemeUpdate_Unset"),
            JsonValueKind.True => UiLanguage.Text("ThemeUpdate_On"),
            JsonValueKind.False => UiLanguage.Text("ThemeUpdate_Off"),
            JsonValueKind.Object or JsonValueKind.Array => UiLanguage.Text("ThemeUpdate_Custom"),
            _ => value.ToString(),
        };
        panel.Children.Add(new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, MaxWidth = 190 });
        return panel;
    }

    private static SolidColorBrush? ThemeUpdateColor(string text)
    {
        if (text.Length is not (7 or 9) || text[0] != '#' || !uint.TryParse(text.AsSpan(1), System.Globalization.NumberStyles.HexNumber, null, out var hex)) return null;
        return new SolidColorBrush(text.Length == 7
            ? Color.FromArgb(255, (byte)(hex >> 16), (byte)(hex >> 8), (byte)hex)
            : Color.FromArgb((byte)hex, (byte)(hex >> 24), (byte)(hex >> 16), (byte)(hex >> 8)));
    }
}
