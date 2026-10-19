// 展示 RMP 相关信息和说明。
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private async Task ShowRmpAboutAsync()
    {
        var content = new StackPanel { Spacing = 16, MaxWidth = 520 };
        void Text(string value) => content.Children.Add(new TextBlock
        {
            Text = value, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true,
        });
        content.Children.Add(new TextBlock
        {
            Text = "Rail Map Painter 6.0.18", FontSize = 20,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold,
        });
        Text(RmpText("AboutModified"));
        Text(RmpText("AboutCredits"));
        Text(RmpText("AboutLicense"));
        foreach (var (key, path) in new[]
        {
            ("AboutLicenseText", Path.Combine("Assets", "Rmp", "licenses", "RMP-LICENSE.txt")),
            ("AboutImageTerms", Path.Combine("Assets", "Rmp", "licenses", "RMP-IMAGE-TERMS.txt")),
            ("AboutNotices", Path.Combine("Assets", "Licenses", "THIRD_PARTY_NOTICES.md")),
        })
        {
            var text = await File.ReadAllTextAsync(Path.Combine(AppContext.BaseDirectory, path));
            content.Children.Add(new Expander
            {
                Header = RmpText(key), HorizontalAlignment = HorizontalAlignment.Stretch,
                Content = new ScrollViewer
                {
                    MaxHeight = 240, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
                    Content = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true },
                },
            });
        }
        await new ContentDialog
        {
            XamlRoot = Root.XamlRoot, Title = RmpText("About"),
            Content = new ScrollViewer { Content = content, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled },
            CloseButtonText = UiLanguage.Text("LanguageNotice_Close"), DefaultButton = ContentDialogButton.Close,
        }.ShowAsync();
    }
}
