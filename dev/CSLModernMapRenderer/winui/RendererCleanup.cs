using System.ComponentModel;
using System.Diagnostics;
using System.Text.RegularExpressions;

namespace CSLModernMapWinUI;

internal static class RendererCleanup
{
    private const string Executable = "CSLModernMapRenderer.exe";
    private const string Marker = ".installed.sha256";
    private const string Manifest = "renderer-manifest.sha256";
    private static readonly Regex VersionName = new(
        @"^(\d+\.\d+\.\d+)(f(?:[1-9]\d*)?)?(\.previous(?:-[0-9a-f]{32})?)?$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    internal static void Run(string currentDirectory, Action<string> log)
    {
        try
        {
            var current = new DirectoryInfo(currentDirectory);
            var root = current.Parent;
            var version = VersionName.Match(current.Name);
            if (root is null || !root.Name.Equals("Renderer", StringComparison.OrdinalIgnoreCase)
                || !version.Success || version.Groups[3].Success
                || !TryVersion(version, out var currentVersion)
                || !File.Exists(Path.Combine(current.FullName, Marker))
                || !File.Exists(Path.Combine(current.FullName, Manifest))
                || (root.Attributes & FileAttributes.ReparsePoint) != 0
                || (current.Attributes & FileAttributes.ReparsePoint) != 0) return;

            var running = RunningPaths(log);
            if (running is null) return;
            foreach (var candidate in root.EnumerateDirectories())
            {
                var name = VersionName.Match(candidate.Name);
                if (!name.Success || !TryVersion(name, out var candidateVersion)) continue;
                var comparison = candidateVersion.CompareTo(currentVersion);
                if (comparison > 0 || (comparison == 0 && !name.Groups[3].Success)) continue;
                if (!File.Exists(Path.Combine(candidate.FullName, Marker))) continue;
                Delete(candidate, root.FullName, running, log);
            }

            if (root.Parent is { } maps)
            {
                var legacy = new DirectoryInfo(Path.Combine(maps.FullName, "CSLModernMapRenderer"));
                if (legacy.Exists && File.Exists(Path.Combine(legacy.FullName, Executable)))
                    Delete(legacy, maps.FullName, running, log);
            }
        }
        catch (IOException error) { log("Renderer cleanup deferred: " + error.Message); }
        catch (UnauthorizedAccessException error) { log("Renderer cleanup deferred: " + error.Message); }
    }

    private static bool TryVersion(Match name, out Version version)
    {
        var fix = name.Groups[2].Value;
        var revision = fix.Length == 0 ? "0" : fix.Length == 1 ? "1" : fix[1..];
        return Version.TryParse(name.Groups[1].Value + "." + revision, out version!);
    }

    private static List<string>? RunningPaths(Action<string> log)
    {
        var paths = new List<string>();
        foreach (var name in new[] { "CSLModernMapRenderer", "CSLModernMapEngine" })
        foreach (var process in Process.GetProcessesByName(name))
        {
            using (process)
            {
                try
                {
                    if (process.MainModule?.FileName is not { } path)
                    {
                        log("Renderer cleanup deferred: cannot identify process " + process.Id);
                        return null;
                    }
                    paths.Add(path);
                }
                catch (InvalidOperationException) { /* 进程已退出。 */ }
                catch (Win32Exception error)
                {
                    log("Renderer cleanup deferred: " + error.Message);
                    return null;
                }
            }
        }
        return paths;
    }

    private static void Delete(DirectoryInfo directory, string expectedParent, List<string> running, Action<string> log)
    {
        try
        {
            if (!string.Equals(directory.Parent?.FullName, expectedParent, StringComparison.OrdinalIgnoreCase)) return;
            var prefix = directory.FullName + Path.DirectorySeparatorChar;
            if (running.Any(path => path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)))
            {
                log("Renderer cleanup skipped (running): " + directory.FullName);
                return;
            }
            if (HasLinks(directory))
            {
                log("Renderer cleanup skipped (directory link): " + directory.FullName);
                return;
            }

            // 保留识别文件到最后，删除被占用文件失败后，下次仍能识别并重试。
            var identity = new[] { Executable, Manifest, Marker };
            foreach (var entry in directory.EnumerateFileSystemInfos())
            {
                if (identity.Contains(entry.Name, StringComparer.OrdinalIgnoreCase)) continue;
                if (entry is DirectoryInfo child) child.Delete(true);
                else entry.Delete();
            }
            File.Delete(Path.Combine(directory.FullName, Executable));
            File.Delete(Path.Combine(directory.FullName, Manifest));
            File.Delete(Path.Combine(directory.FullName, Marker));
            directory.Delete();
            log("Renderer cleanup removed: " + directory.FullName);
        }
        catch (IOException error) { log("Renderer cleanup deferred: " + directory.FullName + ": " + error.Message); }
        catch (UnauthorizedAccessException error) { log("Renderer cleanup deferred: " + directory.FullName + ": " + error.Message); }
    }

    private static bool HasLinks(DirectoryInfo directory)
    {
        if ((directory.Attributes & FileAttributes.ReparsePoint) != 0) return true;
        foreach (var entry in directory.EnumerateFileSystemInfos())
        {
            if ((entry.Attributes & FileAttributes.ReparsePoint) != 0) return true;
            if (entry is DirectoryInfo child && HasLinks(child)) return true;
        }
        return false;
    }
}
