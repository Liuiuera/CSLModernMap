// 定义 POI 样式选择项的界面表示。
using Microsoft.UI.Xaml.Media.Imaging;

namespace CSLModernMapWinUI;

public sealed class PoiStyleChoice
{
    public PoiStyleChoice(string id, string name)
    {
        Id = id;
        Name = name;
        Preview = new BitmapImage(new Uri($"ms-appx:///Assets/Themes/poi-{id}.png"));
    }
    public string Id { get; }
    public string Name { get; }
    public BitmapImage Preview { get; }
}
