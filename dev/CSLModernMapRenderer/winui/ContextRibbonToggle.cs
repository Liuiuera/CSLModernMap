// 提供功能区使用的上下文切换控件。
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using RibbonSpace;
using RibbonSpace.Controls;

namespace CSLModernMapWinUI;

internal sealed class ContextRibbonToggle
{
    internal RibbonToggleButton Button { get; }
    private readonly string _title;
    private readonly Func<bool> _read;

    internal ContextRibbonToggle(string title, IconElement icon, Func<bool> read,
        Func<Task> toggle, Action<Exception> reportError)
    {
        _title = title; _read = read;
        Button = new RibbonToggleButton { Label = title, Icon = icon, Size = RibbonItemSize.Medium };
        Button.Click += async (_, _) =>
        {
            try { await toggle(); }
            catch (Exception error) { reportError(error); }
            finally { Refresh(Button.IsEnabled); }
        };
    }

    internal void Refresh(bool enabled)
    {
        Button.IsEnabled = enabled;
        Button.IsChecked = _read();
        AutomationProperties.SetName(Button, _title + (Button.IsChecked == true ? UiLanguage.Text("ContextRibbonToggle_Refresh_645432A5BF") : UiLanguage.Text("ContextRibbonToggle_Refresh_96E5815C15")));
    }
}
