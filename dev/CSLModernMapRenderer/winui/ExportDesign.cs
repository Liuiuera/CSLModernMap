// 组织导出界面的布局与视觉样式。
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Markup;
using Microsoft.UI.Xaml.Automation;

namespace CSLModernMapWinUI;

internal static class ExportDesign
{
    internal static Border Card(UIElement content) => Surface(content, "CardBackgroundFillColorDefaultBrush");

    internal static Border Divider(UIElement content, double topPadding = 12)
    {
        var border = (Border)XamlReader.Load("""
            <Border xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
                BorderBrush="{ThemeResource CardStrokeColorDefaultBrush}"
                BorderThickness="0,1,0,0" />
            """);
        border.Child = content;
        border.Padding = new Thickness(0, topPadding, 0, 0);
        return border;
    }

    internal static void Secondary(TextBlock text)
    {
        text.FontSize = 12;
        var resource = (TextBlock)XamlReader.Load("""
            <TextBlock xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
                ><TextBlock.Style><Style TargetType="TextBlock">
                <Setter Property="Foreground" Value="{ThemeResource TextFillColorSecondaryBrush}" />
                </Style></TextBlock.Style></TextBlock>
            """);
        text.Style = resource.Style;
    }

    internal static Border Surface(UIElement content, string brush)
    {
        var card = (Border)XamlReader.Load($$"""
            <Border xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
                Background="{ThemeResource {{brush}}}" BorderBrush="{ThemeResource CardStrokeColorDefaultBrush}"
                BorderThickness="1" CornerRadius="8" Padding="12" />
            """);
        card.Child = content;
        return card;
    }

    internal static TextBlock Text(string text, bool heading = false) => new()
    {
        Text = text, TextWrapping = TextWrapping.Wrap,
        FontSize = heading ? 16 : 14,
        FontWeight = heading ? Microsoft.UI.Text.FontWeights.SemiBold : Microsoft.UI.Text.FontWeights.Normal,
    };

    internal static StackPanel Group(string title)
    {
        var body = new StackPanel { Spacing = 8 };
        body.Children.Add(Text(title, true));
        return body;
    }

    internal static Grid InlineField(FrameworkElement input, TextBlock detail)
    {
        var row = new Grid { ColumnSpacing = 12 };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        detail.VerticalAlignment = VerticalAlignment.Center;
        Grid.SetColumn(detail, 1);
        row.Children.Add(input);
        row.Children.Add(detail);
        return row;
    }
}
