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
        if (PreviewId is not null)
        {
            var uri = PreviewId.StartsWith("file:", StringComparison.OrdinalIgnoreCase)
                ? new Uri(PreviewId) : new Uri($"ms-appx:///Assets/Themes/{PreviewId}.png");
            Preview = new BitmapImage { CreateOptions = uri.IsFile ? BitmapCreateOptions.IgnoreImageCache : BitmapCreateOptions.None,
                UriSource = uri };
        }
    }

    public string Id { get; }
    public string Name { get; }
    public string Category => UiLanguage.Text(Id.StartsWith("user_", StringComparison.Ordinal) ? "ThemeEditor_Personal" : "ThemeEditor_Builtins");
    public string? PreviewId { get; }
    public BitmapImage? Preview { get; }
}
