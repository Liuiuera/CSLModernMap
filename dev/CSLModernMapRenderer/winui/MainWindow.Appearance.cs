// 处理主窗口的外观与主题设置。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Windowing;
using Windows.Graphics;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private JsonElement _appearancePalettes;
    private readonly List<(Control Button, IconElement Icon, string Domain, bool Large, Func<bool>? Selected)> _ribbonColors = new();

    private static string CommandDomain(string id) => id switch
    {
        "tools.clear_cache" => "DANGER",
        "map.save_png" or "map.render_selection" or "view.transit.export" or "view.metro.export" or "view.metro.export_rmg" => "OUTPUT",
        "map.copy_image" or "create.web_map" => "SHARE",
        "view.transit" or "view.metro" or "create.random_spot" or "create.interchange_collection" => "CREATE",
        _ when id.StartsWith("tools.") || id.StartsWith("style.") || id is "app.about" or "app.language" || id.EndsWith("settings") => "TOOL",
        _ => "PRIMARY",
    };

    private void RegisterRibbonColors(Control button, object? icon, string domain, bool large = false, Func<bool>? selected = null)
    {
        // Icon 允许多种类型，本工程的图标始终是 IconElement，域色与明暗处理依赖这一点。
        var element = (IconElement)icon!;
        _ribbonColors.Add((button, element, RibbonIcons.Domain(element, domain), large, selected));
        ApplyRibbonColors();
    }

    private void ConfigureAppearance(JsonElement metadata)
    {
        if (metadata.TryGetProperty("appearancePalettes", out var palettes)) _appearancePalettes = palettes.Clone();
        ApplyRibbonColors();
    }

    private void ApplyRibbonColors()
    {
        if (_appearancePalettes.ValueKind != JsonValueKind.Object) return;
        var palette = _appearancePalettes.GetProperty(Root.ActualTheme == ElementTheme.Dark ? "dark" : "light");
        var highContrast = new Windows.UI.ViewManagement.AccessibilitySettings().HighContrast;
        SolidColorBrush Brush(string key) => new(AppearanceControls.ParseColor(palette.GetProperty(key).GetString()!));
        foreach (var (button, icon, domain, large, selected) in _ribbonColors)
        {
            var active = button.IsEnabled && _snapshot.ValueKind == JsonValueKind.Object && selected?.Invoke() == true;
            icon.Foreground = Brush(button.IsEnabled ? domain : "DISABLED");
            RibbonIcons.Apply(icon, Root.ActualTheme == ElementTheme.Dark, button.IsEnabled, highContrast);
            if (highContrast) icon.Foreground = (Brush)Application.Current.Resources["SystemControlForegroundBaseHighBrush"];
            // 交互态由主题画笔键控制；逐项覆盖这些键即可保留原有的文字色、悬停、按下与选中反馈。
            // 主题切换时只改已有画笔的颜色，避免在主题通知里替换资源字典项。
            void Resource(string key, string token)
            {
                var color = AppearanceControls.ParseColor(palette.GetProperty(token).GetString()!);
                if (button.Resources.TryGetValue(key, out var value) && value is SolidColorBrush brush) brush.Color = color;
                else button.Resources[key] = new SolidColorBrush(color);
            }
            Resource("RibbonForegroundBrush", !button.IsEnabled ? "DISABLED" : active ? "TOGGLE_TEXT" : "TEXT");
            Resource("RibbonDisabledForegroundBrush", "DISABLED");
            Resource("RibbonItemHoverBrush", active ? "TOGGLE_HOVER_BG" : "HOVER_BG");
            Resource("RibbonItemPressedBrush", active ? "TOGGLE_PRESSED_BG" : "PRESSED_BG");
            Resource("RibbonItemCheckedBrush", "TOGGLE_BG");
            Resource("RibbonItemCheckedHoverBrush", "TOGGLE_HOVER_BG");
            Resource("RibbonItemCheckedBorderBrush", "TOGGLE_BORDER");
        }
    }

    private void SizeInitialWindow()
    {
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);
        var dpi = GetWindowDpi(hwnd);
        var scale = Math.Max(1, dpi / 96.0);
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        AppWindow.Resize(new SizeInt32((int)Math.Min(1360 * scale, area.Width), (int)Math.Min(900 * scale, area.Height)));
    }

    [System.Runtime.InteropServices.DllImport("user32.dll", EntryPoint = "GetDpiForWindow")]
    private static extern uint GetWindowDpi(nint hwnd);
}
