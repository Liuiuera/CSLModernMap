using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

internal sealed class DraftResolutionInput : StackPanel
{
    private readonly RadioButtons _resolutionMode = new() { MaxColumns = 2 };
    private readonly ComboBox _resolution = new() { HorizontalAlignment = HorizontalAlignment.Stretch };
    private readonly NumberBox _edge = new() { Value = 4096, Minimum = 1, Maximum = 32768 };
    private readonly NumberBox _width = new() { Header = Text("PixelWidth"), Minimum = 1, Maximum = 32768, SmallChange = 256 };
    private readonly NumberBox _height = new() { Header = Text("PixelHeight"), Minimum = 1, Maximum = 32768, SmallChange = 256 };
    private readonly StackPanel _customSize = new() { Spacing = 8 };
    private double[]? _bounds;
    private int _rotation;
    private bool _syncing;
    private double? _customEdge;
    internal NumberBox Edge => _edge;
    internal bool Custom => _resolutionMode.SelectedIndex == 1;
    internal bool IsValid { get; private set; }
    internal event Action? Changed;
    private static string Text(string key) => UiLanguage.Text("CityEditor_" + key);

    internal DraftResolutionInput()
    {
        Spacing = 6;
        AutomationProperties.SetAutomationId(_width, "editor.pixelWidth");
        AutomationProperties.SetAutomationId(_height, "editor.pixelHeight");
        AutomationProperties.SetAutomationId(_resolution, "editor.resolution");
        AutomationProperties.SetAutomationId(_resolutionMode, "editor.resolutionMode");
        AutomationProperties.SetName(_resolution, Text("PresetResolution"));
        foreach (var pixels in new[] { 2048, 4096, 8192 }) _resolution.Items.Add(new ComboBoxItem { Tag = pixels });
        _resolution.SelectedIndex = 1;
        _resolutionMode.Items.Add(Text("PresetResolution"));
        _resolutionMode.Items.Add(Text("CustomResolution"));
        _resolutionMode.SelectedIndex = 0;
        _customSize.Children.Add(new ResponsiveFieldGrid(290, _width, _height));
        _customSize.Children.Add(ExportDesign.Text(Text("SizeLinked")));
        Children.Add(_resolutionMode);
        Children.Add(_resolution);
        Children.Add(_customSize);
        _resolution.SelectionChanged += (_, _) => {
            if (_syncing || _resolutionMode.SelectedIndex != 0) return;
            _edge.Value = (int)((ComboBoxItem)_resolution.SelectedItem).Tag;
            Refresh();
        };
        _resolutionMode.SelectionChanged += (_, _) => {
            if (_syncing) return;
            _edge.Value = _resolutionMode.SelectedIndex == 0
                ? (int)((ComboBoxItem)_resolution.SelectedItem).Tag
                : _customEdge ?? _edge.Value;
            Refresh();
        };
        _width.ValueChanged += (_, args) => Resize(args.NewValue, true);
        _height.ValueChanged += (_, args) => Resize(args.NewValue, false);
    }

    internal void SetGeometry(double[] bounds, int rotation)
    {
        _bounds = bounds;
        _rotation = rotation;
        Refresh();
    }

    internal void Load(double[] bounds, int rotation, int edge, bool custom)
    {
        _syncing = true;
        try
        {
            var preset = _resolution.Items.OfType<ComboBoxItem>().FirstOrDefault(item => (int)item.Tag == edge);
            if (preset is not null) _resolution.SelectedItem = preset;
            _resolutionMode.SelectedIndex = custom || preset is null ? 1 : 0;
            if (Custom) _customEdge = edge;
            _edge.Value = edge;
        }
        finally { _syncing = false; }
        SetGeometry(bounds, rotation);
    }

    private void Resize(double value, bool width)
    {
        if (_syncing || _bounds is null || !Custom) return;
        if (!ValidPixel(value)) { IsValid = false; Changed?.Invoke(); return; }
        var (spanWidth, spanHeight) = CanvasDimensions.Spans(_bounds, _rotation);
        var ratio = (width ? spanWidth : spanHeight) / Math.Max(spanWidth, spanHeight);
        var edge = Math.Round(value / ratio);
        if (!ValidPixel(edge)) { IsValid = false; Changed?.Invoke(); return; }
        _customEdge = _edge.Value = edge;
        Refresh();
    }

    private void Refresh()
    {
        if (_bounds is null || _syncing) return;
        _syncing = true;
        try
        {
            var size = CanvasDimensions.Calculate(_bounds, _edge.Value, _rotation);
            _width.Value = size.Width; _height.Value = size.Height;
            foreach (var item in _resolution.Items.OfType<ComboBoxItem>())
            {
                var preset = CanvasDimensions.Calculate(_bounds, (int)item.Tag, _rotation);
                item.Content = $"{preset.Width} × {preset.Height} px";
            }
            _resolution.Visibility = _resolutionMode.SelectedIndex == 0 ? Visibility.Visible : Visibility.Collapsed;
            _customSize.Visibility = _resolutionMode.SelectedIndex == 1 ? Visibility.Visible : Visibility.Collapsed;
            IsValid = true;
        }
        finally { _syncing = false; }
        Changed?.Invoke();
    }

    private static bool ValidPixel(double value) => double.IsFinite(value) && value == Math.Truncate(value) && value is >= 1 and <= 32768;
}
