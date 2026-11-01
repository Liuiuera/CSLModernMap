// 两个窗口共用按需展开、复制的诊断内容。
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;

namespace CSLModernMapWinUI;

internal static class ErrorFeedback
{
    internal static StackPanel Actions(TaskError error, FrameworkElement root)
    {
        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        var view = new Button { Content = UiLanguage.Text("Failure_ViewDetails") };
        var copy = new Button { Content = UiLanguage.Text("Failure_CopyDetails") };
        view.Click += async (_, _) =>
        {
            view.IsEnabled = false;
            try
            {
                var dialog = new ContentDialog
                {
                    XamlRoot = root.XamlRoot, Title = UiLanguage.Text("Failure_ViewDetails"),
                    Content = new ScrollViewer { MaxHeight = Math.Max(160, Math.Min(460, root.ActualHeight - 180)), MaxWidth = 640,
                        Content = new TextBlock { Text = error.Details, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true } },
                    PrimaryButtonText = UiLanguage.Text("Failure_CopyDetails"),
                    CloseButtonText = UiLanguage.Text("Failure_Close"),
                };
                dialog.PrimaryButtonClick += (_, args) =>
                {
                    if (!Copy(error)) { args.Cancel = true; dialog.Title = UiLanguage.Text("Failure_CopyFailed"); }
                };
                await dialog.ShowAsync();
            }
            finally { view.IsEnabled = true; }
        };
        copy.Click += (_, _) => copy.Content = UiLanguage.Text(Copy(error) ? "Failure_Copied" : "Failure_CopyFailed");
        actions.Children.Add(view); actions.Children.Add(copy);
        return actions;
    }

    private static bool Copy(TaskError error)
    {
        try
        {
            var package = new DataPackage(); package.SetText(error.Details); Clipboard.SetContent(package);
            return true;
        }
        catch (System.Runtime.InteropServices.COMException exception)
        {
            StartupTrace.Write("Copy error details: " + exception);
            return false;
        }
    }
}
