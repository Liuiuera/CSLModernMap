using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public sealed class CenteredInfoBar : InfoBar
{
    protected override void OnApplyTemplate()
    {
        base.OnApplyTemplate();
        if (GetTemplateChild("StandardIconArea") is FrameworkElement icon)
        {
            // WinUI 放在第二行的内容也计入图标的居中范围。
            Grid.SetRowSpan(icon, 2);
            icon.VerticalAlignment = VerticalAlignment.Center;
        }
    }
}
