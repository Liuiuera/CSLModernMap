// 承载嵌入式 RMP 编辑器视图。
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.System;
using Microsoft.Web.WebView2.Core;
using Windows.Storage.Pickers;

namespace CSLModernMapWinUI;

internal sealed class RmpEditorView : Grid, IDisposable
{
    private const string Host = "csl-rmp.local";
    private readonly Window _owner;
    private readonly FrameworkElement _root;
    private readonly WebView2 _web = new();
    private readonly TextBlock _status = new() { TextWrapping = TextWrapping.Wrap };
    private readonly Grid _placementPanel = new() { Visibility = Visibility.Collapsed, ColumnSpacing = 12 };
    private readonly TextBlock _placementText = new() { TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center };
    private JsonElement _generatedMetadata;
    private readonly Dictionary<string, TaskCompletionSource<JsonElement>> _pending = new();
    private JsonElement? _metadata;
    private string _path = "", _projectId = "";
    private long _revision;
    private bool _dirty, _ready, _busy, _closed, _suspended;
    private Task? _initialization;
    public bool IsReady => _ready;
    public bool HasProject => _projectId.Length != 0;
    public bool HasWebFocus => _web.FocusState != FocusState.Unfocused;
    public bool IsBusy => _busy || _suspended;
    public JsonElement State { get; private set; }
    public JsonElement Tools { get; private set; }
    public event Action? StateChanged;
    public event Action? ProjectLoaded;
    public Func<string, Task>? CommandRequested { get; set; }
    public void Suspend(bool value) { _suspended = value; UpdateHostState(); }
    private readonly TaskCompletionSource _loaded = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private static string T(string key) => UiLanguage.Text("RmpEditor_" + key);

    public RmpEditorView(Window owner, FrameworkElement root)
    {
        _owner = owner; _root = root;
        RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        _placementPanel.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        _placementPanel.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        _placementPanel.Children.Add(_placementText);
        var cancel = new Button { Content = T("CancelPlacement") };
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(cancel, "rmp-cancel-placement");
        cancel.Click += async (_, _) => await CancelPlacementAsync();
        Grid.SetColumn(cancel, 1); _placementPanel.Children.Add(cancel);
        var footer = new StackPanel { Spacing = 4 };
        footer.Children.Add(_placementPanel); footer.Children.Add(_status);
        Grid.SetRow(footer, 1); Children.Add(_web); Children.Add(footer);
        _root.AddHandler(UIElement.KeyDownEvent, new KeyEventHandler(PlacementKeyDown), true);
    }

    private async void PlacementKeyDown(object sender, KeyRoutedEventArgs args)
    {
        if (args.Key != VirtualKey.Escape || _placementPanel.Visibility != Visibility.Visible || IsBusy || HasWebFocus) return;
        args.Handled = true;
        await CancelPlacementAsync();
    }
    private async Task CancelPlacementAsync()
    {
        try { await ExecuteAsync("cancelInsert"); }
        catch (Exception error) { _status.Text = error.Message; }
    }
    private void UpdatePlacementHint()
    {
        string? key = null;
        if (State.TryGetProperty("insertion", out var insertion) && insertion.ValueKind == JsonValueKind.Object)
            key = insertion.GetProperty("kind").GetString() switch { "title" => "TitlePlacement", "legend" => "LegendPlacement", "facility" => "IconPlacement", _ => null };
        key ??= State.GetProperty("mode").GetString() switch
        { "misc-node-text" => "PlainTextHelp", "misc-node-virtual" => "HelperPointHelp", "misc-node-fill" => "FillAreaHelp", _ => null };
        _placementPanel.Visibility = key is null ? Visibility.Collapsed : Visibility.Visible;
        if (key is not null) _placementText.Text = T(key);
    }

    public void Dispose()
    {
        _root.RemoveHandler(UIElement.KeyDownEvent, new KeyEventHandler(PlacementKeyDown));
        _closed = true; _ready = false;
        _loaded.TrySetCanceled();
        foreach (var pending in _pending.Values) pending.TrySetCanceled();
        _pending.Clear(); _web.Close();
    }

    private void UpdateHostState()
    {
        StateChanged?.Invoke();
        if (_web.CoreWebView2 is not null && !_closed)
            _web.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(new
            {
                protocolVersion = 1, type = "hostState", projectId = _projectId, enabled = _ready && !IsBusy
            }));
    }
    private async Task GuardAsync(Func<Task> action)
    {
        if (_busy || _closed) return;
        _busy = true; UpdateHostState();
        try { await action(); }
        finally { _busy = false; if (!_closed) UpdateHostState(); }
    }
    private bool Trusted(string source) => Uri.TryCreate(source, UriKind.Absolute, out var uri)
        && uri.Scheme == "https" && uri.Host == Host;

    public Task InitializeAsync() => _initialization ??= InitializeCoreAsync();
    private async Task InitializeCoreAsync()
    {
        if (_web.CoreWebView2 is not null) return;
        var folder = Path.Combine(AppContext.BaseDirectory, "Assets", "Rmp");
        if (!File.Exists(Path.Combine(folder, "embed.html"))) throw new FileNotFoundException(T("MissingAssets"));
        await _web.EnsureCoreWebView2Async();
        var core = _web.CoreWebView2!;
        core.SetVirtualHostNameToFolderMapping(Host, folder, CoreWebView2HostResourceAccessKind.DenyCors);
        core.Settings.AreDefaultContextMenusEnabled = false;
        core.Settings.IsStatusBarEnabled = false;
        core.NewWindowRequested += (_, args) => args.Handled = true;
        core.DownloadStarting += (_, args) => args.Cancel = true;
        core.NavigationStarting += (_, args) => { if (!Trusted(args.Uri)) args.Cancel = true; };
        core.PermissionRequested += (_, args) => args.State =
            args.PermissionKind == CoreWebView2PermissionKind.ClipboardRead && args.IsUserInitiated
                ? CoreWebView2PermissionState.Allow : CoreWebView2PermissionState.Deny;
        core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
        core.WebResourceRequested += (_, args) =>
        {
            if (Trusted(args.Request.Uri) || args.Request.Uri.StartsWith("data:", StringComparison.Ordinal)
                || args.Request.Uri.StartsWith("blob:https://" + Host + "/", StringComparison.Ordinal)) return;
            args.Response = core.Environment.CreateWebResourceResponse(null, 403, "Offline editor", "Content-Type: text/plain");
            _status.Text = T("BlockedResource");
        };
        core.WebMessageReceived += Receive;
        core.NavigationCompleted += (_, args) =>
        {
            if (!args.IsSuccess) _loaded.TrySetException(new IOException(T("LoadFailed") + ": " + args.WebErrorStatus));
        };
        core.ProcessFailed += (_, _) =>
        {
            _ready = false; UpdateHostState(); _status.Text = T("ProcessFailed");
            foreach (var pending in _pending.Values) pending.TrySetException(new IOException(T("ProcessFailed")));
            _loaded.TrySetException(new IOException(T("ProcessFailed")));
        };
        var language = UiLanguage.Language switch { "zh-CN" => "zh-Hans", "zh-TW" => "zh-Hant", var value => value.Split('-')[0] };
        _web.Source = new Uri($"https://{Host}/embed.html?language={Uri.EscapeDataString(language)}");
        await _loaded.Task.WaitAsync(TimeSpan.FromSeconds(60));
        var catalog = await RequestAsync("getTools");
        Tools = catalog.GetProperty("tools").Clone();
        StateChanged?.Invoke();
    }

    public async Task<bool> ApplyDraftAsync(JsonElement draft, bool restoreDefault)
    {
        var applied = false;
        await GuardAsync(async () =>
        {
            await InitializeAsync();
            var generatedMetadata = draft.GetProperty("metadata").Clone();
            var defaultPath = draft.GetProperty("defaultPath").GetString()!;
            var project = draft.GetProperty("project").Clone();
            JsonElement? metadata = generatedMetadata;
            var path = "";
            var fit = true;
            if (restoreDefault && File.Exists(defaultPath))
            {
                var saved = await ReadProjectAsync(defaultPath);
                var provenance = await ReadMetadataAsync(defaultPath, saved);
                if (provenance is { } matched && Matches(matched, generatedMetadata))
                { project = saved; metadata = matched; path = defaultPath; fit = false; }
                else
                {
                    var dialog = new ContentDialog { XamlRoot = _root.XamlRoot, Title = T("Detached"),
                        Content = T("MismatchPrompt"), PrimaryButtonText = T("OpenExisting"),
                        SecondaryButtonText = T("NewDraft"), CloseButtonText = T("Cancel") };
                    var result = await dialog.ShowAsync();
                    if (result == ContentDialogResult.None) return;
                    if (result == ContentDialogResult.Primary)
                    { project = saved; metadata = provenance; path = defaultPath; fit = false; }
                }
            }
            await LoadAsync(project, path, fit);
            _generatedMetadata = generatedMetadata; _metadata = metadata;
            _dirty = path.Length == 0; ShowPath(); applied = true;
        });
        return applied;
    }

    private void Receive(CoreWebView2 sender, CoreWebView2WebMessageReceivedEventArgs args)
    {
        if (_closed || !Trusted(args.Source)) return;
        try
        {
            using var document = JsonDocument.Parse(args.WebMessageAsJson);
            var data = document.RootElement;
            if (!data.TryGetProperty("channel", out var channel) || channel.GetString() != "rmp") return;
            if (!data.TryGetProperty("protocolVersion", out var version) || version.GetInt32() != 1) return;
            var type = data.GetProperty("type").GetString();
            if (type == "ready") { _loaded.TrySetResult(); return; }
            if (data.GetProperty("projectId").GetString() != _projectId) return;
            if (type == "changed")
            {
                _revision = data.GetProperty("revision").GetInt64(); _dirty = true; ShowPath(); return;
            }
            if (type == "editorState")
            {
                State = data.GetProperty("state").Clone(); UpdatePlacementHint(); StateChanged?.Invoke();
            }
            if (type == "commandRequested")
            {
                if (_ready && !IsBusy && CommandRequested is { } requested &&
                    data.TryGetProperty("command", out var command) && command.ValueKind == JsonValueKind.String)
                    _ = requested(command.GetString()!);
                return;
            }
            if (data.TryGetProperty("requestId", out var rawId) && rawId.ValueKind == JsonValueKind.String
                && _pending.TryGetValue(rawId.GetString()!, out var pending))
            {
                if (type == "error") pending.TrySetException(new InvalidDataException(data.GetProperty("message").GetString()));
                else pending.TrySetResult(data.Clone());
            }
            else if (type == "error") _status.Text = data.GetProperty("message").GetString();
        }
        catch (JsonException error) { _status.Text = error.Message; }
    }

    private async Task<JsonElement> RequestAsync(string type, object? payload = null)
    {
        if (_closed) throw new OperationCanceledException();
        var id = Guid.NewGuid().ToString("N");
        var completion = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending.Add(id, completion);
        try
        {
            var message = JsonSerializer.SerializeToNode(payload ?? new { })!.AsObject();
            message["protocolVersion"] = 1;
            message["type"] = type; message["requestId"] = id; message["projectId"] = _projectId;
            _web.CoreWebView2.PostWebMessageAsJson(message.ToJsonString());
            return await completion.Task.WaitAsync(TimeSpan.FromSeconds(120));
        }
        finally { _pending.Remove(id); }
    }
    private async Task LoadAsync(JsonElement project, string path, bool fit = false)
    {
        var previous = _projectId;
        _projectId = Guid.NewGuid().ToString("N");
        try { await RequestAsync("loadProject", new { project, fit }); }
        catch { _projectId = previous; throw; }
        _path = path; _revision = 0; _dirty = false; _ready = true; ShowPath();
        ProjectLoaded?.Invoke();
    }
    private void ShowPath()
    {
        StateChanged?.Invoke();
        _status.Text = (string.IsNullOrEmpty(_path) ? T("Unsaved") : _path) + (_dirty ? " *" : "");
    }
    private static bool Matches(JsonElement a, JsonElement b) =>
        new[] { "sourceFingerprint", "settingsFingerprint", "layoutFingerprint", "generatorVersion", "rmpCommit" }
        .All(key => a.TryGetProperty(key, out var left) && b.TryGetProperty(key, out var right)
            && left.ValueKind == JsonValueKind.String && right.ValueKind == JsonValueKind.String
            && left.GetString() == right.GetString());
    private static async Task<JsonElement> ReadProjectAsync(string path)
    {
        using var document = JsonDocument.Parse(await File.ReadAllTextAsync(path, Encoding.UTF8));
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("version", out var version)
            || !version.TryGetInt32(out var value) || value < 1 || value > 77 || !root.TryGetProperty("graph", out _))
            throw new InvalidDataException(T("InvalidProject"));
        return root.Clone();
    }
    private static async Task<JsonElement?> ReadMetadataAsync(string path, JsonElement project)
    {
        var metadataPath = path + ".cmm-meta.json";
        if (!File.Exists(metadataPath)) return null;
        try
        {
            using var document = JsonDocument.Parse(await File.ReadAllTextAsync(metadataPath, Encoding.UTF8));
            var root = document.RootElement;
            return root.ValueKind == JsonValueKind.Object && root.TryGetProperty("projectHash", out var hash)
                && hash.ValueKind == JsonValueKind.String && hash.GetString() == RmpProjectStore.Hash(project) ? root.Clone() : null;
        }
        catch (JsonException error)
        {
            StartupTrace.Write($"RMP metadata parse failed: {metadataPath}: {error.Message}");
            return null;
        }
    }
    private static async Task WriteAtomicAsync(string path, byte[] data)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try { await File.WriteAllBytesAsync(temporary, data); File.Move(temporary, path, true); }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
    private void InitializePicker(object picker) => WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(_owner));
    private string ProjectCityName()
    {
        var projectName = Path.GetFileName(_path);
        return _generatedMetadata.ValueKind == JsonValueKind.Object
            ? _generatedMetadata.GetProperty("cityName").GetString()!
            : projectName.EndsWith(".rmp.json", StringComparison.OrdinalIgnoreCase)
                ? projectName[..^9] : Path.GetFileNameWithoutExtension(projectName);
    }
    private async Task<string?> PickSaveAsync(string extension)
    {
        var name = ExportWindow.SuggestedFileNameFor(ProjectCityName(), "metro",
            extension == ".rmp.json" ? "RMP" : extension[1..].ToUpperInvariant(), DateTime.Now);
        name = name.EndsWith(".rmp.json", StringComparison.OrdinalIgnoreCase)
            ? name[..^9] : Path.GetFileNameWithoutExtension(name);
        var picker = new FileSavePicker { SuggestedFileName = name };
        picker.FileTypeChoices.Add(extension == ".rmp.json" ? "Rail Map Painter" : extension.ToUpperInvariant(), new List<string> { extension });
        InitializePicker(picker); return (await picker.PickSaveFileAsync())?.Path;
    }
    private async Task<bool> SaveAsync(bool saveAs)
    {
        if (!_ready) throw new IOException(T("ProcessFailed"));
        var path = saveAs || string.IsNullOrEmpty(_path) ? await PickSaveAsync(".rmp.json") : _path;
        if (path is null) return false;
        var response = await RequestAsync("saveProject");
        var project = response.GetProperty("project");
        var revision = response.GetProperty("revision").GetInt64();
        await RmpProjectStore.SaveAsync(path, project, _metadata, revision);
        _path = path; _dirty = _revision != revision; ShowPath(); return !_dirty;
    }
    public async Task<bool> ConfirmUnsavedAsync()
    {
        if (IsBusy) return false;
        var confirmed = false;
        await GuardAsync(async () => { confirmed = await ConfirmReplaceAsync(); });
        return confirmed;
    }
    public async Task<bool> ConfirmRegenerationAsync()
    {
        if (IsBusy) return false;
        var confirmed = false;
        await GuardAsync(async () => { confirmed = await ConfirmReplaceAsync(regenerating: true); });
        return confirmed;
    }
    private async Task<bool> ConfirmReplaceAsync(bool regenerating = false)
    {
        if (!_dirty && !regenerating) return true;
        var dialog = new ContentDialog { XamlRoot = _root.XamlRoot,
            Title = T(regenerating ? "Apply" : "Unsaved"), Content = T(regenerating ? "RegeneratePrompt" : "UnsavedPrompt"),
            PrimaryButtonText = regenerating ? (_dirty ? T("SaveAndApply") : T("Apply")) : T("Save"),
            SecondaryButtonText = regenerating ? (_dirty ? T("DiscardAndApply") : "") : T("Discard"), CloseButtonText = T("Cancel") };
        var result = await dialog.ShowAsync();
        return result == ContentDialogResult.Secondary || result == ContentDialogResult.Primary && (!_dirty || await SaveAsync(false));
    }
    private async Task OpenAsync()
    {
        var picker = new FileOpenPicker(); picker.FileTypeFilter.Add(".json"); InitializePicker(picker);
        var file = await picker.PickSingleFileAsync(); if (file is null) return;
        var project = await ReadProjectAsync(file.Path);
        var metadata = await ReadMetadataAsync(file.Path, project);
        await InitializeAsync();
        if (!await ConfirmReplaceAsync()) return;
        await LoadAsync(project, file.Path); _metadata = metadata;
        if (metadata is null || _generatedMetadata.ValueKind != JsonValueKind.Object || !Matches(metadata.Value, _generatedMetadata)) _status.Text = T("Detached");
    }
    public async Task ExecuteAsync(string command, object? payload = null)
    {
        if (IsBusy || !_ready && command != "open") return;
        await GuardAsync(async () =>
        {
            switch (command)
            {
                case "save": await SaveAsync(false); break;
                case "saveAs": await SaveAsync(true); break;
                case "open": await OpenAsync(); break;
                case "beginInsert":
                    var insertion = JsonSerializer.SerializeToNode(payload)!.AsObject();
                    if (insertion["insertion"]!["kind"]!.GetValue<string>() == "title")
                        insertion["insertion"]!["title"] = T("DefaultTitle").Replace("{cityName}", ProjectCityName());
                    await RequestAsync(command, insertion); break;
                case "fit": case "undo": case "redo": case "setMode": case "setViewOptions": case "cancelInsert":
                    await RequestAsync(command, payload); break;
                default: throw new ArgumentException("Unknown RMP command: " + command);
            }
        });
        if (command is "beginInsert" or "setMode") _web.Focus(FocusState.Programmatic);
    }
    public Task ExportFileAsync(string path, string format, RmpExportOptions options) => GuardAsync(async () =>
    {
        var response = await RequestAsync("export", new { format, scale = options.Scale,
            transparent = options.Transparent, svgVersion = options.SvgVersion });
        var data = response.GetProperty("data").GetString()!;
        await WriteAtomicAsync(path, format == "svg" ? Encoding.UTF8.GetBytes(data) : Convert.FromBase64String(data));
        _status.Text = T("Exported") + " " + path;
    });
}
