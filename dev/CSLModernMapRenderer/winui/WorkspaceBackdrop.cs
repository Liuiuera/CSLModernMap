// 绘制工作区背景和窗口材质。
using Microsoft.UI.Composition;
using Microsoft.UI.Composition.SystemBackdrops;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;

namespace CSLModernMapWinUI;

// 标题和标签栏使用跟随主题的基础材质；功能区和侧栏另有更强着色，地图保持不透明。
internal sealed class WorkspaceBackdrop : SystemBackdrop
{
    private DesktopAcrylicController? _controller;
    private SystemBackdropConfiguration? _configuration;
    protected override void OnTargetConnected(ICompositionSupportsSystemBackdrop target, XamlRoot xamlRoot)
    {
        base.OnTargetConnected(target, xamlRoot);
        if (!DesktopAcrylicController.IsSupported()) return;
        _configuration = GetDefaultSystemBackdropConfiguration(target, xamlRoot);
        _controller = new DesktopAcrylicController();
        ApplyTheme(_controller, ((FrameworkElement)xamlRoot.Content).ActualTheme);
        _controller.SetSystemBackdropConfiguration(_configuration);
        _controller.AddSystemBackdropTarget(target);
    }
    protected override void OnDefaultSystemBackdropConfigurationChanged(ICompositionSupportsSystemBackdrop target, XamlRoot xamlRoot)
    {
        if (_controller is not null && _configuration is not null)
        {
            ApplyTheme(_controller, ((FrameworkElement)xamlRoot.Content).ActualTheme);
            _controller.SetSystemBackdropConfiguration(_configuration);
        }
    }
    private static void ApplyTheme(DesktopAcrylicController controller, ElementTheme theme)
    {
        var dark = theme == ElementTheme.Dark;
        var color = dark ? Windows.UI.Color.FromArgb(255, 36, 38, 41)
            : Windows.UI.Color.FromArgb(255, 247, 248, 250);
        // 自定义材质属性会关闭自动主题色；深色模式限制色调和亮度以防背景亮窗冲淡。
        controller.TintColor = color;
        controller.FallbackColor = color;
        controller.TintOpacity = dark ? .60f : .12f;
        controller.LuminosityOpacity = dark ? .75f : .45f;
    }
    protected override void OnTargetDisconnected(ICompositionSupportsSystemBackdrop target)
    {
        _controller?.RemoveSystemBackdropTarget(target);
        _controller?.Dispose(); _controller = null;
        _configuration = null;
        base.OnTargetDisconnected(target);
    }
}
