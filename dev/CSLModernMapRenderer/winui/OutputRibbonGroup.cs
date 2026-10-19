// 组织功能区中的输出操作控件。
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using RibbonSpace.Controls;

namespace CSLModernMapWinUI;

internal static class OutputRibbonGroup
{
    internal static RibbonGroup Create(string title)
    {
        var group = new RibbonGroup { Header = title };
        // 只强调这一组的标题底色；其余分组保持应用主题的透明标题。
        group.Resources["RibbonGroupCaptionBackgroundBrush"] =
            (Brush)Application.Current.Resources["WorkspaceOutputHeaderBrush"];
        return group;
    }
}
