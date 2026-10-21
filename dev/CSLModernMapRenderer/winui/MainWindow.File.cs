// 处理主窗口中的文件打开与保存流程。
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private readonly MenuFlyoutSubItem _fileRecentMenu = new() { Text = UiLanguage.Text("MainWindow_File__fileRecentMenu_B6D7CBC94D") };

    private MenuFlyoutItem? _workspaceOpenMenu, _workspaceSaveMenu, _workspaceSaveAsMenu, _openCityMenu;

    private MenuFlyout? _fileMenu;
    private MenuFlyoutItem? _workspaceExportMenu;

    private void BuildFileMenu()
    {
        Ribbon.IsApplicationButtonVisible = true;
        Ribbon.ApplicationButtonContent = new TextBlock { Text = UiLanguage.Text("MainWindow_File_BuildFileMenu_39932F24FE") };
        var file = _fileMenu = new MenuFlyout();
        // 应用按钮承载文件菜单：点击时展开/收起，菜单内容与命令注册保持原样。
        Ribbon.ApplicationButtonClick += (_, args) =>
        {
            args.Handled = true;
            if (file.IsOpen) file.Hide();
            else if (Ribbon.ApplicationButton is { } button) file.ShowAt(button);
        };
        MenuFlyoutItem Action(string title, string commandId, Symbol? icon = null, string? shortcut = null)
        {
            var item = new MenuFlyoutItem { Text = title };
            if (icon is { } symbol) item.Icon = new SymbolIcon(symbol);
            if (shortcut is not null) item.KeyboardAcceleratorTextOverride = shortcut;
            item.Click += async (_, _) => await ExecuteCommandAsync(commandId);
            if (_commands.TryGetValue(commandId, out var command)) command.Controls.Add(item);
            file.Items.Add(item);
            return item;
        }
        _workspaceOpenMenu = Action(UiLanguage.Text("MainWindow_File_BuildFileMenu_CF2A3F856E"), "file.open", Symbol.OpenFile, "Ctrl+O");
        _openCityMenu = Action(UiLanguage.Text("MainWindow_File_BuildFileMenu_CF2A3F856E"), "file.open_city", Symbol.OpenFile);
        file.Items.Add(_fileRecentMenu);
        file.Items.Add(new MenuFlyoutSeparator());
        _workspaceSaveMenu = Action(UiLanguage.Text("MainWindow_File_BuildFileMenu_D5962F4266"), "map.save_png", Symbol.Save, "Ctrl+S");
        _workspaceSaveAsMenu = Action(RmpText("SaveAs"), "workspace.saveAs", shortcut: "Ctrl+Shift+S");
        _workspaceExportMenu = Action(RmpText("Export"), "workspace.export", shortcut: "Ctrl+Shift+E");
        file.Items.Add(new MenuFlyoutSeparator());
        Action(UiLanguage.Text("MainWindow_File_BuildFileMenu_02E61DEAD2"), "app.language");
        var appearance = new MenuFlyoutSubItem { Text = UiLanguage.Text("MainWindow_File_BuildFileMenu_86A63F23A0") };
        foreach (var (label, theme) in new[] { (UiLanguage.Text("MainWindow_File_BuildFileMenu_5B50D7C4B5"), ElementTheme.Default), (UiLanguage.Text("MainWindow_File_BuildFileMenu_AA0819DFC4"), ElementTheme.Light), (UiLanguage.Text("MainWindow_File_BuildFileMenu_A6B75D0680"), ElementTheme.Dark) })
        {
            var item = new MenuFlyoutItem { Text = label };
            item.Click += (_, _) =>
            {
                file.Hide();
                Root.DispatcherQueue.TryEnqueue(() => Root.RequestedTheme = theme);
            };
            appearance.Items.Add(item);
        }
        file.Items.Add(appearance);
        var configuration = new MenuFlyoutItem
        {
            Text = UiLanguage.Text("MainWindow_File_OpenConfigurationFolder"),
            Icon = new SymbolIcon(Symbol.Folder),
        };
        configuration.Click += async (_, _) => await RunAsync(async () =>
        {
            Directory.CreateDirectory(UserData.DirectoryPath);
            await Windows.System.Launcher.LaunchFolderPathAsync(UserData.DirectoryPath);
        });
        file.Items.Add(configuration);
        file.Items.Add(new MenuFlyoutSeparator());
        Action(UiLanguage.Text("MainWindow_File_BuildFileMenu_52D25A9E30"), "app.about", Symbol.Help);
        Action(UiLanguage.Text("MainWindow_File_BuildFileMenu_498E1D59B4"), "app.quit", Symbol.Cancel, "Ctrl+Q");
        RefreshWorkspaceFileMenu();
    }

    private void RefreshWorkspaceFileMenu()
    {
        if (_workspaceExportMenu is not null) _workspaceExportMenu.Text = RmpText("Export") + (_context == "metro" ? "" : "…");
        if (_workspaceSaveAsMenu is not null) _workspaceSaveAsMenu.Visibility = _context == "metro" ? Visibility.Visible : Visibility.Collapsed;
        if (_openCityMenu is not null) _openCityMenu.Visibility = _context == "metro" ? Visibility.Visible : Visibility.Collapsed;
        if (_workspaceOpenMenu is not null) _workspaceOpenMenu.Text = _context == "metro" ? RmpText("Open") : UiLanguage.Text("MainWindow_File_BuildFileMenu_CF2A3F856E");
        if (_workspaceSaveMenu is not null) _workspaceSaveMenu.Text = _context == "metro" ? RmpText("Save") : UiLanguage.Text("MainWindow_File_BuildFileMenu_D5962F4266");
    }

    private void RefreshFileRecent()
    {
        _fileRecentMenu.Items.Clear();
        if (_snapshot.ValueKind != System.Text.Json.JsonValueKind.Object) return;
        var busy = _snapshot.GetProperty("busy").GetBoolean();
        foreach (var item in _snapshot.GetProperty("recentFiles").EnumerateArray())
        {
            var path = item.GetString();
            if (string.IsNullOrWhiteSpace(path)) continue;
            var exists = File.Exists(path);
            var command = new MenuFlyoutItem { Text = Path.GetFileName(path), IsEnabled = exists && !busy };
            ToolTipService.SetToolTip(command, exists ? path : UiLanguage.Text("MainWindow_File_RefreshFileRecent_95D7E39D01") + path);
            command.Click += async (_, _) => await RunAsync(() => OpenPathAsync(path));
            _fileRecentMenu.Items.Add(command);
        }
        if (_fileRecentMenu.Items.Count == 0)
            _fileRecentMenu.Items.Add(new MenuFlyoutItem { Text = UiLanguage.Text("MainWindow_File_RefreshFileRecent_C3FE933AA4"), IsEnabled = false });
    }
}
