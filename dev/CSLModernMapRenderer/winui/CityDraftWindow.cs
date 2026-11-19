// 创建草稿只收集会话选项，不保存主题。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Markup;

namespace CSLModernMapWinUI;

internal sealed record CityDraftOptions(int LongEdge, int Rotation, JsonObject? Region, double ElementScale, JsonObject PoiDensity, bool CustomResolution);

internal sealed class CityDraftWindow
{
    private readonly EditorWindow _window;
    private readonly EngineClient _engine;
    private readonly JsonElement _profile;
    private readonly double[] _fullBounds;
    private readonly JsonObject? _initialRegion;
    private JsonObject? _customRegion;
    private readonly MapRangePreview _rangePreview = new(112) { Visibility = Visibility.Collapsed };
    private readonly MapRangeControls _range;
    private readonly DraftResolutionInput _canvasSize = new();
    private readonly ComboBox _rotation = new() { MinWidth = 108, HorizontalAlignment = HorizontalAlignment.Left };
    private readonly ElementScaleInput _scale;
    private readonly DraftDensityInput _density = new();
    private readonly TextBlock _error = new() { TextWrapping = TextWrapping.Wrap };
    private readonly Button _create;
    private bool _syncing;
    private MapRangeWindow? _rangeWindow;
    private string _rangeError = "";
    private CityDraftOptions? _result;
    private static string Text(string key) => UiLanguage.Text("CityEditor_" + key);
    private int Rotation => (int)((ComboBoxItem)_rotation.SelectedItem).Tag;
    private JsonObject? Region => _range.Entry.IsChecked == true ? _initialRegion : _range.Custom.IsChecked == true ? _customRegion : null;
    private double[] Bounds => Region is { } region ? region["bounds"]!.AsArray().Select(item => item!.GetValue<double>()).ToArray() : _fullBounds;

    internal CityDraftWindow(EngineClient engine, JsonElement profile, JsonElement? selection, int rotation, Window owner, JsonElement poiProfile)
    {
        _engine = engine; _profile = profile.Clone();
        _range = new(_rangePreview);
        _fullBounds = profile.GetProperty("cityBounds").EnumerateArray().Select(item => item.GetDouble()).ToArray();
        _initialRegion = selection is null ? null : JsonSerializer.SerializeToNode(new { bounds = selection.Value, source = "selection" })!.AsObject();
        _window = new(Text("Create"), "pencil", 840, 530, translucent: true, owner: owner, modal: true) { ActionsRightAligned = true };
        AutomationProperties.SetAutomationId(_window.Root, "editor.createDraft");
        AutomationProperties.SetAutomationId(_rotation, "editor.rotation");
        AutomationProperties.SetLiveSetting(_error, AutomationLiveSetting.Assertive);
        _range.Entry.Visibility = selection is null ? Visibility.Collapsed : Visibility.Visible;
        _range.Custom.IsEnabled = profile.GetProperty("available").GetBoolean();
        _range.Choose.Visibility = Visibility.Collapsed;
        _range.Full.Checked += (_, _) => Refresh();
        _range.Entry.Checked += (_, _) => Refresh();
        _range.Custom.Checked += async (_, _) => {
            if (_syncing) return;
            if (_customRegion is null) await ChooseRangeAsync(); else Refresh();
        };
        _range.Choose.Click += async (_, _) => await ChooseRangeAsync();
        if (selection is null) _range.Full.IsChecked = true; else _range.Entry.IsChecked = true;
        foreach (var angle in new[] { 0, 90, 180, 270 })
        {
            var item = new ComboBoxItem { Content = $"{angle}°", Tag = angle };
            _rotation.Items.Add(item);
            if (angle == rotation) _rotation.SelectedItem = item;
        }
        _scale = new ElementScaleInput(_canvasSize.Edge, 1, compact: true);
        _density.Load(poiProfile);
        var canvas = new StackPanel { Spacing = 6 };
        canvas.Children.Add(_canvasSize);
        var sizing = ExportDesign.Group(Text("ElementSize"));
        sizing.Children.Add(_scale);
        canvas.Children.Add(ExportDesign.Divider(sizing));
        var rotationHelp = ExportDesign.Text(Text("CreateRotationHelp")); ExportDesign.Secondary(rotationHelp);
        var direction = ExportDesign.InlineField(_rotation, rotationHelp);
        var area = new StackPanel { Spacing = 10 };
        area.Children.Add(Section("Range", "marquee-2", _range, primary: true));
        area.Children.Add(Section("Rotation", "rotate-clockwise", direction));
        var output = new StackPanel { Spacing = 10 };
        output.Children.Add(Section("Resolution", "dimensions", canvas, primary: true));
        output.Children.Add(Section("IconDensity", "grid-dots", _density));
        var panels = new ResponsiveFieldGrid(700, area, output);
        AutomationProperties.SetAutomationId(panels, "editor.draftPanels");
        var content = new StackPanel { Spacing = 12 };
        var title = new TextBlock { Text = Text("Create"), FontSize = 22, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold };
        AutomationProperties.SetHeadingLevel(title, AutomationHeadingLevel.Level1);
        content.Children.Add(title);
        content.Children.Add(panels); content.Children.Add(_error);
        _window.SetBody(new ScrollViewer { Content = content, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled });
        _window.AddAction(UiLanguage.Text("Artwork_Cancel"), () => _window.RequestCloseAsync());
        _create = _window.AddAction(Text("Create"), CreateAsync, manageEnabled: false);
        _create.Style = (Style)Application.Current.Resources["AccentButtonStyle"];
        AutomationProperties.SetAutomationId(_create, "editor.create");
        _canvasSize.Changed += Validate;
        _rotation.SelectionChanged += (_, _) => Refresh();
        _scale.Input.ValueChanged += (_, _) => Validate();
        _scale.Input.Loaded += (_, _) => Validate();
        if (profile.GetProperty("available").GetBoolean())
        {
            _window.Root.Loaded += async (_, _) =>
            {
                await _rangePreview.LoadAsync(_profile, () => _window.Root.XamlRoot is not null,
                    () => _rangePreview.Visibility = Visibility.Visible,
                    message => { _rangeError = message; Validate(); });
            };
        }
        Refresh();
    }

    private static FrameworkElement Section(string title, string iconName, UIElement fields, bool primary = false)
    {
        var header = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var icon = (FontIcon)RibbonIcons.Named(iconName);
        icon.FontSize = primary ? 21 : 16;
        icon.VerticalAlignment = VerticalAlignment.Center;
        var foreground = primary ? "AccentTextFillColorPrimaryBrush" : "TextFillColorSecondaryBrush";
        icon.Style = (Style)XamlReader.Load($$"""
            <Style xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" TargetType="FontIcon">
                <Setter Property="Foreground" Value="{ThemeResource {{foreground}}}" />
            </Style>
            """);
        var heading = ExportDesign.Text(Text(title), heading: true);
        heading.FontSize = primary ? 18 : 14;
        AutomationProperties.SetHeadingLevel(heading, AutomationHeadingLevel.Level2);
        header.Children.Add(icon);
        header.Children.Add(heading);
        var body = new StackPanel { Spacing = 8 };
        body.Children.Add(header);
        body.Children.Add(fields);
        var card = ExportDesign.Card(body);
        card.Padding = new Thickness(12);
        return card;
    }

    private void Refresh()
    {
        if (_create is null || _syncing) return;
        _syncing = true;
        try
        {
            _canvasSize.SetGeometry(Bounds, Rotation);
            _range.Choose.Visibility = _range.Custom.IsChecked == true ? Visibility.Visible : Visibility.Collapsed;
            var bounds = Bounds;
            _rangePreview.Select(bounds);
            _range.Summary.Text = $"{(bounds[3] - bounds[1]) / 1000:0.###} × {(bounds[2] - bounds[0]) / 1000:0.###} km";
        }
        finally { _syncing = false; }
        Validate();
    }

    private void Validate()
    {
        if (_create is null) return;
        var valid = _canvasSize.IsValid && _scale.IsValid;
        _error.Text = valid ? _rangeError : Text("InvalidSize");
        _error.Visibility = _error.Text.Length == 0 ? Visibility.Collapsed : Visibility.Visible;
        _create.IsEnabled = valid && _rangeWindow is null;
    }
    private async Task ChooseRangeAsync()
    {
        if (_rangeWindow is not null) return;
        _create.IsEnabled = false; _rangeError = "";
        try
        {
            var bounds = (_customRegion ?? _initialRegion)?["bounds"]?.AsArray().Select(item => item!.GetValue<double>()).ToArray() ?? _fullBounds;
            var chooser = _rangeWindow = new MapRangeWindow(_engine, _profile, bounds, owner: _window);
            if (await chooser.ShowAsync() && chooser.AppliedRegion is { } region)
                _customRegion = JsonNode.Parse(region.GetRawText())!.AsObject();
        }
        catch (InvalidOperationException error) { _rangeError = error.Message; }
        finally
        {
            _rangeWindow = null;
            if (_customRegion is null) { if (_initialRegion is null) _range.Full.IsChecked = true; else _range.Entry.IsChecked = true; }
            Refresh();
        }
    }

    private Task CreateAsync()
    {
        _window.CommitFocusedInput(); Validate();
        if (!_create.IsEnabled) return Task.CompletedTask;
        var region = Region?.DeepClone().AsObject() ?? JsonSerializer.SerializeToNode(new { bounds = _fullBounds, source = "city" })!.AsObject();
        _result = new((int)_canvasSize.Edge.Value, Rotation, region, _scale.ElementScale, _density.Snapshot(), _canvasSize.Custom);
        _window.ForceClose();
        return Task.CompletedTask;
    }

    internal async Task<CityDraftOptions?> ShowAsync() { await _window.ShowAsync(); return _result; }
}
