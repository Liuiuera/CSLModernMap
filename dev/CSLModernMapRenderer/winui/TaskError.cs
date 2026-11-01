// 保存错误摘要及可复制诊断，不以异常名称推断修复建议。
using System.Reflection;
using System.Text.Json;

namespace CSLModernMapWinUI;

internal sealed class TaskError : InvalidOperationException
{
    internal string Details { get; }
    internal JsonElement? Payload { get; }

    internal TaskError(string message, string details, JsonElement? payload = null) : base(message)
    { Details = details; Payload = payload; }

    internal static TaskError FromPayload(JsonElement payload) => new(
        payload.TryGetProperty("message", out var message) && payload.TryGetProperty("category", out _)
            ? message.GetString() ?? UiLanguage.Text("Failure_Internal") : UiLanguage.Text("Failure_Internal"),
        string.Join("\n\n", payload.EnumerateObject().Select(field => $"{field.Name}: {field.Value}")), payload.Clone());

    internal static TaskError FromException(Exception error, string operation = "", string stage = "")
    {
        if (error is TaskError failure) return failure;
        var reason = error switch
        {
            UnauthorizedAccessException => "Failure_Permission",
            FileNotFoundException => "Failure_MissingFile",
            OutOfMemoryException => "Failure_Memory",
            _ => "Failure_Internal",
        };
        return new((string.IsNullOrWhiteSpace(operation) ? UiLanguage.Text("Failure_Operation") : operation) + "。" + UiLanguage.Text(reason),
            $"Version: {Assembly.GetExecutingAssembly().GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion}\nOperation: {operation}\nStage: {stage}\nException: {error.GetType().FullName}\n{error}");
    }

    internal static TaskError EngineStopped(string detail) => new(UiLanguage.Text("Failure_EngineStopped"),
        FromException(new IOException(detail), stage: "engineExit").Details);
}
