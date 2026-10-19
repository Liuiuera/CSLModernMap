// 按窗口宽度排列设置字段。
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

internal sealed class ResponsiveFieldGrid : Grid
{
    private int _columns;
    private readonly double _breakpoint;

    internal ResponsiveFieldGrid(params FrameworkElement[] fields) : this(600, fields) { }

    internal ResponsiveFieldGrid(double breakpoint, params FrameworkElement[] fields)
    {
        _breakpoint = breakpoint;
        ColumnSpacing = 12;
        RowSpacing = 8;
        foreach (var field in fields) Children.Add(field);
        Loaded += (_, _) => ArrangeFields();
        SizeChanged += (_, _) => ArrangeFields();
        foreach (var field in fields) field.RegisterPropertyChangedCallback(VisibilityProperty, (_, _) => ArrangeFields(true));
        ArrangeFields();
    }

    private void ArrangeFields(bool force = false)
    {
        var columns = ActualWidth >= _breakpoint ? 2 : 1;
        if (!force && columns == _columns) return;
        _columns = columns;
        ColumnDefinitions.Clear();
        RowDefinitions.Clear();
        for (var column = 0; column < columns; column++)
            ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var index = 0;
        foreach (var field in Children.OfType<FrameworkElement>().Where(field => field.Visibility == Visibility.Visible))
        {
            if (index % columns == 0) RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            SetRow(field, index / columns);
            SetColumn(field, index % columns);
            index++;
        }
    }
}
