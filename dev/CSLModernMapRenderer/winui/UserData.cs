// 定位、迁移和管理用户配置目录。
using System.Text.Json;
using System.Text.Json.Nodes;

namespace CSLModernMapWinUI;

internal enum MapWorkflow { Theme, Export }

internal static class UserData
{
    private static string WorkflowPath(string directory, MapWorkflow workflow) => Path.Combine(directory,
        workflow == MapWorkflow.Theme ? "theme-editor-choice.json" : "export-choice.json");

    private static JsonElement ReadObject(string path)
    {
        if (!File.Exists(path)) return default;
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            return document.RootElement.Clone();
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException)
        {
            StartupTrace.Write($"{path}: {error.Message}");
            return default;
        }
    }

    private static JsonValue? Field(JsonElement data, string key) =>
        data.ValueKind == JsonValueKind.Object && data.TryGetProperty(key, out var field) &&
        field.ValueKind is JsonValueKind.String or JsonValueKind.Number or JsonValueKind.True or JsonValueKind.False
            ? JsonValue.Create(field) : null;

    private static T Field<T>(JsonElement data, string key, T fallback) =>
        Field(data, key) is { } field && field.TryGetValue<T>(out var value) ? value : fallback;

    internal static bool? ReadWorkflowChoice(string directory, MapWorkflow workflow) =>
        Field(ReadObject(WorkflowPath(directory, workflow)), "useDraft") is { } field &&
        field.TryGetValue<bool>(out var value) ? value : null;

    private static void WriteJson<T>(string path, T value)
    {
        var directory = Path.GetDirectoryName(path)!;
        Directory.CreateDirectory(directory);
        var temporary = Path.Combine(directory, Path.GetRandomFileName());
        try
        {
            File.WriteAllText(temporary, JsonSerializer.Serialize(value, PreviewQualitySettings.JsonOptions));
            File.Move(temporary, path, overwrite: true);
        }
        finally { File.Delete(temporary); }
    }

    internal static void RememberWorkflowChoice(string directory, MapWorkflow workflow, bool useDraft) =>
        WriteJson(WorkflowPath(directory, workflow), new { useDraft });

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

    internal static bool ReadAutoPreview(string directory) =>
        Field(ReadObject(Path.Combine(directory, "preview-startup.json")), "autoPreview", true);

    internal static PreviewQualitySettings ReadPreviewQuality(string directory)
    {
        var data = ReadObject(Path.Combine(directory, "preview-quality.json"));
        var scale = Field(data, "pixelScale", 1d);
        string Detail(string key) => Field(data, key, "standard") == "fine" ? "fine" : "standard";
        int Percent(string key) => Field(data, key, 100) is >= 50 and <= 200 and var value ? value : 100;
        return new(scale is .75 or 1.25 ? scale : 1, Detail("lineDetail"), Detail("polygonDetail"),
            Percent("textPercent"), Percent("linePercent"), Percent("iconPercent"));
    }

    internal static void WritePreviewQuality(string directory, PreviewQualitySettings settings)
    {
        settings.Validate();
        WriteJson(Path.Combine(directory, "preview-quality.json"), settings);
    }

    internal static void WriteAutoPreview(string directory, bool enabled) =>
        WriteJson(Path.Combine(directory, "preview-startup.json"), new { autoPreview = enabled });

    internal static bool ReadMapInteractionGuideSeen(string directory) =>
        Field(ReadObject(Path.Combine(directory, "preview-ui.json")), "mapInteractionGuideSeen", false);

    internal static void WriteMapInteractionGuideSeen(string directory) =>
        WriteJson(Path.Combine(directory, "preview-ui.json"), new { mapInteractionGuideSeen = true });

    internal static bool DraftWelcomeSeen(string directory) => File.Exists(Path.Combine(directory, "draft-welcome.seen"));

    internal static void MarkDraftWelcomeSeen(string directory)
    {
        Directory.CreateDirectory(directory);
        using var marker = File.Open(Path.Combine(directory, "draft-welcome.seen"), FileMode.OpenOrCreate,
            FileAccess.Write, FileShare.ReadWrite);
    }

    internal static RibbonLayout ReadRibbonLayout(string directory)
    {
        var data = ReadObject(Path.Combine(directory, "ribbon-layout.json"));
        var visibility = Field(data, "visibilityMode", "AlwaysShow");
        return new(Field(data, "displayMode", "Classic") == "Simplified" ? "Simplified" : "Classic",
            visibility is "TabsOnly" or "FullScreen" or "PanelButtons" or "PanelTitles" ? visibility : "AlwaysShow",
            Field(data, "simplifiedIntroSeen", false));
    }

    internal static void WriteRibbonLayout(string directory, RibbonLayout layout) =>
        WriteJson(Path.Combine(directory, "ribbon-layout.json"), layout);

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
        try
        {
            Migrate(DirectoryPath, sources, engineDirectory, Path.Combine(Path.GetDirectoryName(DirectoryPath)!, "preferences"));
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or InvalidDataException)
        {
            StartupTrace.Write($"User data migration: {error.Message}");
        }
        return Field(ReadObject(Path.Combine(DirectoryPath, "settings.json")), "version", 0d) == 3.1 ? [] : legacySources;
    }

    // 将旧用户配置搬到固定目录，并在确认目标可读取后清理旧文件。
    internal static void Migrate(string target, IEnumerable<string> sources, string engineDirectory, string preferences)
    {
        var roots = sources.Append(engineDirectory).Select(Path.GetFullPath).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        var settingsPath = Path.Combine(target, "settings.json");
        if (File.Exists(settingsPath)) return;

        var (source, settings) = roots.Select(root => (root, data: ReadSettings(Path.Combine(root, "settings.json"))))
            .FirstOrDefault(item => item.data is not null);
        var historicFiles = settings is null && new[] { "styles", "legacy-themes" }.Any(name =>
            Directory.Exists(Path.Combine(target, name)) && Directory.EnumerateFiles(Path.Combine(target, name), "*", SearchOption.AllDirectories)
                .Any(path => Path.GetExtension(path).ToLowerInvariant() is ".yaml" or ".yml"));
        settings ??= new JsonObject { ["version"] = historicFiles ? 3d : 3.1d, ["values"] = new JsonObject() };
        var preserveLegacy = source is not null && settings["version"]?.GetValue<double>() != 3.1;
        var oldPreferences = Path.Combine(preferences, "settings.json");
        var preferencesData = ReadSettings(oldPreferences);
        if (preferencesData?["values"]?["language"] is { } language) settings["values"]!["language"] = language.DeepClone();

        Directory.CreateDirectory(target);
        var migrated = new List<string>();
        void Copy(string from, string to, bool removeSource)
        {
            if (!File.Exists(from) || Path.GetFullPath(from).Equals(Path.GetFullPath(to), StringComparison.OrdinalIgnoreCase)) return;
            try
            {
                if (File.Exists(to))
                {
                    if (!File.ReadAllBytes(from).AsSpan().SequenceEqual(File.ReadAllBytes(to))) return;
                }
                else
                {
                    var directory = Path.GetDirectoryName(to)!;
                    Directory.CreateDirectory(directory);
                    var temporary = Path.Combine(directory, Path.GetRandomFileName());
                    try
                    {
                        using (var input = File.OpenRead(from))
                        using (var output = File.Open(temporary, FileMode.CreateNew)) input.CopyTo(output);
                        File.Move(temporary, to);
                    }
                    finally { File.Delete(temporary); }
                }
                if (removeSource) migrated.Add(from);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException)
            {
                StartupTrace.Write($"{from}: {error.Message}");
            }
        }

        var configurationRoot = source ?? roots.FirstOrDefault();
        if (configurationRoot is not null)
            foreach (var name in new[] { "views.json", "poi_mapping.override.yaml", "poi_mapping.yaml" })
                Copy(Path.Combine(configurationRoot, name), Path.Combine(target, name), name != "views.json" || !preserveLegacy);
        foreach (var root in roots)
            foreach (var name in new[] { "custom_fonts", "user_icon_libraries" })
            {
                var from = Path.Combine(root, name);
                if (!Directory.Exists(from)) continue;
                var options = new EnumerationOptions { RecurseSubdirectories = true, IgnoreInaccessible = true };
                foreach (var path in Directory.EnumerateFiles(from, "*", options))
                    Copy(path, Path.Combine(target, name, Path.GetRelativePath(from, path)), true);
            }

        var staged = Path.Combine(target, Path.GetRandomFileName());
        try
        {
            File.WriteAllText(staged, settings.ToJsonString());
            File.Move(staged, settingsPath);
        }
        finally { File.Delete(staged); }
        if (source is not null && !preserveLegacy) migrated.Add(Path.Combine(source, "settings.json"));
        if (preferencesData is not null) migrated.Add(oldPreferences);
        foreach (var path in migrated)
        {
            try { File.Delete(path); }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { StartupTrace.Write($"{path}: {error.Message}"); }
        }
    }

    private static JsonObject? ReadSettings(string path)
    {
        var settings = ReadObject(path);
        if (settings.ValueKind != JsonValueKind.Object || !settings.TryGetProperty("values", out var values) ||
            values.ValueKind != JsonValueKind.Object) return null;
        if (settings.TryGetProperty("version", out var version) && version.ValueKind != JsonValueKind.Null &&
            (version.ValueKind != JsonValueKind.Number || !version.TryGetDouble(out var number) ||
             number is not (1 or 2 or 3 or 3.1))) return null;
        return JsonObject.Create(settings);
    }
}
