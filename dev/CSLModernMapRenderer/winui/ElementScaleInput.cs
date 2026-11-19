using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Windows.Globalization.NumberFormatting;

namespace CSLModernMapWinUI;

internal sealed class ElementScaleInput : StackPanel
{
    internal NumberBox Input { get; }
    private double _edge;
    internal double ElementScale => double.TryParse(Input.Text, out var value) ? value / 100 : double.NaN;
    internal bool IsValid => double.IsFinite(ElementScale) && ElementScale > 0;

    internal ElementScaleInput(NumberBox edge, double scale, bool compact = false)
    {
        Spacing = 6;
        _edge = edge.Value;
        Input = new NumberBox { Header = UiLanguage.Text("Pixel_Scale"),
            NumberFormatter = new DecimalFormatter { FractionDigits = 2,
                NumberRounder = new IncrementNumberRounder { Increment = 0.01 } }, Value = scale * 100,
            SmallChange = 5, ValidationMode = NumberBoxValidationMode.Disabled };
        AutomationProperties.SetAutomationId(Input, "elementScale");
        var recommendation = new TextBlock { TextWrapping = TextWrapping.Wrap, FontSize = 12 };
        void Recommend() => recommendation.Text = UiLanguage.Format("Pixel_Recommended", (edge.Value / 4096 * 100).ToString("F2"));
        Recommend();
        edge.ValueChanged += (_, args) => {
            if (!double.IsFinite(args.NewValue) || args.NewValue <= 0) return;
            if (_edge > 0 && IsValid) Input.Value = ElementScale * 100 * args.NewValue / _edge;
            _edge = args.NewValue; Recommend();
        };
        if (compact)
        {
            var entry = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
            AutomationProperties.SetName(Input, UiLanguage.Text("Pixel_Scale"));
            Input.Header = null;
            Input.Width = 102;
            entry.Children.Add(Input);
            entry.Children.Add(new TextBlock { Text = "%", VerticalAlignment = VerticalAlignment.Center });
            recommendation.VerticalAlignment = VerticalAlignment.Center;
            Children.Add(new ResponsiveFieldGrid(280, entry, recommendation));
        }
        else
        {
            Children.Add(Input);
            Children.Add(recommendation);
            Children.Add(new TextBlock { Text = UiLanguage.Text("Pixel_Help"), TextWrapping = TextWrapping.Wrap, FontSize = 12 });
        }
    }
}
