using System.Xml.Linq;

namespace CSLModernMapWinUI;

// 无界面检查读取正式中文资源，不依赖 WinUI 资源加载器。
internal static class UiLanguage
{
    private static readonly Dictionary<string, string> Texts = XDocument.Load(
        Path.Combine(AppContext.BaseDirectory, "Resources.resw")).Root!.Elements("data")
        .ToDictionary(item => (string)item.Attribute("name")!, item => (string)item.Element("value")!);

    internal static string Text(string key) => Texts[key];
}
