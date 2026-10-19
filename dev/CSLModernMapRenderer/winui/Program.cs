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
        if (args.Length > 0 && args[0] != "gui" && !File.Exists(args[0]))
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
        string executable;
        string directory;
        var bundled = Path.Combine(AppContext.BaseDirectory, "engine", "CSLModernMapEngine.exe");
        if (File.Exists(bundled))
        {
            executable = bundled;
            directory = Path.GetDirectoryName(bundled)!;
        }
        else
        {
            var root = new DirectoryInfo(AppContext.BaseDirectory);
            string? main = null;
            for (; root != null; root = root.Parent)
            {
                var candidate = Path.Combine(root.FullName, "dev", "CSLModernMapRenderer", "main.py");
                if (File.Exists(candidate)) { main = candidate; break; }
            }
            if (main is null) return 2;
            executable = "python";
            directory = Path.GetDirectoryName(main)!;
        }
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
        if (executable == "python") process.StartInfo.ArgumentList.Add("main.py");
        foreach (var arg in args) process.StartInfo.ArgumentList.Add(arg);
        process.Start();
        var stdout = process.StandardOutput.BaseStream.CopyToAsync(Console.OpenStandardOutput());
        var stderr = process.StandardError.BaseStream.CopyToAsync(Console.OpenStandardError());
        Task.WaitAll(stdout, stderr);
        process.WaitForExit();
        return process.ExitCode;
    }
}
