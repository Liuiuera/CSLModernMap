// 定位、迁移和管理用户配置目录。
using System.Text.Json;
using System.Text.Json.Nodes;

namespace CSLModernMapWinUI;

internal static class UserData
{
    internal static string DirectoryPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CSLModernMap", "UserData");

    internal static void Prepare(string[] arguments, string engineDirectory)
    {
        var map = arguments.FirstOrDefault(File.Exists);
        var sources = new[]
        {
            map is null ? Environment.CurrentDirectory : Path.GetDirectoryName(Path.GetFullPath(map))!,
            engineDirectory, AppContext.BaseDirectory,
        };
        Migrate(DirectoryPath, sources, engineDirectory,
            Path.Combine(Path.GetDirectoryName(DirectoryPath)!, "preferences"));
    }

    // 将旧用户配置搬到固定目录，并在确认目标可读取后清理旧文件。
    internal static void Migrate(string target, IEnumerable<string> sources, string engineDirectory, string preferences)
    {
        var settingsPath = Path.Combine(target, "settings.json");
        if (File.Exists(settingsPath))
        {
            ReadSettings(settingsPath);
            return;
        }

        var roots = sources.Select(Path.GetFullPath).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        var source = roots.FirstOrDefault(root => File.Exists(Path.Combine(root, "settings.json")));
        var settings = source is null
            ? new JsonObject { ["version"] = 3, ["values"] = new JsonObject() }
            : ReadSettings(Path.Combine(source, "settings.json"));
        var oldPreferences = Path.Combine(preferences, "settings.json");
        if (File.Exists(oldPreferences))
        {
            var language = ReadSettings(oldPreferences)["values"]!["language"];
            if (language is not null) settings["values"]!["language"] = language.DeepClone();
        }

        Directory.CreateDirectory(target);
        var migrated = new List<string>();
        void Copy(string from, string to, bool removeSource)
        {
            if (!File.Exists(from) || Path.GetFullPath(from).Equals(Path.GetFullPath(to), StringComparison.OrdinalIgnoreCase)) return;
            Directory.CreateDirectory(Path.GetDirectoryName(to)!);
            // 上次中断的迁移可能已复制此文件。
            if (File.Exists(to))
            {
                if (!File.ReadAllBytes(from).AsSpan().SequenceEqual(File.ReadAllBytes(to)))
                    throw new IOException($"配置文件已存在且内容不同：{to}");
            }
            else File.Copy(from, to);
            if (removeSource) migrated.Add(from);
        }
        void CopyDirectory(string from, string to, bool removeSource)
        {
            if (!Directory.Exists(from)) return;
            foreach (var path in Directory.EnumerateFiles(from, "*", SearchOption.AllDirectories))
                Copy(path, Path.Combine(to, Path.GetRelativePath(from, path)), removeSource);
        }

        var configurationRoot = source ?? roots.FirstOrDefault();
        if (configurationRoot is not null)
            foreach (var name in new[] { "views.json", "poi_mapping.override.yaml", "poi_mapping.yaml" })
                Copy(Path.Combine(configurationRoot, name), Path.Combine(target, name), true);
        foreach (var root in roots)
            foreach (var name in new[] { "custom_fonts", "user_icon_libraries" })
                CopyDirectory(Path.Combine(root, name), Path.Combine(target, name), true);
        // 旧版本允许直接编辑安装目录中的文件。
        // 保留安装目录副本，让引擎仍能读取默认资源。
        CopyDirectory(Path.Combine(engineDirectory, "styles"), Path.Combine(target, "styles"), false);
        var mapping = Path.Combine(engineDirectory, "csmodernmap", "services", "poi", "poi_mapping.yaml");
        if (!File.Exists(Path.Combine(target, "poi_mapping.yaml")))
            Copy(mapping, Path.Combine(target, "poi_mapping.yaml"), false);

        var temporary = settingsPath + ".tmp";
        File.WriteAllText(temporary, settings.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        ReadSettings(temporary);
        File.Move(temporary, settingsPath);
        if (source is not null) migrated.Add(Path.Combine(source, "settings.json"));
        if (File.Exists(oldPreferences)) migrated.Add(oldPreferences);
        foreach (var path in migrated) File.Delete(path);
    }

    private static JsonObject ReadSettings(string path)
    {
        var settings = JsonNode.Parse(File.ReadAllText(path)) as JsonObject;
        if (settings?["values"] is not JsonObject)
            throw new InvalidDataException($"配置文件格式无效：{path}");
        return settings;
    }
}
