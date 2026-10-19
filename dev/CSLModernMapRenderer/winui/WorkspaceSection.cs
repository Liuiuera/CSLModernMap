// 定义工作区功能区的分组控件。
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Markup;

namespace CSLModernMapWinUI;

internal static class WorkspaceSection
{
    internal static Expander Create(string title, UIElement content, bool expanded = true, bool emphasizeHeader = false)
    {
        var section = new Expander
        {
            Header = title,
            Content = content,
            IsExpanded = expanded,
            Style = (Style)Application.Current.Resources["WorkspaceSectionStyle"],
        };
        if (emphasizeHeader)
            section.Resources["ExpanderHeaderBackground"] = XamlReader.Load("""
                <SolidColorBrush xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
                    Color="{ThemeResource WorkspaceOutputHeaderColor}" />
                """);
        return section;
    }
}
