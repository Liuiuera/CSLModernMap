// 初始化 WinUI 应用并管理应用级生命周期。
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public partial class App : Application
{
    private Window? _window;

    public App()
    {
        UnhandledException += (_, args) => StartupTrace.Write("Unhandled UI exception: " + args.Exception);
        InitializeComponent();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _ = StartMainWindowAsync(Environment.GetCommandLineArgs().Skip(1).ToArray());
    }

    private async Task StartMainWindowAsync(string[] arguments)
    {
        var engine = new EngineClient();
        var cancellation = new CancellationTokenSource();
        var spinner = new ProgressRing { IsActive = true, Width = 32, Height = 32 };
        var errorText = new TextBlock { TextWrapping = TextWrapping.Wrap, Visibility = Visibility.Collapsed };
        var content = new StackPanel
        {
            Spacing = 16, Margin = new Thickness(24), VerticalAlignment = VerticalAlignment.Center,
            Children = { new TextBlock { Text = "CSLModernMap", FontSize = 20, HorizontalAlignment = HorizontalAlignment.Center }, spinner, errorText },
        };
        var startup = new Window { Title = "CSLModernMap", Content = content };
        startup.AppWindow.Resize(new Windows.Graphics.SizeInt32(420, 240));
        var handedOff = false;
        var closing = false;
        var allowClose = false;
        startup.AppWindow.Closing += async (_, args) =>
        {
            if (allowClose) return;
            args.Cancel = true;
            if (closing) return;
            closing = true;
            cancellation.Cancel();
            await engine.CloseAsync();
            allowClose = true;
            startup.Close();
        };
        startup.Closed += (_, _) => cancellation.Dispose();
        ShowWindow(startup);
        try
        {
            await engine.StartAsync(arguments);
            var metadata = await engine.RequestAsync("getUiMetadata", cancellation: cancellation.Token);
            cancellation.Token.ThrowIfCancellationRequested();
            UiLanguage.Configure(metadata);
            var main = new MainWindow(arguments, engine, metadata);
            ShowWindow(main);
            handedOff = true;
            allowClose = true;
            startup.Close();
            _ = Task.Run(() => RendererCleanup.Run(AppContext.BaseDirectory, StartupTrace.Write));
        }
        catch (OperationCanceledException) when (closing) { }
        catch (Exception error)
        {
            StartupTrace.Write("Startup failed: " + error);
            if (!closing)
            {
                spinner.IsActive = false;
                spinner.Visibility = Visibility.Collapsed;
                errorText.Text = error.Message;
                errorText.Visibility = Visibility.Visible;
            }
        }
        finally
        {
            if (!handedOff) await engine.CloseAsync();
        }
    }

    internal void ShowWindow(Window window)
    {
        _window = window;
        window.Activate();
    }
}
