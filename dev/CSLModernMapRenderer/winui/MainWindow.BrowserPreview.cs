// 将当前地图快照直接交给系统浏览器。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage;
using Windows.System;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private string? _browserPreviewRequestId;

    private async Task OpenBrowserPreviewAsync()
    {
        _browserPreviewRequestId = Guid.NewGuid().ToString("N");
        _externalExporting = true;
        ApplyMapTaskState();
        try
        {
            var result = await _engine.RequestAsync("openBrowserPreview", new
            {
                requestId = _browserPreviewRequestId, documentEpoch = _engineDocumentEpoch,
                context = _context == "transit" ? "transit" : "normal",
            });
            if (!result.GetProperty("started").GetBoolean())
                throw new InvalidOperationException(UiLanguage.Text("MainWindow_Status_CompilePreviewAsync_2A55669135"));
        }
        catch
        {
            _browserPreviewRequestId = null;
            _externalExporting = false;
            ApplyMapTaskState();
            throw;
        }
    }

    private async Task OpenBrowserPreviewResultAsync(JsonElement payload)
    {
        _browserPreviewRequestId = null;
        _externalExporting = false;
        var path = payload.GetProperty("path").GetString()!;
        try
        {
            var file = await StorageFile.GetFileFromPathAsync(path);
            if (!await Launcher.LaunchFileAsync(file)) throw new IOException(UiLanguage.Text("Preview_BrowserOpenFailed"));
        }
        catch (Exception error)
        {
            SetError(error);
            var actions = new StackPanel { Spacing = 6 };
            actions.Children.Add(new TextBlock { Text = path, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true });
            var folder = new Button { Content = UiLanguage.Text("Preview_OpenFolder") };
            folder.Click += async (_, _) => await RunAsync(async () =>
            {
                await Launcher.LaunchFolderAsync(await StorageFolder.GetFolderFromPathAsync(Path.GetDirectoryName(path)!));
            });
            actions.Children.Add(folder);
            OperationError.Content = actions;
        }
        ApplyMapTaskState();
    }
}
