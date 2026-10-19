// 保存和读取嵌入式 RMP 工程数据。
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace CSLModernMapWinUI;

internal static class RmpProjectStore
{
    public static string Hash(JsonElement project) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(project.GetRawText())));

    // 原子保存 RMP 工程与来源信息，并保留上一组文件用于恢复。
    public static async Task SaveAsync(string path, JsonElement project, JsonElement? provenance, long revision)
    {
        path = Path.GetFullPath(path);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var metadataPath = path + ".cmm-meta.json";
        var suffix = "." + Guid.NewGuid().ToString("N") + ".tmp";
        var projectTemp = path + suffix;
        var metadataTemp = metadataPath + suffix;
        var metadata = provenance is { } known ? JsonNode.Parse(known.GetRawText())!.AsObject() : new JsonObject();
        metadata["projectHash"] = Hash(project);
        metadata["projectRevision"] = revision;
        var hadProject = File.Exists(path);
        var hadMetadata = File.Exists(metadataPath);
        var projectReplaced = false;
        try
        {
            await File.WriteAllTextAsync(projectTemp, project.GetRawText(), new UTF8Encoding(false));
            await File.WriteAllTextAsync(metadataTemp, metadata.ToJsonString(), new UTF8Encoding(false));
            // 成功保存后仍保留上一组文件供崩溃恢复，直到下次成功保存。
            if (hadProject) File.Copy(path, path + ".previous", true);
            if (hadMetadata) File.Copy(metadataPath, metadataPath + ".previous", true);
            File.Move(projectTemp, path, true);
            projectReplaced = true;
            File.Move(metadataTemp, metadataPath, true);
        }
        catch
        {
            if (projectReplaced)
            {
                if (hadProject) File.Copy(path + ".previous", path, true);
                else File.Delete(path);
                if (hadMetadata) File.Copy(metadataPath + ".previous", metadataPath, true);
            }
            throw;
        }
        finally
        {
            File.Delete(projectTemp);
            File.Delete(metadataTemp);
        }
    }
}
