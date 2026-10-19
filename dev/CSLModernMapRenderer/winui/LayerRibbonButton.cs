// 提供功能区的图层按钮控件。
using Microsoft.UI.Xaml.Controls;
using RibbonSpace;
using RibbonSpace.Controls;

namespace CSLModernMapWinUI;

internal static class LayerRibbonButton
{
    // 主体开关图层，箭头由调用方挂接详细设置菜单。
    internal static RibbonSplitButton Create(string title, IconElement icon, bool large,
        Func<Task> toggle, Action<Exception> reportError)
    {
        var button = new RibbonSplitButton
        {
            Label = title,
            Icon = icon,
            Size = large ? RibbonItemSize.Large : RibbonItemSize.Medium,
            IsCheckable = true,
        };
        button.Click += async (_, _) =>
        {
            try { await toggle(); }
            catch (Exception error) { reportError(error); }
        };
        return button;
    }
}
