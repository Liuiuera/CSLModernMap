using CSLModernMap.Systems;

namespace CSLModernMap;

internal static class Program
{
    private static void Main()
    {
        var documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments,
            Environment.SpecialFolderOption.DoNotVerify);
        var expected = string.IsNullOrEmpty(documents) ? string.Empty : Path.Combine(documents, "CSLModernMap", "CS2");
        Check(ExportPaths.DefaultDirectory == expected, "Unexpected default export folder");

        var root = Directory.CreateTempSubdirectory("cmm-export-folder-");
        try
        {
            var first = Path.Combine(root.FullName, "自选 地图");
            Mod.Settings.ExportDirectory = first;
            Check(ExportPaths.EnsureExportDirectory() == first, "Selected folder was not used directly");
            var previousMap = Path.Combine(first, "城市.cmm.gz");
            File.WriteAllText(previousMap, string.Empty);
            Check(ExportPaths.FindNewestExport()?.FileName == "城市.cmm.gz", "Previous map was not found");

            Mod.Settings.ExportDirectory = Path.Combine(root.FullName, "next");
            Check(ExportPaths.FindNewestExport() == null, "New folder still lists the previous map");
            File.WriteAllText(Path.Combine(ExportPaths.EnsureExportDirectory(), "next.cmm"), string.Empty);
            Check(ExportPaths.FindNewestExport()?.FileName == "next.cmm" && File.Exists(previousMap),
                "Switching folders did not preserve maps in both locations");

            Mod.Settings.ExportDirectory = previousMap;
            try
            {
                ExportPaths.EnsureExportDirectory();
                throw new InvalidOperationException("Accepted a file as the export folder");
            }
            catch (IOException) { }

            Mod.Settings.ExportDirectory = "relative";
            try
            {
                ExportPaths.EnsureExportDirectory();
                throw new InvalidOperationException("Accepted a relative export folder");
            }
            catch (ArgumentException) { }
        }
        finally { root.Delete(true); }
    }

    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}

internal static class Mod
{
    internal static class Settings
    {
        internal static string ExportDirectory { get; set; }
    }

    internal static readonly TestLog log = new();

    internal sealed class TestLog
    {
        internal void Warn(string message) => throw new InvalidOperationException(message);
    }
}
