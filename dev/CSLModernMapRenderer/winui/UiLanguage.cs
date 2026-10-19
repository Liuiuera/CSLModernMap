// 加载界面语言资源并切换显示语言。
using System.Globalization;
using System.Text.Json;
using Microsoft.Windows.ApplicationModel.Resources;
using Microsoft.Windows.Globalization;

namespace CSLModernMapWinUI;

internal static class UiLanguage
{
    private static readonly ResourceManager Manager = new(Path.Combine(AppContext.BaseDirectory, "CSLModernMapRenderer.pri"));
    private static readonly ResourceMap Strings = Manager.MainResourceMap.GetSubtree("Resources");
    private static readonly ResourceContext Context = Manager.CreateResourceContext();
    internal static string Language { get; private set; } = "zh-CN";
    internal sealed record LanguageOption(string code, string resourceLanguage, string nativeName, string automaticAnnotation)
    {
        public string DisplayName => string.IsNullOrEmpty(automaticAnnotation) ? nativeName : $"{nativeName} ({automaticAnnotation})";
    }
    internal static readonly LanguageOption[] Options = JsonSerializer.Deserialize<LanguageOption[]>(
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Assets", "languages.json")))!;

    static UiLanguage() => SetLanguage("zh-CN");

    internal static void Configure(JsonElement metadata) => SetLanguage(metadata.GetProperty("language").GetString()!);

    private static void SetLanguage(string language)
    {
        var option = Options.FirstOrDefault(option => option.code == language) ?? Options.First(option => option.code == "zh-CN");
        Language = option.code;
        var qualifier = option.resourceLanguage;
        Context.QualifierValues["Language"] = qualifier;
        ApplicationLanguages.PrimaryLanguageOverride = qualifier;
    }

    public static string Text(string key) => Strings.GetValue(key, Context).ValueAsString;

    public static string Format(string key, params object?[] arguments) =>
        string.Format(CultureInfo.GetCultureInfo(Options.First(option => option.code == Language).resourceLanguage), Text(key), arguments);
}
