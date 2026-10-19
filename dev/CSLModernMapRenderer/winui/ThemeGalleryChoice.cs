// 定义主题图库中的可选项。
using Microsoft.UI.Xaml.Media.Imaging;

namespace CSLModernMapWinUI;

public sealed class ThemeGalleryChoice
{
    public ThemeGalleryChoice(string id, string name)
    {
        Id = id;
        Name = name;
        Preview = new BitmapImage(new Uri($"ms-appx:///Assets/Themes/{id}.png"));
    }

    public string Id { get; }
    public string Name { get; }
    public BitmapImage Preview { get; }
}
