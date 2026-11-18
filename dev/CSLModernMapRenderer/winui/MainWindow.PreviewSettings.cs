// 处理地图预览设置的界面交互。
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private EditorWindow? _previewSettingsWindow;

    private async Task ShowPreviewSettingsAsync()
    {
        if (_previewSettingsWindow is { } existing) { existing.Activate(); return; }
        if (_engineUnavailable || !_openingEditors.Add("preview-settings")) return;
        try
        {
            var generation = _documentGeneration;
            var data = await _engine.RequestAsync("getPreviewSettings");
            if (_closing || generation != _documentGeneration) return;
            var window = _previewSettingsWindow = new EditorWindow(UiLanguage.Text("MainWindow_PreviewSettings_ShowPreviewSettingsAsync_3279E8322A"), 480, 330, translucent: true, owner: this);
            var diagnostics = AppearanceControls.Toggle(UiLanguage.Text("MainWindow_PreviewSettings_ShowPreviewSettingsAsync_F15F7C4642"), data.GetProperty("performanceDiagnostics").GetBoolean());
            var autoPreview = AppearanceControls.Toggle(UiLanguage.Text("Preview_AutoStart"), UserData.ReadAutoPreview(_ribbonLayoutDirectory));
            var status = new TextBlock { TextWrapping = TextWrapping.Wrap };
            var body = new StackPanel { Spacing = 16 };
            body.Children.Add(diagnostics);
            body.Children.Add(autoPreview);
            body.Children.Add(new TextBlock
            {
                Text = UiLanguage.Text("MainWindow_PreviewSettings_ShowPreviewSettingsAsync_BD4DD78F2D"),
                TextWrapping = TextWrapping.Wrap,
            });
            body.Children.Add(status);
            window.SetBody(body);
            window.ActionsRightAligned = true;
            window.AddAction(UiLanguage.Text("MainWindow_PreviewSettings_ShowPreviewSettingsAsync_BA2E93E730"), () => { diagnostics.IsOn = false; autoPreview.IsOn = true; return Task.CompletedTask; });
            window.AddAction(UiLanguage.Text("MainWindow_PreviewSettings_ShowPreviewSettingsAsync_2CD0F3BE87"), () => window.RequestCloseAsync());
            window.AddAction(UiLanguage.Text("MainWindow_PreviewSettings_ShowPreviewSettingsAsync_63C73C4730"), async () =>
            {
                if (_closing || generation != _documentGeneration) { status.Text = UiLanguage.Text("MainWindow_PreviewSettings_ShowPreviewSettingsAsync_8B710BEF56"); return; }
                try
                {
                    await _engine.RequestAsync("setPreviewSettings", new
                    {
                        performanceDiagnostics = diagnostics.IsOn,
                        documentEpoch = data.GetProperty("documentEpoch").GetInt32(),
                    });
                    UserData.WriteAutoPreview(_ribbonLayoutDirectory, autoPreview.IsOn);
                    await window.RequestCloseAsync();
                }
                catch (Exception error) { status.Text = UiLanguage.Text("Failure_Operation"); SetError(error); }
            });
            try { await window.ShowAsync(); }
            finally { if (_previewSettingsWindow == window) _previewSettingsWindow = null; }
        }
        finally { _openingEditors.Remove("preview-settings"); }
    }
}
