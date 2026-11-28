// Read only the CMM header needed by the empty view.
#nullable enable

using System;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text.Json;
using CSLModernMap;

namespace CSLModernMapWinUI;

internal sealed record RecentCmmExport(FileInfo MapFile, string Version)
{
    internal static RecentCmmExport? FindLatest(string registry, Action<string> log)
    {
        var file = GameExportDirectories
            .EnumerateRegisteredMaps(registry, log)
            .OrderByDescending(file => file.LastWriteTimeUtc)
            .ThenBy(file => file.FullName, StringComparer.OrdinalIgnoreCase).FirstOrDefault();
        if (file is null) return null;
        try { return new RecentCmmExport(file, ReadVersion(file.FullName)); }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or InvalidDataException or JsonException)
        {
            log($"Cannot read CMM header: {file.FullName}: {error.Message}");
            return new RecentCmmExport(file, "");
        }
    }

    internal static string ReadVersion(string path)
    {
        using var source = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read | FileShare.Delete);
        Span<byte> signature = stackalloc byte[2];
        source.ReadExactly(signature);
        source.Position = 0;

        if (signature.SequenceEqual("PK"u8))
        {
            using var archive = new ZipArchive(source, ZipArchiveMode.Read);
            using var city = (archive.GetEntry("city.json") ?? throw new InvalidDataException("Missing city.json.")).Open();
            var version = ReadHeader(city);
            if (version != "CMM 1.1") throw new InvalidDataException("CMM ZIP requires schema_version cmm1.1.");
            return version;
        }
        if (signature[0] == 0x1f && signature[1] == 0x8b)
        {
            using var city = new GZipStream(source, CompressionMode.Decompress);
            return ReadHeader(city);
        }
        return ReadHeader(source);
    }

    private static string ReadHeader(Stream city)
    {
        // The game exporters write the version and metadata before geometry and terrain.
        Span<byte> prefix = stackalloc byte[16 * 1024];
        int length = city.ReadAtLeast(prefix, prefix.Length, false);
        var json = prefix[..length];
        if (json.StartsWith("\uFEFF"u8)) json = json[3..];
        var reader = new Utf8JsonReader(json, isFinalBlock: length < prefix.Length, state: default);
        while (reader.Read())
        {
            if (reader.TokenType == JsonTokenType.PropertyName && reader.CurrentDepth == 1 &&
                reader.ValueTextEquals("schema_version"u8) && reader.Read() && reader.TokenType == JsonTokenType.String)
                return reader.GetString() switch { "cmm-v1" => "CMM v1", "cmm1.1" => "CMM 1.1", _ => "" };
        }
        throw new InvalidDataException("Missing CMM version.");
    }
}
