using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.Web.WebView2.Core;

namespace CSLModernMapWinUI;

// Owns only the lightweight demonstration; closing its window releases both sample maps.
internal sealed class PreviewQualityDemo : Grid, IDisposable
{
    private readonly WebView2 _view = new() { Name = "PreviewQualityDemoView" };
    private readonly TextBlock _error = new() { TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Bottom };
    private readonly Uri _url;
    private readonly string _profile;
    private object? _state;
    private bool _started, _closed;

    internal PreviewQualityDemo(Uri viewerUrl, string profile)
    {
        _url = new Uri(viewerUrl, "quality-demo.html"); _profile = profile;
        Children.Add(_view); Children.Add(_error);
        SizeChanged += (_, _) => Resize();
        Loaded += async (_, _) =>
        {
            if (_started || _closed) return;
            _started = true;
            try
            {
                Directory.CreateDirectory(_profile);
                var environment = await CoreWebView2Environment.CreateWithOptionsAsync(null, _profile, new CoreWebView2EnvironmentOptions());
                if (_closed) return;
                await _view.EnsureCoreWebView2Async(environment);
                if (_closed) return;
                var core = _view.CoreWebView2;
                core.Settings.AreDefaultContextMenusEnabled = false;
                core.Settings.AreDevToolsEnabled = false;
                core.WebMessageReceived += (_, args) =>
                {
                    if (_closed) return;
                    using var message = JsonDocument.Parse(args.TryGetWebMessageAsString());
                    var type = message.RootElement.GetProperty("type").GetString();
                    if (type == "qualityDemoReady") Send();
                    else if (type == "qualityDemoError") _error.Text = message.RootElement.GetProperty("message").GetString();
                };
                core.NavigationCompleted += (_, args) =>
                {
                    if (_closed) return;
                    if (args.IsSuccess) Send();
                    else _error.Text = args.WebErrorStatus.ToString();
                };
                _view.Source = _url;
            }
            catch (Exception error) { if (!_closed) _error.Text = error.Message; }
        };
        Resize();
    }

    internal void Update(PreviewQualitySettings quality, string baselineLabel, string draftLabel, ElementTheme theme)
    {
        _state = new { type = "setQualityDemo", quality, baselineLabel, draftLabel,
            language = UiLanguage.Language, appearance = theme == ElementTheme.Dark ? "dark" : "light" };
        Send();
    }

    private void Resize() => Height = ActualWidth is > 0 and < 450 ? 600 : 340;
    private void Send()
    {
        if (!_closed && _state is not null && _view.CoreWebView2 is { } core)
            core.PostWebMessageAsJson(JsonSerializer.Serialize(_state, PreviewQualitySettings.JsonOptions));
    }

    public void Dispose() { _closed = true; _view.Close(); }
}
