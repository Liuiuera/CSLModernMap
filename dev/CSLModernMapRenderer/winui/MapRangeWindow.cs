// 提供地图范围选择窗口。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Shapes;
using Windows.Foundation;
using Windows.UI;

namespace CSLModernMapWinUI;

internal sealed class MapRangeWindow
{
    private readonly EditorWindow _window;
    private readonly EngineClient _engine;
    private readonly JsonElement _profile;
    private readonly RadioButton _full = new() { Content = UiLanguage.Text("MapRangeWindow__full_14425C91B0"), GroupName = "RangeMode" };
    private readonly RadioButton _outside = new() { Content = UiLanguage.Text("MapRangeWindow__outside_4A992809A4"), GroupName = "RangeMode" };
    private readonly RadioButton _tiles = new() { Content = UiLanguage.Text("MapRangeWindow__tiles_96C67183D7"), GroupName = "RangeMode" };
    private readonly RadioButton _manual = new() { Content = UiLanguage.Text("MapRangeWindow__manual_20459B88AA"), GroupName = "RangeMode" };
    private readonly NumberBox _count = new() { Header = UiLanguage.Text("MapRangeWindow__count_093CEEB552"), Minimum = 1, SmallChange = 1 };
    private readonly TextBlock _summary = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _status = new() { TextWrapping = TextWrapping.Wrap };
    private readonly Canvas _canvas = new() { Width = 410, Height = 410, Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent) };
    private readonly Image _image = new() { Stretch = Stretch.Fill, IsHitTestVisible = false };
    private readonly Rectangle _selection = new() { Stroke = new SolidColorBrush(Microsoft.UI.Colors.DodgerBlue),
        StrokeThickness = 3, Fill = new SolidColorBrush(Color.FromArgb(35, 30, 144, 255)), IsHitTestVisible = false };
    private readonly double[] _world, _city;
    private double[] _shown, _selected, _dragOriginal;
    private double _mapEdge = 410;
    private Point? _dragStart;
    private bool _moving, _loading;
    private int _revision;
    public JsonElement? AppliedRegion { get; private set; }

    public MapRangeWindow(EngineClient engine, JsonElement profile, double[]? initialBounds = null, Window? owner = null)
    {
        _window = new(UiLanguage.Text("MapRangeWindow_View_E00B2B4CBC"), 900, 650, translucent: true, owner: owner, modal: owner is not null);
        _engine = engine;
        _profile = profile.Clone();
        _world = Bounds(profile.GetProperty("worldBounds"));
        _city = Bounds(profile.GetProperty("cityBounds"));
        _shown = _city;
        _selected = initialBounds ?? Bounds(profile.GetProperty("resolvedBounds"));
        _dragOriginal = _selected;
        var limit = profile.GetProperty("limit").GetInt32();
        var grid = profile.GetProperty("grid").GetInt32();
        _count.Maximum = Math.Max(1, limit);
        _count.Value = Math.Min(3, Math.Max(1, limit));
        _outside.Visibility = profile.GetProperty("hasOutside").GetBoolean() ? Visibility.Visible : Visibility.Collapsed;
        _tiles.IsEnabled = limit > 0;
        var controls = new StackPanel { Spacing = 10 };
        controls.Children.Add(new TextBlock { Text = profile.GetProperty("game").GetString()?.ToUpperInvariant() + " · " + UiLanguage.Text("MapRangeWindow_View_E00B2B4CBC"),
            FontSize = 18, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        controls.Children.Add(_full);
        if (_outside.Visibility == Visibility.Visible) controls.Children.Add(_outside);
        controls.Children.Add(_tiles);
        var presets = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 5 };
        foreach (var item in profile.GetProperty("presets").EnumerateArray())
        {
            var n = item.GetInt32();
            var button = new Button { Content = $"{n}×{n}", MinWidth = 52 };
            button.Click += (_, _) => { _count.Value = n; _tiles.IsChecked = true; };
            presets.Children.Add(button);
        }
        controls.Children.Add(presets);
        controls.Children.Add(_count);
        controls.Children.Add(_manual);
        var redraw = new Button { Content = UiLanguage.Text("MapRangeWindow_View_83BD870016") };
        redraw.Click += (_, _) => { _manual.IsChecked = true; _status.Text = UiLanguage.Text("MapRangeWindow_View_615E061A2F"); };
        controls.Children.Add(redraw);
        controls.Children.Add(_summary);
        controls.Children.Add(_status);

        _canvas.Children.Add(_image);
        _canvas.Children.Add(_selection);
        var locator = new StackPanel { Spacing = 8, HorizontalAlignment = HorizontalAlignment.Center };
        locator.Children.Add(_canvas);
        locator.Children.Add(new TextBlock { Text = UiLanguage.Text("MapRangeWindow_View_000E18C870"), HorizontalAlignment = HorizontalAlignment.Center });
        locator.Children.Add(new TextBlock
        {
            Text = UiLanguage.Text("MapRangeWindow_View_F3A8315894"),
            TextWrapping = TextWrapping.Wrap, MaxWidth = 410,
        });
        var layout = new Grid { ColumnSpacing = 16 };
        layout.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        layout.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        layout.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        layout.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        layout.Children.Add(new ScrollViewer { Content = controls, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled });
        Grid.SetColumn(locator, 1); layout.Children.Add(locator);
        layout.SizeChanged += (_, _) =>
        {
            var stacked = layout.ActualWidth < 720;
            layout.ColumnDefinitions[1].Width = stacked ? new GridLength(0) : GridLength.Auto;
            Grid.SetColumn(locator, stacked ? 0 : 1);
            Grid.SetRow(locator, stacked ? 1 : 0);
            var edge = stacked ? Math.Clamp(layout.ActualWidth - 48, 260, 410)
                : Math.Clamp((layout.ActualWidth - 320) * .9, 260, 410);
            if (Math.Abs(edge - _mapEdge) < 1) return;
            _mapEdge = edge;
            ResizeMap();
        };
        _window.SetBody(layout);
        _window.ActionsRightAligned = true;
        _window.AddAction(UiLanguage.Text("MapRangeWindow_View_498A38978B"), ApplyAsync);
        _window.AddAction(UiLanguage.Text("MapRangeWindow_View_2CD0F3BE87"), async () => { await _window.RequestCloseAsync(); });

        _canvas.PointerPressed += PointerPressed;
        _canvas.PointerMoved += PointerMoved;
        _canvas.PointerReleased += PointerReleased;
        _canvas.PointerCaptureLost += (_, _) => _dragStart = null;
        _full.Checked += (_, _) => _ = UpdateSelectionAsync();
        _outside.Checked += (_, _) => _ = UpdateSelectionAsync();
        _tiles.Checked += (_, _) => _ = UpdateSelectionAsync();
        _manual.Checked += (_, _) => _ = UpdateSelectionAsync();
        _count.ValueChanged += (_, _) => { if (_tiles.IsChecked == true) _ = UpdateSelectionAsync(); };

        if (initialBounds is not null || profile.GetProperty("legacy").GetBoolean()) _manual.IsChecked = true;
        else if (profile.GetProperty("selection") is { ValueKind: JsonValueKind.Object } saved)
        {
            var n = saved.GetProperty("tiles").GetInt32();
            var anchor = saved.GetProperty("anchor").GetString();
            if (anchor is "manual" or "busiest") _manual.IsChecked = true;
            else if (n == 0) (_outside.Visibility == Visibility.Visible ? _outside : _full).IsChecked = true;
            else if (n == grid && anchor == "center") _full.IsChecked = true;
            else
            {
                _count.Value = Math.Max(1, n);
                _tiles.IsChecked = true;
            }
        }
        else (_outside.Visibility == Visibility.Visible ? _outside : _full).IsChecked = true;
        _ = UpdateSelectionAsync();
    }

    public async Task<bool> ShowAsync()
    {
        await _window.ShowAsync();
        return AppliedRegion is not null;
    }

    private static double[] Bounds(JsonElement value) => value.EnumerateArray().Select(item => item.GetDouble()).ToArray();
    private const string Anchor = "center";

    private async Task UpdateSelectionAsync()
    {
        var revision = ++_revision;
        _loading = true;
        try
        {
            _count.IsEnabled = _tiles.IsChecked == true;
            _shown = _outside.IsChecked == true ? _world : _city;
            if (_full.IsChecked == true) _selected = _city;
            else if (_outside.IsChecked == true) _selected = _world;
            else if (_tiles.IsChecked == true && double.IsFinite(_count.Value))
            {
                var result = await _engine.RequestAsync("getRangePreset", new { tiles = (int)_count.Value, anchor = Anchor });
                if (revision != _revision) return;
                _selected = Bounds(result.GetProperty("bounds"));
            }
            if (_manual.IsChecked == true && !_city.SequenceEqual(_selected))
                _shown = _selected[0] >= _city[0] && _selected[1] >= _city[1] &&
                    _selected[2] <= _city[2] && _selected[3] <= _city[3] ? _city : _world;
            var name = _shown.SequenceEqual(_world) ? "world" : "city";
            ResizeMap();
            await PreviewImages.SetAsync(_image, _profile.GetProperty("previews").GetProperty(name).GetString()!,
                () => revision == _revision, error => _status.Text = error);
            if (revision == _revision) PaintSelection();
        }
        catch (Exception error) { if (revision == _revision) _status.Text = error.Message; }
        finally { if (revision == _revision) _loading = false; }
    }

    private void PaintSelection()
    {
        var width = _canvas.Width; var height = _canvas.Height;
        Canvas.SetLeft(_selection, (_shown[3] - _selected[3]) / (_shown[3] - _shown[1]) * width);
        Canvas.SetTop(_selection, (_shown[2] - _selected[2]) / (_shown[2] - _shown[0]) * height);
        _selection.Width = (_selected[3] - _selected[1]) / (_shown[3] - _shown[1]) * width;
        _selection.Height = (_selected[2] - _selected[0]) / (_shown[2] - _shown[0]) * height;
        var kmX = (_selected[2] - _selected[0]) / 1000;
        var kmZ = (_selected[3] - _selected[1]) / 1000;
        _summary.Text = _tiles.IsChecked == true
            ? $"{(int)_count.Value}×{(int)_count.Value} {UiLanguage.Text("MapRangeWindow_PaintSelection_565323A1BB")} · {kmZ:0.###} × {kmX:0.###} km"
            : $"{kmZ:0.###} × {kmX:0.###} km";
    }

    private void ResizeMap()
    {
        var spanX = _shown[2] - _shown[0];
        var spanZ = _shown[3] - _shown[1];
        _canvas.Width = _mapEdge * spanZ / Math.Max(spanX, spanZ);
        _canvas.Height = _mapEdge * spanX / Math.Max(spanX, spanZ);
        _image.Width = _canvas.Width;
        _image.Height = _canvas.Height;
        PaintSelection();
    }

    private double[] PointToWorld(Point point)
    {
        var x = Math.Clamp(point.X / _canvas.Width, 0, 1);
        var y = Math.Clamp(point.Y / _canvas.Height, 0, 1);
        return [_shown[2] - y * (_shown[2] - _shown[0]), _shown[3] - x * (_shown[3] - _shown[1])];
    }

    private void PointerPressed(object sender, Microsoft.UI.Xaml.Input.PointerRoutedEventArgs args)
    {
        if (_loading) return;
        var point = args.GetCurrentPoint(_canvas).Position;
        if (point.X < 0 || point.Y < 0 || point.X > _canvas.Width || point.Y > _canvas.Height) return;
        _dragStart = point;
        _dragOriginal = _selected.ToArray();
        _moving = _manual.IsChecked == true &&
            point.X >= Canvas.GetLeft(_selection) && point.X <= Canvas.GetLeft(_selection) + _selection.Width &&
            point.Y >= Canvas.GetTop(_selection) && point.Y <= Canvas.GetTop(_selection) + _selection.Height;
        _canvas.CapturePointer(args.Pointer);
        args.Handled = true;
    }

    private void PointerMoved(object sender, Microsoft.UI.Xaml.Input.PointerRoutedEventArgs args)
    {
        if (_dragStart is not { } start) return;
        var point = args.GetCurrentPoint(_canvas).Position;
        var from = PointToWorld(start); var to = PointToWorld(point);
        if (_moving)
        {
            var dx = Math.Clamp(to[0] - from[0], _shown[0] - _dragOriginal[0], _shown[2] - _dragOriginal[2]);
            var dz = Math.Clamp(to[1] - from[1], _shown[1] - _dragOriginal[1], _shown[3] - _dragOriginal[3]);
            _selected = [_dragOriginal[0] + dx, _dragOriginal[1] + dz,
                _dragOriginal[2] + dx, _dragOriginal[3] + dz];
        }
        else
        {
            _selected = [Math.Min(from[0], to[0]), Math.Min(from[1], to[1]),
                Math.Max(from[0], to[0]), Math.Max(from[1], to[1])];
        }
        if (_selected[2] > _selected[0] && _selected[3] > _selected[1]) PaintSelection();
    }

    private void PointerReleased(object sender, Microsoft.UI.Xaml.Input.PointerRoutedEventArgs args)
    {
        if (_dragStart is not { } start) return;
        var point = args.GetCurrentPoint(_canvas).Position;
        if (Math.Abs(point.X - start.X) < 3 && Math.Abs(point.Y - start.Y) < 3)
            _selected = _dragOriginal;
        else _manual.IsChecked = true;
        _dragStart = null;
        _canvas.ReleasePointerCapture(args.Pointer);
        PaintSelection();
    }

    private Task ApplyAsync()
    {
        if (_loading) return Task.CompletedTask;
        if (_selected[2] <= _selected[0] || _selected[3] <= _selected[1])
        {
            _status.Text = UiLanguage.Text("MapRangeWindow_ApplyAsync_37E50EB5F4");
            return Task.CompletedTask;
        }
        AppliedRegion = JsonSerializer.SerializeToElement(new { bounds = _selected, source = "custom" });
        _window.ForceClose();
        return Task.CompletedTask;
    }
}
