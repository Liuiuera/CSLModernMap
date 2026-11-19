using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Windows.UI;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private EditorWindow? _presetUpdateWindow;

    internal async Task<bool> ShowThemeUpdateAsync(bool includeHandled = false, string? theme = null)
    {
        if (_presetUpdateWindow is not null) return false;
        JsonElement notice;
        if (includeHandled)
        {
            var result = await _engine.RequestAsync("getThemeUpdate", new { theme });
            notice = result.GetProperty("notice");
        }
        else if (!_snapshot.TryGetProperty("themeUpdate", out notice)) return false;
        if (notice.ValueKind != JsonValueKind.Object)
        {
            if (includeHandled) ShowNotice(UiLanguage.Text("ThemeUpdate_None"));
            return false;
        }
        var epoch = _snapshot.GetProperty("documentEpoch").GetInt32();
        var window = _presetUpdateWindow = new EditorWindow(UiLanguage.Text("ThemeUpdate_Title"), "refresh", 820, 630,
            translucent: true, owner: this, modal: true) { ActionsRightAligned = true };
        var body = new Grid { RowSpacing = 12 };
        body.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        body.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        body.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var heading = ExportDesign.Group(UiLanguage.Text("ThemeUpdate_Title"));
        ((TextBlock)heading.Children[0]).FontSize = 22;
        heading.Children.Add(ExportDesign.Text(
            string.Format(UiLanguage.Text("ThemeUpdate_Intro"), notice.GetProperty("name").GetString())));
        var release = ExportDesign.Text($"{notice.GetProperty("fromVersion").GetString()} → {notice.GetProperty("toVersion").GetString()}");
        ExportDesign.Secondary(release);
        heading.Children.Add(release);
        body.Children.Add(heading);
        var groups = new StackPanel { Spacing = 10 };
        foreach (var group in notice.GetProperty("changes").EnumerateArray().GroupBy(change => change.GetProperty("section").GetString()))
            groups.Children.Add(ThemeChangeGroup(group.Key!, group));
        var list = new ScrollViewer { Content = groups, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
            HorizontalContentAlignment = HorizontalAlignment.Stretch };
        Grid.SetRow(list, 1);
        body.Children.Add(list);
        var note = ExportDesign.Text(UiLanguage.Text("ThemeUpdate_Archived"));
        ExportDesign.Secondary(note);
        var error = new InfoBar { Severity = InfoBarSeverity.Error, IsClosable = false };
        var footer = new StackPanel { Spacing = 8 };
        footer.Children.Add(note);
        footer.Children.Add(error);
        Grid.SetRow(footer, 2);
        body.Children.Add(footer);
        window.SetBody(body);
        var decided = false;
        var applied = false;
        var deciding = false;
        async Task Decide(bool apply)
        {
            deciding = true;
            try
            {
                await _engine.RequestAsync("acknowledgeThemeUpdate", new {
                    apply, theme = notice.GetProperty("theme").GetString(),
                    revision = notice.GetProperty("revision").GetString(),
                    sourceDigest = notice.GetProperty("sourceDigest").GetString(), documentEpoch = epoch });
                decided = true; applied = apply;
            }
            finally { deciding = false; }
        }
        window.CanCloseAsync = async () => {
            if (decided) return true;
            if (deciding) return false;
            try { await Decide(false); return true; }
            catch (InvalidOperationException exception) { ShowNotice(exception.Message); return true; }
        };
        window.AddAction(UiLanguage.Text("ThemeUpdate_UsePrevious"), async () => await window.RequestCloseAsync());
        window.AddAction(UiLanguage.Text("ThemeUpdate_Continue"), async () => {
            if (deciding) return;
            try { await Decide(true); window.ForceClose(); }
            catch (InvalidOperationException exception) { error.Message = exception.Message; error.IsOpen = true; }
        }).Style = (Style)Application.Current.Resources["AccentButtonStyle"];
        try { await window.ShowAsync(); }
        finally { _presetUpdateWindow = null; }
        await RefreshSnapshotAsync();
        if (applied && _snapshot.GetProperty("hasDocument").GetBoolean()) await RefreshLivePreviewAsync();
        return applied;
    }

    private static FrameworkElement ThemeChangeGroup(string title, IEnumerable<JsonElement> changes)
    {
        var section = new StackPanel { Spacing = 8 };
        var name = ExportDesign.Text(title, heading: true);
        AutomationProperties.SetHeadingLevel(name, AutomationHeadingLevel.Level2);
        var current = ExportDesign.Text(UiLanguage.Text("ThemeUpdate_Current"));
        var updated = ExportDesign.Text(UiLanguage.Text("ThemeUpdate_Preset"));
        ExportDesign.Secondary(current);
        ExportDesign.Secondary(updated);
        section.Children.Add(ThemeComparisonRow(name, current, updated));
        foreach (var change in changes)
        {
            var label = new StackPanel { Spacing = 3 };
            label.Children.Add(ExportDesign.Text(change.GetProperty("label").GetString()!));
            if (change.GetProperty("conflict").GetBoolean())
            {
                var conflict = ExportDesign.Text(UiLanguage.Text("ThemeUpdate_Conflict"));
                ExportDesign.Secondary(conflict);
                label.Children.Add(conflict);
            }
            var row = ThemeComparisonRow(label, ThemeUpdateValue(change.GetProperty("before"), change.GetProperty("beforeLabel")),
                ThemeUpdateValue(change.GetProperty("after"), change.GetProperty("afterLabel")));
            var divider = ExportDesign.Divider(row);
            divider.Padding = new Thickness(0, 7, 0, 0);
            section.Children.Add(divider);
        }
        return ExportDesign.Card(section);
    }

    private static Grid ThemeComparisonRow(FrameworkElement label, FrameworkElement current, FrameworkElement updated)
    {
        var row = new Grid { ColumnSpacing = 12 };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1.4, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        Grid.SetColumn(current, 1);
        Grid.SetColumn(updated, 2);
        row.Children.Add(label);
        row.Children.Add(current);
        row.Children.Add(updated);
        return row;
    }

    private static FrameworkElement ThemeUpdateValue(JsonElement value, JsonElement label)
    {
        var panel = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, HorizontalAlignment = HorizontalAlignment.Left };
        if (value.ValueKind == JsonValueKind.String && value.GetString() is { } color && ThemeUpdateColor(color) is { } brush)
        {
            panel.Children.Add(new Border { Width = 24, Height = 18, CornerRadius = new CornerRadius(3), Background = brush });
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(panel, color);
            panel.Children.Add(new TextBlock { Text = color, VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap });
            return panel;
        }
        if (value.ValueKind == JsonValueKind.Array && value.GetArrayLength() > 0 && value[0].ValueKind == JsonValueKind.Object && value[0].TryGetProperty("color", out _))
        {
            var ramp = new StackPanel { Orientation = Orientation.Horizontal };
            foreach (var stop in value.EnumerateArray())
                ramp.Children.Add(new Border { Width = 100.0 / value.GetArrayLength(), Height = 18, Background = ThemeUpdateColor(stop.GetProperty("color").GetString()!) });
            AutomationProperties.SetName(ramp, UiLanguage.Text("ThemeUpdate_ColorRamp"));
            ToolTipService.SetToolTip(ramp, string.Join(" · ", value.EnumerateArray().Select(stop => stop.GetProperty("color").GetString())));
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
