using System;
using System.Globalization;
using System.IO;

namespace CSLModernMap.Systems
{
    /// <summary>管理导出文件路径</summary>
    internal static class ExportPaths
    {
        private const string FileSuffix = ".cmm";

        internal static string DefaultDirectory
        {
            get
            {
                var documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments,
                    Environment.SpecialFolderOption.DoNotVerify);
                return string.IsNullOrEmpty(documents) ? string.Empty : Path.Combine(documents, "CSLModernMap", "CS2");
            }
        }

        internal static string ExportDirectory => Mod.Settings.ExportDirectory;

        internal static string AbsoluteDirectory(string directory)
        {
            var root = Path.GetPathRoot(directory);
            if (string.IsNullOrEmpty(root) || (Path.DirectorySeparatorChar == '\\'
                && (root.Length == 1 || root.EndsWith(":", StringComparison.Ordinal))))
                throw new ArgumentException("Expected an absolute export directory: " + directory);
            return Path.GetFullPath(directory);
        }

        internal static void RegisterExportDirectory()
        {
            try
            {
                GameExportDirectories.Register(GameExportDirectories.RegistryDirectory, ExportGame.CS2, AbsoluteDirectory(ExportDirectory));
            }
            catch (Exception error) when (error is IOException || error is UnauthorizedAccessException ||
                error is ArgumentException || error is NotSupportedException)
            {
                Mod.log.Warn("[export] Could not register the export directory: " + error.Message);
            }
        }

        internal static string EnsureExportDirectory()
        {
            return Directory.CreateDirectory(AbsoluteDirectory(ExportDirectory)).FullName;
        }

        internal static string BuildFileName(string cityName, DateTime localTime)
        {
            var chars = (cityName ?? "").Trim().ToCharArray();
            var invalid = Path.GetInvalidFileNameChars();
            for (var i = 0; i < chars.Length; i++)
            {
                if (char.IsControl(chars[i]) || Array.IndexOf(invalid, chars[i]) >= 0
                    || "<>:\"/\\|?*".IndexOf(chars[i]) >= 0)
                {
                    chars[i] = '_';
                }
            }

            var safeName = new string(chars).Trim().TrimEnd('.');
            if (string.IsNullOrWhiteSpace(safeName))
            {
                safeName = "City";
            }

            return safeName + "-"
                + localTime.ToString("yyyyMMdd-HHmmss-fff", CultureInfo.InvariantCulture)
                + FileSuffix;
        }
        internal static ExportFileInfo? FindNewestExport()
        {
            try
            {
                var directory = new DirectoryInfo(AbsoluteDirectory(ExportDirectory));
                if (!directory.Exists)
                {
                    return null;
                }

                FileInfo newest = null;
                foreach (var file in GameExportDirectories.EnumerateMaps(directory.FullName))
                {
                    if (newest == null || file.LastWriteTimeUtc > newest.LastWriteTimeUtc)
                    {
                        newest = file;
                    }
                }

                if (newest == null)
                {
                    return null;
                }

                return new ExportFileInfo(newest.Name, newest.LastWriteTime);
            }
            catch (Exception e)
            {
                Mod.log.Warn("[export] 无法读取导出目录: " + e.Message);
                return null;
            }
        }

        internal readonly struct ExportFileInfo
        {
            internal ExportFileInfo(string fileName, DateTime localTime)
            {
                FileName = fileName;
                LocalTime = localTime;
            }

            internal string FileName { get; }

            internal DateTime LocalTime { get; }
        }
    }
}
