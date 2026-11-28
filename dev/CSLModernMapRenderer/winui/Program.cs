// 启动 WinUI 程序并配置进程级服务。
using System.Diagnostics;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;

namespace CSLModernMapWinUI;

public static class Program
{
    [STAThread]
    public static int Main(string[] args)
    {
        // CS1 和 CS2 通过 gui 加文件路径启动；旧命令行操作仍由 Python 程序处理。
        if (args.Length > 0 && (args[0].StartsWith('-') || args[0] is "engine" or "self-check" or "parse" or
            "render" or "transit" or "random" or "interchange" or "spot" or "list-styles" or "export-web"))
            return ForwardCli(args);

        WinRT.ComWrappersSupport.InitializeComWrappers();
        Application.Start(_ =>
        {
            SynchronizationContext.SetSynchronizationContext(
                new DispatcherQueueSynchronizationContext(DispatcherQueue.GetForCurrentThread()));
            new App();
        });
        return 0;
    }

    private static int ForwardCli(string[] args)
    {
        var (executable, engineArgs, directory) = EngineClient.FindEngine();
        using var process = new Process();
        process.StartInfo = new ProcessStartInfo(executable)
        {
            WorkingDirectory = directory,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8,
            StandardErrorEncoding = System.Text.Encoding.UTF8,
            CreateNoWindow = true,
        };
        if (engineArgs.StartsWith("main.py ")) process.StartInfo.ArgumentList.Add("main.py");
        foreach (var arg in args) process.StartInfo.ArgumentList.Add(arg);
        process.Start();
        var stdout = process.StandardOutput.BaseStream.CopyToAsync(Console.OpenStandardOutput());
        var stderr = process.StandardError.BaseStream.CopyToAsync(Console.OpenStandardError());
        Task.WaitAll(stdout, stderr);
        process.WaitForExit();
        return process.ExitCode;
    }
}
