// 定义主题图库中的可选项。
using Microsoft.UI.Xaml.Media.Imaging;

namespace CSLModernMapWinUI;

public sealed class ThemeGalleryChoice
{
    public ThemeGalleryChoice(string id, string name) : this(id, name, null) { }

    public ThemeGalleryChoice(string id, string name, string? previewId)
    {
        Id = id;
        Name = name;
        PreviewId = previewId ?? (id.StartsWith("user_", StringComparison.Ordinal) ? null : id);
        Preview = PreviewId is null ? null : new BitmapImage(new Uri($"ms-appx:///Assets/Themes/{PreviewId}.png"));
    }

    public string Id { get; }
    public string Name { get; }
    public string? PreviewId { get; }
    public BitmapImage? Preview { get; }
}
