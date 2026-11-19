// 导出与创建草稿共用的范围入口。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;

namespace CSLModernMapWinUI;

internal sealed class MapRangeControls : StackPanel
{
    internal RadioButton Full { get; } = new() { Content = UiLanguage.Text("ExportWindow_Preview__rangeFull_653AEA4D67") };
    internal RadioButton Entry { get; } = new() { Content = UiLanguage.Text("ExportWindow_Preview__rangeEntry_157A8D388F") };
    internal RadioButton Custom { get; } = new() { Content = UiLanguage.Text("ExportWindow_Preview__rangeCustom_4EAFA9E925") };
    internal TextBlock Summary { get; } = new() { TextWrapping = TextWrapping.Wrap };
    internal Button Choose { get; } = new() { Content = UiLanguage.Text("ExportWindow__chooseRange_A2BF7686B7") };

    internal MapRangeControls(MapRangePreview? preview = null)
    {
        Spacing = 10;
        var modes = new StackPanel { Spacing = 8, Orientation = Orientation.Horizontal };
        var group = Guid.NewGuid().ToString("N");
        foreach (var radio in new[] { Full, Entry, Custom })
        {
            radio.GroupName = group;
            radio.MinWidth = 0;
            modes.Children.Add(radio);
        }
        modes.SizeChanged += (_, _) => modes.Orientation = modes.ActualWidth >= 340 ? Orientation.Horizontal : Orientation.Vertical;
        Children.Add(modes);
        var details = new StackPanel { Spacing = 8, VerticalAlignment = VerticalAlignment.Center };
        details.Children.Add(Summary);
        details.Children.Add(Choose);
        if (preview is null) Children.Add(details);
        else
        {
            var row = new Grid { ColumnSpacing = 12 };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.Children.Add(preview);
            Grid.SetColumn(details, 1);
            row.Children.Add(details);
            Children.Add(row);
        }
    }
}

internal sealed class MapRangePreview : Canvas
{
    private readonly Image _image = new() { Stretch = Stretch.Fill };
    private readonly Rectangle _outline = new() { StrokeThickness = 2, IsHitTestVisible = false };
    private double[]? _world, _selection;
    internal bool HasMap { get; private set; }

    internal MapRangePreview(double size)
    {
        Width = Height = size;
        HorizontalAlignment = HorizontalAlignment.Center;
        _outline.Stroke = (Brush)Application.Current.Resources["AccentFillColorDefaultBrush"];
        Children.Add(_image);
        Children.Add(_outline);
        SizeChanged += (_, _) => Draw();
        AutomationProperties.SetName(this, UiLanguage.Text("ExportWindow_Preview_BuildRangeControls_647B8C0BCB"));
    }

    internal Task LoadAsync(JsonElement profile, Func<bool> active, Action loaded, Action<string>? failed = null)
    {
        _world = profile.GetProperty("worldBounds").EnumerateArray().Select(value => value.GetDouble()).ToArray();
        var path = profile.GetProperty("previews").GetProperty("world").GetString()!;
        return PreviewImages.SetAsync(_image, path, active, failed, loaded: () =>
        {
            HasMap = true;
            loaded();
            Draw();
        });
    }

    internal void Select(double[] bounds)
    {
        _selection = bounds;
        Draw();
    }

    private void Draw()
    {
        if (_world is not { Length: 4 } world || ActualWidth <= 0) return;
        var spanX = world[2] - world[0];
        var spanZ = world[3] - world[1];
        if (spanX <= 0 || spanZ <= 0) return;
        var scale = Math.Min(ActualWidth / spanZ, Height / spanX);
        var mapWidth = _image.Width = spanZ * scale;
        var mapHeight = _image.Height = spanX * scale;
        var left = (ActualWidth - mapWidth) / 2;
        var top = (Height - mapHeight) / 2;
        SetLeft(_image, left);
        SetTop(_image, top);
        var selection = _selection ?? world;
        // 地图总览的屏幕向右是负 Z，向上是正 X。
        var startX = Math.Clamp((world[3] - selection[3]) / spanZ, 0, 1);
        var endX = Math.Clamp((world[3] - selection[1]) / spanZ, 0, 1);
        var startY = Math.Clamp((world[2] - selection[2]) / spanX, 0, 1);
        var endY = Math.Clamp((world[2] - selection[0]) / spanX, 0, 1);
        SetLeft(_outline, left + startX * mapWidth);
        SetTop(_outline, top + startY * mapHeight);
        _outline.Width = (endX - startX) * mapWidth;
        _outline.Height = (endY - startY) * mapHeight;
    }
}

internal static class CanvasDimensions
{
    internal static (double Width, double Height) Spans(double[] bounds, int rotation)
    {
        var spanZ = bounds[3] - bounds[1];
        var spanX = bounds[2] - bounds[0];
        return rotation is 90 or 270 ? (spanX, spanZ) : (spanZ, spanX);
    }

    internal static (int Width, int Height) Calculate(double[] bounds, double longEdge, int rotation)
    {
        var (width, height) = Spans(bounds, rotation);
        var scale = longEdge / Math.Max(width, height);
        return (Math.Max(1, (int)Math.Round(width * scale)), Math.Max(1, (int)Math.Round(height * scale)));
    }
}
