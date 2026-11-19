// 管理工作区沉浸显示，地图场景和功能区布局保持原状态。
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.System;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private bool _immersive;
    private (bool Settings, bool Inspector) _panelsBeforeImmersive;
    private TeachingTip? _immersiveTip;

    private void ConfigureImmersiveMode()
    {
        ExitImmersiveButton.Content = UiLanguage.Text("Workspace_ExitImmersive");
        ToolTipService.SetToolTip(ExitImmersiveButton, UiLanguage.Text("Workspace_ExitImmersive") + " (Esc)");
        ExitImmersiveButton.Click += (_, _) => SetImmersiveMode(false);
        Root.KeyDown += (_, args) =>
        {
            // WebView 内的 Esc 先交给地图面板处理，随后由地图通知宿主退出。
            if (!_immersive || args.Key != VirtualKey.Escape || MapView.FocusState != FocusState.Unfocused) return;
            if (Microsoft.UI.Xaml.Media.VisualTreeHelper.GetOpenPopupsForXamlRoot(Root.XamlRoot).Count > 0) return;
            SetImmersiveMode(false);
            args.Handled = true;
        };
    }

    private void SetImmersiveMode(bool immersive)
    {
        if (_immersive == immersive) return;
        if (immersive) _panelsBeforeImmersive = (_settingsOpen, _inspectorOpen);
        else (_settingsOpen, _inspectorOpen) = _panelsBeforeImmersive;
        _immersive = immersive;
        if (_immersiveTip is not null) _immersiveTip.IsOpen = false;
        if (_simplifiedRibbonIntro is not null) _simplifiedRibbonIntro.IsOpen = false;
        Ribbon.Visibility = immersive ? Visibility.Collapsed : Visibility.Visible;
        MapToolbarHeader.Visibility = immersive ? Visibility.Collapsed : Visibility.Visible;
        StatusBar.Visibility = immersive ? Visibility.Collapsed : Visibility.Visible;
        LanguageButton.Visibility = immersive ? Visibility.Collapsed : Visibility.Visible;
        ExitImmersiveButton.Visibility = immersive ? Visibility.Visible : Visibility.Collapsed;
        UpdateScaleAndLayout();
        SendPreviewUiState();
        if (immersive && _compatibilityPreview) CompatibilitySurface.Focus(FocusState.Programmatic);
        else if (immersive) MapView.Focus(FocusState.Programmatic);
        else if (!immersive) WorkspaceModeButton.Focus(FocusState.Programmatic);
    }

    private void ShowImmersiveSuggestion()
    {
        if (_immersive || _context == "metro" || Root.XamlRoot is null) return;
        if (_immersiveTip is null)
        {
            _immersiveTip = new TeachingTip
            {
                Target = WorkspaceModeButton,
                Title = UiLanguage.Text("Workspace_Immersive"),
                Subtitle = UiLanguage.Text("Workspace_ImmersiveHint"),
                ActionButtonContent = UiLanguage.Text("Workspace_TryImmersive"),
                CloseButtonContent = UiLanguage.Text("Ribbon_IntroLater"),
                IsLightDismissEnabled = true,
                PreferredPlacement = TeachingTipPlacementMode.BottomRight,
            };
            _immersiveTip.ActionButtonClick += (_, _) => SetImmersiveMode(true);
            Root.Children.Add(_immersiveTip);
        }
        _immersiveTip.IsOpen = true;
    }
}
