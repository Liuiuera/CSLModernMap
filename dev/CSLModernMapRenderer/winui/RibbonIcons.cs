// 加载并呈现功能区图标。
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace CSLModernMapWinUI;

internal static class RibbonIcons
{
    private sealed record Definition(string Name, string Domain);
    private static readonly Dictionary<string, string[]> Definitions = JsonSerializer.Deserialize<Dictionary<string, string[]>>(
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Assets", "ribbon-icons.json")))!;
    private static readonly Dictionary<string, int> Codepoints = Regex.Matches(
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Assets", "Fonts", "tabler-icons.css")),
        @"\.ti-([\w-]+):before\s*\{[^}]*content:\s*[""']\\([0-9a-fA-F]+)")
        .ToDictionary(match => match.Groups[1].Value, match => Convert.ToInt32(match.Groups[2].Value, 16));

    internal static IconElement Create(string id, bool large = false)
    {
        var definition = Definitions.TryGetValue(id, out var found) ? found
            : throw new ArgumentException("Unknown ribbon icon ID: " + id, nameof(id));
        return Named(definition[0], definition[1], large);
    }

    internal static IconElement Named(string name, string domain = "PRIMARY", bool large = false)
    {
        if (!Codepoints.ContainsKey(name)) throw new ArgumentException("Unknown bundled Tabler icon: " + name);
        IconElement icon = large ? new BitmapIcon { ShowAsMonochrome = false, Width = 32, Height = 32 }
            : new FontIcon { Glyph = char.ConvertFromUtf32(Codepoints[name]), FontSize = 20,
                FontFamily = new FontFamily("ms-appx:///Assets/Fonts/tabler-icons.ttf#tabler-icons") };
        icon.Tag = new Definition(name, domain);
        Apply(icon, false, true, false);
        return icon;
    }

    internal static string Domain(IconElement icon, string fallback) => icon.Tag is Definition definition ? definition.Domain : fallback;

    internal static string LayerTitle(string layer) => layer switch
    {
        "terrain" => UiLanguage.Text("RibbonIcons_LayerTitle_51695AD45A"), "water" => UiLanguage.Text("RibbonIcons_LayerTitle_207E6D1CF5"), "road" => UiLanguage.Text("RibbonIcons_LayerTitle_A44C7BA211"), "building" => UiLanguage.Text("RibbonIcons_LayerTitle_5B46860A8E"),
        "built_area" => UiLanguage.Text("RibbonIcons_LayerTitle_433091EABE"), "poi_area" => UiLanguage.Text("RibbonIcons_LayerTitle_AD68267D54"), "railway" => UiLanguage.Text("RibbonIcons_LayerTitle_97492A0E5D"), "traffic" => UiLanguage.Text("RibbonIcons_LayerTitle_18B9E17D3D"),
        "metro" => UiLanguage.Text("RibbonIcons_LayerTitle_1AB216CC0D"), "poi" => UiLanguage.Text("RibbonIcons_LayerTitle_9FC7D0CF1D"), "label" => UiLanguage.Text("RibbonIcons_LayerTitle_4F0C1E4BF0"), "district" => UiLanguage.Text("RibbonIcons_LayerTitle_0983E2CCDD"),
        "zone" => UiLanguage.Text("RibbonIcons_LayerTitle_8592878D7E"), "land_use" => UiLanguage.Text("RibbonIcons_LayerTitle_D84EBADC9F"), "watermark" => UiLanguage.Text("RibbonIcons_LayerTitle_AA9F217C81"), "procedural_objects" => UiLanguage.Text("RibbonIcons_LayerTitle_87FC97C6B8"),
        _ => layer,
    };

    internal static void Apply(IconElement icon, bool dark, bool enabled, bool highContrast)
    {
        if (icon is not BitmapIcon bitmap || icon.Tag is not Definition definition) return;
        bitmap.ShowAsMonochrome = highContrast;
        var relative = highContrast ? $"mono/{definition.Name}.png"
            : $"{(dark ? "dark" : "light")}/{definition.Domain}-{definition.Name}.png";
        bitmap.UriSource = new Uri("ms-appx:///Assets/Ribbon/" + relative);
        bitmap.Opacity = enabled ? 1 : .4;
    }

    internal static IconElement Context(string title) => Create(title switch
    {
        var resourceTitle when resourceTitle == UiLanguage.Text("RibbonIcons_Context_7EE5D33BF1") => "context.stations",
        var resourceTitle when resourceTitle == UiLanguage.Text("RibbonIcons_Context_66A211F8F4") || resourceTitle == UiLanguage.Text("RibbonIcons_Context_CF2382056B") || resourceTitle == UiLanguage.Text("RibbonIcons_Context_FF48E2FFEB") || resourceTitle == UiLanguage.Text("RibbonIcons_Context_A8A9631DF4") => "context.names",
        var resourceTitle when resourceTitle == UiLanguage.Text("RibbonIcons_Context_ED178EDA0E") => "context.merge",
        var resourceTitle when resourceTitle == UiLanguage.Text("RibbonIcons_Context_661499ED97") => "context.auto_color",
        var resourceTitle when resourceTitle == UiLanguage.Text("RibbonIcons_Context_CB8F15F475") || resourceTitle == UiLanguage.Text("RibbonIcons_Context_784E7FAA7F") => "context.water",
        var resourceTitle when resourceTitle == UiLanguage.Text("RibbonIcons_Context_BB06598E0D") => "layer.road",
        var resourceTitle when resourceTitle == UiLanguage.Text("RibbonIcons_Context_20B638DD5D") || resourceTitle == UiLanguage.Text("RibbonIcons_Context_A3883FE83C") => "layer.district",
        var resourceTitle when resourceTitle == UiLanguage.Text("RibbonIcons_Context_8019E91690") => "context.facilities",
        var resourceTitle when resourceTitle == UiLanguage.Text("RibbonIcons_Context_4E10E29DA7") || resourceTitle == UiLanguage.Text("RibbonIcons_Context_23C662D4EA") => "context.scale",
        var resourceTitle when resourceTitle == UiLanguage.Text("RibbonIcons_Context_B81B14943D") => "context.lines",
        var resourceTitle when resourceTitle == UiLanguage.Text("RibbonIcons_Context_C1399D6D07") => "context.outline",
        var resourceTitle when resourceTitle == UiLanguage.Text("RibbonIcons_Context_5F6A03B217") || resourceTitle == UiLanguage.Text("RibbonIcons_Context_3FD85C9C2E") => "context.legend",
        _ => "view.context.advanced",
    });
}
