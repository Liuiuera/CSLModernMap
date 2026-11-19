// 定位、迁移和管理用户配置目录。
using System.Text.Json;
using System.Text.Json.Nodes;

namespace CSLModernMapWinUI;

internal enum MapWorkflow { Theme, Export }

internal static class UserData
{
    private static string WorkflowPath(string directory, MapWorkflow workflow) => Path.Combine(directory,
        workflow == MapWorkflow.Theme ? "theme-editor-choice.json" : "export-choice.json");

    internal static bool? ReadWorkflowChoice(string directory, MapWorkflow workflow)
    {
        var path = WorkflowPath(directory, workflow);
        if (!File.Exists(path)) return null;
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        return document.RootElement.GetProperty("useDraft").GetBoolean();
    }

    internal static void RememberWorkflowChoice(string directory, MapWorkflow workflow, bool useDraft)
    {
        Directory.CreateDirectory(directory);
        var path = WorkflowPath(directory, workflow);
        var temporary = Path.Combine(directory, Path.GetRandomFileName());
        try
        {
            File.WriteAllText(temporary, new JsonObject { ["useDraft"] = useDraft }.ToJsonString());
            File.Move(temporary, path, overwrite: true);
        }
        finally { File.Delete(temporary); }
    }

    internal static void ResetWorkflowChoices(string directory)
    {
        foreach (var workflow in Enum.GetValues<MapWorkflow>())
        {
            var path = WorkflowPath(directory, workflow);
            if (File.Exists(path)) File.Delete(path);
        }
    }

    internal sealed record RibbonLayout(string DisplayMode, string VisibilityMode, bool SimplifiedIntroSeen = false);

    internal static bool ShouldOfferSimplifiedIntro(RibbonLayout layout, Version version) =>
        version >= new Version(6, 7, 3, 0) && layout.DisplayMode == "Classic" && !layout.SimplifiedIntroSeen;

    internal static string DirectoryPath => Environment.GetEnvironmentVariable("CSLMM_USER_DATA_DIR") is { Length: > 0 } directory
        ? Path.GetFullPath(directory)
        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CSLModernMap", "UserData");

    internal static bool ReadAutoPreview(string directory)
    {
        var path = Path.Combine(directory, "preview-startup.json");
        if (!File.Exists(path)) return true;
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        if (document.RootElement.ValueKind != JsonValueKind.Object ||
            !document.RootElement.TryGetProperty("autoPreview", out var enabled) ||
            enabled.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
            throw new InvalidDataException($"Invalid preview startup settings: {path}");
        return enabled.GetBoolean();
    }

    internal static PreviewQualitySettings ReadPreviewQuality(string directory)
    {
        var path = Path.Combine(directory, "preview-quality.json");
        if (!File.Exists(path)) return new();
        var settings = JsonSerializer.Deserialize<PreviewQualitySettings>(File.ReadAllText(path), PreviewQualitySettings.JsonOptions)
            ?? throw new InvalidDataException($"Invalid preview quality settings: {path}");
        settings.Validate();
        return settings;
    }

    internal static void WritePreviewQuality(string directory, PreviewQualitySettings settings)
    {
        settings.Validate();
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, "preview-quality.json");
        File.WriteAllText(path + ".tmp", JsonSerializer.Serialize(settings, PreviewQualitySettings.JsonOptions));
        File.Move(path + ".tmp", path, overwrite: true);
    }

    internal static void WriteAutoPreview(string directory, bool enabled)
    {
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, "preview-startup.json");
        File.WriteAllText(path + ".tmp", JsonSerializer.Serialize(new { autoPreview = enabled }));
        File.Move(path + ".tmp", path, overwrite: true);
    }

    internal static bool ReadMapInteractionGuideSeen(string directory)
    {
        var path = Path.Combine(directory, "preview-ui.json");
        if (!File.Exists(path)) return false;
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        if (document.RootElement.ValueKind != JsonValueKind.Object ||
            !document.RootElement.TryGetProperty("mapInteractionGuideSeen", out var seen) ||
            seen.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
            throw new InvalidDataException($"地图操作提示配置格式无效：{path}");
        return seen.GetBoolean();
    }

    internal static void WriteMapInteractionGuideSeen(string directory)
    {
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, "preview-ui.json");
        File.WriteAllText(path + ".tmp", "{\"mapInteractionGuideSeen\":true}");
        File.Move(path + ".tmp", path, overwrite: true);
    }

    internal static bool DraftWelcomeSeen(string directory) => File.Exists(Path.Combine(directory, "draft-welcome.seen"));

    internal static void MarkDraftWelcomeSeen(string directory)
    {
        Directory.CreateDirectory(directory);
        using var marker = File.Open(Path.Combine(directory, "draft-welcome.seen"), FileMode.OpenOrCreate,
            FileAccess.Write, FileShare.ReadWrite);
    }

    internal static RibbonLayout ReadRibbonLayout(string directory)
    {
        var path = Path.Combine(directory, "ribbon-layout.json");
        if (!File.Exists(path)) return new("Classic", "AlwaysShow");
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object ||
            !root.TryGetProperty("displayMode", out var display) || display.ValueKind != JsonValueKind.String ||
            !root.TryGetProperty("visibilityMode", out var visibility) || visibility.ValueKind != JsonValueKind.String ||
            display.GetString() is not ("Classic" or "Simplified") ||
            visibility.GetString() is not ("AlwaysShow" or "TabsOnly" or "FullScreen" or "PanelButtons" or "PanelTitles"))
            throw new InvalidDataException($"Ribbon 布局配置格式无效：{path}");
        var seen = false;
        if (root.TryGetProperty("simplifiedIntroSeen", out var intro))
        {
            if (intro.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                throw new InvalidDataException($"Ribbon 布局配置格式无效：{path}");
            seen = intro.GetBoolean();
        }
        return new(display.GetString()!, visibility.GetString()!, seen);
    }

    internal static void WriteRibbonLayout(string directory, RibbonLayout layout)
    {
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, "ribbon-layout.json");
        var json = new JsonObject
        {
            ["displayMode"] = layout.DisplayMode, ["visibilityMode"] = layout.VisibilityMode,
            ["simplifiedIntroSeen"] = layout.SimplifiedIntroSeen,
        };
        File.WriteAllText(path + ".tmp", json.ToJsonString());
        File.Move(path + ".tmp", path, overwrite: true);
    }

    internal static string[] Prepare(string[] arguments, string engineDirectory)
    {
        var map = arguments.FirstOrDefault(File.Exists);
        var sources = new[]
        {
            map is null ? Environment.CurrentDirectory : Path.GetDirectoryName(Path.GetFullPath(map))!,
            engineDirectory, AppContext.BaseDirectory,
        };
        var legacySources = sources.Where(root => File.Exists(Path.Combine(root, "settings.json")))
            .Select(Path.GetFullPath).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        Migrate(DirectoryPath, sources, engineDirectory,
            Path.Combine(Path.GetDirectoryName(DirectoryPath)!, "preferences"));
        var target = ReadSettings(Path.Combine(DirectoryPath, "settings.json"));
        return target["version"]?.GetValue<double>() == 3.1 ? [] : legacySources;
    }

    // 将旧用户配置搬到固定目录，并在确认目标可读取后清理旧文件。
    internal static void Migrate(string target, IEnumerable<string> sources, string engineDirectory, string preferences)
    {
        var roots = sources.Append(engineDirectory).Select(Path.GetFullPath).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        var settingsPath = Path.Combine(target, "settings.json");
        if (File.Exists(settingsPath))
        {
            ReadSettings(settingsPath);
            return;
        }

        var source = roots.FirstOrDefault(root => File.Exists(Path.Combine(root, "settings.json")));
        var historicFiles = new[] { "styles", "legacy-themes" }.Any(name =>
            Directory.Exists(Path.Combine(target, name)) && Directory.EnumerateFiles(Path.Combine(target, name), "*", SearchOption.AllDirectories)
                .Any(path => Path.GetExtension(path).ToLowerInvariant() is ".yaml" or ".yml"));
        var settings = source is null
            ? new JsonObject { ["version"] = historicFiles ? 3d : 3.1d, ["values"] = new JsonObject() }
            : ReadSettings(Path.Combine(source, "settings.json"));
        var preserveLegacy = source is not null && settings["version"]?.GetValue<double>() != 3.1;
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
                Copy(Path.Combine(configurationRoot, name), Path.Combine(target, name), name != "views.json" || !preserveLegacy);
        foreach (var root in roots)
            foreach (var name in new[] { "custom_fonts", "user_icon_libraries" })
                CopyDirectory(Path.Combine(root, name), Path.Combine(target, name), true);
        var mapping = Path.Combine(engineDirectory, "csmodernmap", "services", "poi", "poi_mapping.yaml");
        if (!File.Exists(Path.Combine(target, "poi_mapping.yaml")))
            Copy(mapping, Path.Combine(target, "poi_mapping.yaml"), false);

        var temporary = settingsPath + ".tmp";
        File.WriteAllText(temporary, settings.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        ReadSettings(temporary);
        File.Move(temporary, settingsPath);
        if (source is not null && !preserveLegacy) migrated.Add(Path.Combine(source, "settings.json"));
        if (File.Exists(oldPreferences)) migrated.Add(oldPreferences);
        foreach (var path in migrated) File.Delete(path);
    }

    private static JsonObject ReadSettings(string path)
    {
        var settings = JsonNode.Parse(File.ReadAllText(path)) as JsonObject;
        if (settings?["values"] is not JsonObject)
            throw new InvalidDataException($"配置文件格式无效：{path}");
        if (settings["version"] is { } version &&
            (version is not JsonValue number || !number.TryGetValue<double>(out var value) || value is not (1 or 2 or 3 or 3.1)))
            throw new InvalidDataException($"不支持的配置版本：{path}");
        return settings;
    }
}
