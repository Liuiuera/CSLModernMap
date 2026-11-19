using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private ContentDialog? _satelliteGuidanceDialog;

    private async Task<int?> SatelliteTextureLimitAsync()
    {
        if (!_viewerReady) return null;
        var limit = await MapView.ExecuteScriptAsync("(() => { const gl = document.querySelector('.maplibregl-canvas').getContext('webgl2'); return gl.getParameter(gl.MAX_TEXTURE_SIZE); })()");
        return JsonSerializer.Deserialize<int>(limit);
    }

    private async Task ShowSatelliteGuidanceAsync()
    {
        if (_satelliteGuidanceDialog is not null || _closing) return;
        var generation = _documentGeneration;
        var context = _context;
        var available = _snapshot.GetProperty("hasDocument").GetBoolean() &&
            _snapshot.GetProperty("satelliteAvailable").GetBoolean();
        var body = new StackPanel { Spacing = 20, MaxWidth = 460 };
        body.Children.Add(new TextBlock { Text = UiLanguage.Text("SatelliteGuidance_Body"), TextWrapping = TextWrapping.Wrap });
        var rows = new StackPanel { Spacing = 16 };
        rows.Children.Add(SatelliteGuidanceRow("✓", "EnableTitle", "EnableLayers"));
        rows.Children.Add(SatelliteGuidanceRow("○", "OptionalTitle", "OptionalLayers", "Preserve"));
        rows.Children.Add(SatelliteGuidanceRow("−", "DisableTitle", "DisableLayers"));
        body.Children.Add(rows);
        body.Children.Add(new Expander
        {
            Header = UiLanguage.Text("SatelliteGuidance_Details"),
            HorizontalAlignment = HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Stretch,
            Content = new TextBlock { Text = UiLanguage.Text("SatelliteGuidance_DetailBody"), TextWrapping = TextWrapping.Wrap },
        });
        if (!available)
            body.Children.Add(new TextBlock { Text = UiLanguage.Text("SatelliteGuidance_Unavailable"), TextWrapping = TextWrapping.Wrap });
        var dialog = new ContentDialog
        {
            XamlRoot = Root.XamlRoot,
            Title = UiLanguage.Text("SatelliteGuidance_Title"),
            Content = new ScrollViewer { Content = body, MaxHeight = 440, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled },
            PrimaryButtonText = UiLanguage.Text("SatelliteGuidance_Apply"),
            CloseButtonText = UiLanguage.Text("SatelliteGuidance_Keep"),
            IsPrimaryButtonEnabled = available && context == "map",
            DefaultButton = available && context == "map" ? ContentDialogButton.Primary : ContentDialogButton.Close,
        };
        _satelliteGuidanceDialog = dialog;
        ContentDialogResult choice;
        try { choice = await dialog.ShowAsync(); }
        finally { _satelliteGuidanceDialog = null; }
        bool Current() => !_closing && generation == _documentGeneration && context == _context;
        if (choice != ContentDialogResult.Primary || !Current()) return;
        if (_themeEditor is { } editor && !await editor.RequestCloseAsync()) return;
        if (!Current()) return;
        var result = await _engine.RequestAsync("applySatelliteRecommendations", new
        {
            documentEpoch = _snapshot.GetProperty("documentEpoch").GetInt32(),
            maxTextureSize = await SatelliteTextureLimitAsync(),
        });
        foreach (var item in result.GetProperty("mapLayers").EnumerateObject())
            SendViewer(LayerVisibilityCommand.Create(item.Name, item.Value.GetBoolean(), _sceneId, _revision));
        await RefreshLivePreviewAsync();
        await RefreshSnapshotAsync();
    }

    private static Grid SatelliteGuidanceRow(string icon, string title, string layers, string? note = null)
    {
        var row = new Grid { ColumnSpacing = 12 };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(24) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        row.Children.Add(new TextBlock { Text = icon, FontSize = 20, HorizontalAlignment = HorizontalAlignment.Center });
        var text = new StackPanel { Spacing = 4 };
        text.Children.Add(new TextBlock { Text = UiLanguage.Text("SatelliteGuidance_" + title), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, TextWrapping = TextWrapping.Wrap });
        text.Children.Add(new TextBlock { Text = UiLanguage.Text("SatelliteGuidance_" + layers), TextWrapping = TextWrapping.Wrap });
        if (note is not null)
            text.Children.Add(new TextBlock { Text = UiLanguage.Text("SatelliteGuidance_" + note), FontSize = 12, Opacity = 0.7, TextWrapping = TextWrapping.Wrap });
        Grid.SetColumn(text, 1);
        row.Children.Add(text);
        return row;
    }
}
