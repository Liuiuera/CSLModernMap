// 生成和加载界面预览所需的图像。
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Storage;
using Windows.Storage.Streams;
using Windows.Graphics.Imaging;

namespace CSLModernMapWinUI;

internal static class PreviewImages
{
    public static async Task<BitmapImage> LoadAsync(string url, int maxEdge = 0)
    {
        var uri = new Uri(url, UriKind.Absolute);
        using IRandomAccessStream stream = uri.IsFile
            ? await (await StorageFile.GetFileFromPathAsync(uri.LocalPath)).OpenReadAsync()
            : await RandomAccessStreamReference.CreateFromUri(uri).OpenReadAsync();
        var bitmap = new BitmapImage();
        if (maxEdge > 0)
        {
            var decoder = await BitmapDecoder.CreateAsync(stream);
            var edge = Math.Max(decoder.PixelWidth, decoder.PixelHeight);
            if (edge > maxEdge)
                bitmap.DecodePixelWidth = Math.Max(1, (int)Math.Round(decoder.PixelWidth * (double)maxEdge / edge));
            stream.Seek(0);
        }
        await bitmap.SetSourceAsync(stream);
        return bitmap;
    }
    public static async Task SetAsync(Image image, string url, Func<bool> current, Action<string>? failed = null, Action? loaded = null, int maxEdge = 0)
    {
        try { var bitmap = await LoadAsync(url, maxEdge); if (current()) { image.Source = bitmap; loaded?.Invoke(); } }
        catch (Exception error) { if (current()) failed?.Invoke(error.Message); }
    }
}
