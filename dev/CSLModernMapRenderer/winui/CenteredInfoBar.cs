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
            Grid.SetRowSpan(icon, 2);
            icon.VerticalAlignment = VerticalAlignment.Center;
        }
    }
}
