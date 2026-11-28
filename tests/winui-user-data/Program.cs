using System.Text.Json.Nodes;
using CSLModernMapWinUI;

static void Check(bool ok, string failure)
{
    if (!ok) throw new InvalidOperationException(failure);
}

var root = Directory.CreateTempSubdirectory();
try
{
    var target = root.CreateSubdirectory("target").FullName;
    var source = root.CreateSubdirectory("legacy").FullName;
    var engine = root.CreateSubdirectory("engine").FullName;
    var prefs = root.CreateSubdirectory("preferences").FullName;
    foreach (var name in new[] { "preview-quality.json", "preview-ui.json", "preview-startup.json", "ribbon-layout.json" })
        File.WriteAllText(Path.Combine(target, name), "{");
    Check(UserData.ReadPreviewQuality(target) == new PreviewQualitySettings(), "broken quality");
    Check(UserData.ReadAutoPreview(target) && !UserData.ReadMapInteractionGuideSeen(target), "broken toggles");
    Check(UserData.ReadRibbonLayout(target) == new UserData.RibbonLayout("Classic", "AlwaysShow"), "broken ribbon");
    File.WriteAllText(Path.Combine(target, "preview-quality.json"), """{"pixelScale":"bad","textPercent":150,"lineDetail":"fine"}""");
    var quality = UserData.ReadPreviewQuality(target);
    Check(quality.PixelScale == 1 && quality.TextPercent == 150 && quality.LineDetail == "fine", "per-field fallback");
    UserData.WriteAutoPreview(target, false);
    Check(!UserData.ReadAutoPreview(target), "disabled preview preference");

    var builtin = Directory.CreateDirectory(Path.Combine(engine, "csmodernmap", "services", "poi"));
    File.WriteAllText(Path.Combine(builtin.FullName, "poi_mapping.yaml"), "bundled");
    File.WriteAllText(Path.Combine(source, "settings.json"), """{"version":3.1,"values":{"map":"city"}}""");
    File.WriteAllText(Path.Combine(prefs, "settings.json"), "{");
    File.WriteAllText(Path.Combine(source, "views.json"), "old views");
    File.WriteAllText(Path.Combine(target, "views.json"), "chosen views");
    var fonts = Directory.CreateDirectory(Path.Combine(source, "custom_fonts"));
    var font = Path.Combine(fonts.FullName, "sample.ttf");
    File.WriteAllText(font, "font");
    File.SetAttributes(font, FileAttributes.ReadOnly);
    UserData.Migrate(target, [source], engine, prefs);
    var stored = JsonNode.Parse(File.ReadAllText(Path.Combine(target, "settings.json")));
    Check(stored?["values"]?["map"]?.GetValue<string>() == "city", "publish despite optional conflicts");
    Check(File.ReadAllText(Path.Combine(prefs, "settings.json")) == "{", "broken settings source preserved");
    Check(File.ReadAllText(Path.Combine(source, "views.json")) == "old views", "conflicting source preserved");
    Check(File.ReadAllText(Path.Combine(target, "views.json")) == "chosen views", "conflicting target preserved");
    Check(!File.Exists(Path.Combine(target, "poi_mapping.yaml")), "do not copy packaged default");
    Check(File.ReadAllText(Path.Combine(target, "custom_fonts", "sample.ttf")) == "font", "user font migrated");

    Check(!File.GetAttributes(Path.Combine(target, "custom_fonts", "sample.ttf")).HasFlag(FileAttributes.ReadOnly), "copied content does not inherit packaged attributes");
    if (File.Exists(font)) File.SetAttributes(font, FileAttributes.Normal);

    File.WriteAllText(Path.Combine(target, "settings.json"), "{");
    UserData.Migrate(target, [source], engine, prefs);
    Check(File.ReadAllText(Path.Combine(target, "settings.json")) == "{", "invalid existing target left intact");
    Console.WriteLine("Windows startup preference and migration checks passed.");
}
finally { root.Delete(true); }
