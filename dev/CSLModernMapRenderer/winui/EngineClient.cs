// 管理 WinUI 与渲染引擎进程的通信。
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace CSLModernMapWinUI;

internal sealed class EngineClient : IAsyncDisposable
{
    private readonly ConcurrentDictionary<long, TaskCompletionSource<JsonElement>> _pending = new();
    private readonly SemaphoreSlim _write = new(1, 1);
    private Process? _process;
    private long _serial;
    private volatile bool _closing;
    private readonly CancellationTokenSource _shutdown = new();
    private readonly object _closeLock = new();
    private Task? _closeTask;
    private Task _stdout = Task.CompletedTask, _stderr = Task.CompletedTask;


    public event Action<string, JsonElement>? EventReceived;
    public event Action<string>? Faulted;

    public async Task StartAsync(string[] startupArguments)
    {
        var (executable, arguments, directory) = FindEngine();
        await Task.Run(() => UserData.Prepare(startupArguments, directory), _shutdown.Token);
        await StartAsync(executable, arguments, directory);
    }

    // 显式启动进程也便于无界面的生命周期回归检查。
    internal Task StartAsync(string executable, string arguments, string directory)
    {
        var start = new ProcessStartInfo(executable, arguments)
        {
            WorkingDirectory = directory,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        start.Environment["PYTHONIOENCODING"] = "utf-8";
        start.Environment["CSLMM_USER_DATA_DIR"] = UserData.DirectoryPath;
        lock (_closeLock)
        {
            _shutdown.Token.ThrowIfCancellationRequested();
            if (_process is not null) throw new InvalidOperationException(UiLanguage.Text("EngineClient_StartAsync_874DC8B84F"));
            _process = Process.Start(start) ?? throw new InvalidOperationException(UiLanguage.Text("EngineClient_StartAsync_641B4C254B"));
            _stdout = ReadLoopAsync();
            _stderr = ReadErrorsAsync(_process.StandardError);
        }
        return Task.CompletedTask;
    }

    private async Task ReadErrorsAsync(StreamReader reader)
    {
        try
        {
            // 持续排空 stderr，避免子进程阻塞；请求错误由协议上报。
            while (await reader.ReadLineAsync().ConfigureAwait(false) is not null) { }
        }
        catch (IOException error) { if (!_closing) FailAll(error.Message); }
    }

    private static (string executable, string arguments, string directory) FindEngine()
    {
        var packaged = Path.Combine(AppContext.BaseDirectory, "engine", "CSLModernMapEngine.exe");
        if (File.Exists(packaged))
            return (packaged, "engine", Path.GetDirectoryName(packaged)!);
        var overridePath = Environment.GetEnvironmentVariable("CSLMM_ENGINE_EXE");
        if (!string.IsNullOrWhiteSpace(overridePath) && File.Exists(overridePath))
            return (overridePath, "engine", Path.GetDirectoryName(overridePath)!);
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            var main = Path.Combine(dir.FullName, "dev", "CSLModernMapRenderer", "main.py");
            if (File.Exists(main))
                return ("python", "main.py engine", Path.GetDirectoryName(main)!);
        }
        throw new FileNotFoundException(UiLanguage.Text("EngineClient_FindEngine_EED0A8F582"));
    }

    // 向引擎发送一个协议请求，并将响应或取消结果交还调用方。
    public async Task<JsonElement> RequestAsync(string method, object? parameters = null, CancellationToken cancellation = default)
    {
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellation, _shutdown.Token);
        var token = linked.Token;
        token.ThrowIfCancellationRequested();
        var process = _process;
        if (_closing || process is null)
            throw new InvalidOperationException(UiLanguage.Text("EngineClient_RequestAsync_1B3D323B4E"));
        var id = Interlocked.Increment(ref _serial);
        var source = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = source;
        try
        {
            var line = JsonSerializer.Serialize(new { id, method, @params = parameters ?? new { } });
            await WriteLineAsync(process, line, token).ConfigureAwait(false);
            return await source.Task.WaitAsync(TimeSpan.FromSeconds(120), token).ConfigureAwait(false);
        }
        finally { _pending.TryRemove(id, out _); }
    }

    private async Task WriteLineAsync(Process process, string line, CancellationToken token)
    {
        await _write.WaitAsync(token).ConfigureAwait(false);
        try
        {
            // CloseAsync 先取消请求再获取同一把锁；关闭取得锁后写入器不能再访问进程流。
            token.ThrowIfCancellationRequested();
            await process.StandardInput.WriteLineAsync(line.AsMemory(), token).ConfigureAwait(false);
            await process.StandardInput.FlushAsync(token).ConfigureAwait(false);
        }
        finally { _write.Release(); }
    }

    // 持续读取引擎协议帧，并按请求标识完成等待中的操作。
    private async Task ReadLoopAsync()
    {
        try
        {
            while (_process is not null && await _process.StandardOutput.ReadLineAsync().ConfigureAwait(false) is { } line)
            {
                using var document = JsonDocument.Parse(line);
                var root = document.RootElement;
                if (root.TryGetProperty("event", out var name))
                {
                    if (!_closing) EventReceived?.Invoke(name.GetString() ?? "", root.GetProperty("payload").Clone());
                }
                else if (root.TryGetProperty("id", out var rawId) && rawId.TryGetInt64(out var id) && _pending.TryGetValue(id, out var source))
                {
                    if (root.TryGetProperty("error", out var error))
                        source.TrySetException(new InvalidOperationException(error.GetProperty("message").GetString()));
                    else
                        source.TrySetResult(root.GetProperty("result").Clone());
                }
            }
            if (!_closing) FailAll(UiLanguage.Text("EngineClient_ReadLoopAsync_4A90E34339"));
        }
        catch (Exception error) { if (!_closing) FailAll(error.Message); }
    }

    private void FailAll(string message)
    {
        foreach (var (_, source) in _pending)
            source.TrySetException(new InvalidOperationException(message));
        Faulted?.Invoke(message);
    }

    public Task CloseAsync()
    {
        lock (_closeLock) return _closeTask ??= CloseCoreAsync();
    }

    public ValueTask DisposeAsync() => new(CloseAsync());

    private async Task CloseCoreAsync()
    {
        _closing = true;
        _shutdown.Cancel();
        var process = _process;
        if (process is null) return;
        try
        {
            using var grace = new CancellationTokenSource(TimeSpan.FromSeconds(3));
            if (!process.HasExited)
            {
                var line = JsonSerializer.Serialize(new { id = Interlocked.Increment(ref _serial), method = "close", @params = new { } });
                await WriteLineAsync(process, line, grace.Token).ConfigureAwait(false);
            }
            await process.WaitForExitAsync(grace.Token).ConfigureAwait(false);
        }
        catch (Exception error) when (error is OperationCanceledException or IOException or InvalidOperationException)
        {
            StartupTrace.Write("Engine graceful close: " + error.Message);
            try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
            catch (InvalidOperationException) { }
        }
        // 等待读取器及被中断的写入器结束后再释放流，不能阻塞界面调度线程或提前释放信号量。
        await process.WaitForExitAsync().ConfigureAwait(false);
        await Task.WhenAll(_stdout, _stderr).ConfigureAwait(false);
        await _write.WaitAsync().ConfigureAwait(false);
        try { _process = null; process.Dispose(); }
        finally { _write.Release(); }
    }
}
