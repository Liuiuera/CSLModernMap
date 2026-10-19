// 处理导出窗口中的 RMP 图像选项。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

internal sealed record RmpExportOptions(double Scale = 100, bool Transparent = false, double SvgVersion = 2);

internal sealed partial class ExportWindow
{
    private readonly Func<string, string, RmpExportOptions, Task>? _exportRmp;
    private readonly ComboBox _rmpScale = new() { Header = UiLanguage.Text("ExportWindow_RmpScale"), HorizontalAlignment = HorizontalAlignment.Stretch };
    private readonly CheckBox _rmpTransparent = new() { Content = UiLanguage.Text("ExportWindow_RmpTransparent") };
    private readonly ComboBox _rmpSvgVersion = new() { Header = UiLanguage.Text("ExportWindow_RmpSvgVersion"), HorizontalAlignment = HorizontalAlignment.Stretch };

    private static JsonElement RmpExportProfile(JsonElement source)
    {
        var profile = JsonNode.Parse(source.GetRawText())!.AsObject();
        profile["formats"] = new JsonArray("PNG", "SVG");
        profile["output"] = ReadOutput(source);
        profile["output"]!["format"] = "PNG";
        var capabilities = new JsonObject();
        foreach (var format in new[] { "PNG", "SVG" })
            capabilities[format] = JsonSerializer.SerializeToNode(new
            {
                context = "metro", targetKind = "file", showsOutputOptions = true,
                showsRange = false, showsPoiProfile = false, showsSharedLayers = false, showsTransitLines = false,
                resultImage = format == "PNG", outputProfileTarget = (string?)null,
                outputFields = format == "PNG" ? new[] { "scale", "transparent" } : new[] { "svgVersion" },
            });
        profile["formatCapabilities"] = capabilities;
        return JsonSerializer.SerializeToElement(profile);
    }

    private void BuildRmpExportControls(StackPanel image)
    {
        foreach (var scale in new[] { 25, 50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000 })
            _rmpScale.Items.Add(new ComboBoxItem { Content = $"{scale}%", Tag = (double)scale });
        _rmpScale.SelectedIndex = 2;
        foreach (var version in new[] { 2.0, 1.1 })
            _rmpSvgVersion.Items.Add(new ComboBoxItem { Content = version == 2 ? "SVG 2.0" : "SVG 1.1", Tag = version });
        _rmpSvgVersion.SelectedIndex = 0;
        image.Children.Add(_rmpScale);
        image.Children.Add(_rmpTransparent);
        image.Children.Add(_rmpSvgVersion);
        _editable.AddRange([_rmpScale, _rmpTransparent, _rmpSvgVersion]);
    }

    private void UpdateRmpExportControls()
    {
        _rmpScale.Visibility = _rmpTransparent.Visibility = SelectedFormat == "PNG" ? Visibility.Visible : Visibility.Collapsed;
        _rmpSvgVersion.Visibility = SelectedFormat == "SVG" ? Visibility.Visible : Visibility.Collapsed;
    }

    private async Task ExportRmpFileAsync(string target)
    {
        var format = SelectedFormat;
        var options = new RmpExportOptions((double)((ComboBoxItem)_rmpScale.SelectedItem).Tag,
            _rmpTransparent.IsChecked == true, (double)((ComboBoxItem)_rmpSvgVersion.SelectedItem).Tag);
        _outputDirectory = Path.GetDirectoryName(target)!;
        SetBusy(true);
        _status.Text = UiLanguage.Text("ExportWindow_StartAsync_62731D95BC");
        await _exportRmp!(target, format.ToLowerInvariant(), options);
        _latestResultPath = target;
        _latestResultFormat = format;
        _latestSuccessfulPng = format == "PNG" ? target : null;
        SetBusy(false);
        _progress.IsIndeterminate = false;
        _progress.Value = 100;
        _status.Text = UiLanguage.Text("ExportWindow_HandleEventAsync_76AA0EDA7A") + target;
        UpdateResultActions();
        if (_autoOpen.IsChecked == true) await OpenResultFolderAsync();
    }
}
