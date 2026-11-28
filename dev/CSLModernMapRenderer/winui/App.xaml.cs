// 初始化 WinUI 应用并管理应用级生命周期。
using Microsoft.UI.Xaml;

namespace CSLModernMapWinUI;

public partial class App : Application
{
    private Window? _window;

    public App()
    {
        UnhandledException += (_, args) => StartupTrace.Write("Unhandled UI exception: " + args.Exception);
        InitializeComponent();
    }

    protected override async void OnLaunched(LaunchActivatedEventArgs args) =>
        ShowWindow(await CreateMainWindowAsync(Environment.GetCommandLineArgs().Skip(1).ToArray()));

    internal static async Task<MainWindow> CreateMainWindowAsync(string[] arguments, bool showLanguageNotice = false)
    {
        var engine = new EngineClient();
        Exception? startupError = null;
        try
        {
            await engine.StartAsync(arguments);
            var hello = await engine.RequestAsync("hello", new { protocolVersion = 1 });
            UiLanguage.Configure(hello);
        }
        catch (Exception error) { startupError = error; }
        return new MainWindow(arguments, engine, startupError, showLanguageNotice);
    }

    internal void ShowWindow(Window window)
    {
        _window = window;
        window.Activate();
    }
}
