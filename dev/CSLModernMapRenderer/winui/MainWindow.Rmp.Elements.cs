// 处理 RMP 元素的选择和编辑操作。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Imaging;
using RibbonSpace;
using RibbonSpace.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private void BuildRmpElements(RibbonGroup group)
    {
        AddButton(group, RmpText("AddTitle"), () => RunRmpCommandAsync("beginInsert", new { insertion = new { kind = "title" } }), "rmp.insertTitle");
        AddButton(group, RmpText("AddLegend"), () => RunRmpCommandAsync("beginInsert", new { insertion = new { kind = "legend" } }), "rmp.insertLegend");
        var icons = new RibbonDropDownButton { Label = RmpText("AddIcon"), Icon = RibbonIcons.Create("rmp.insertIcon"),
            Size = RibbonItemSize.Medium };
        var iconFlyout = new Flyout();
        icons.Flyout = iconFlyout;
        iconFlyout.Opening += (_, _) => iconFlyout.Content = BuildRmpFacilityPicker(iconFlyout);
        AutomationProperties.SetAutomationId(icons, "rmp-add-icon");
        RegisterCommand("rmp.insertIcon", () => Task.CompletedTask, true, icons);
        RegisterRibbonColors(icons, icons.Icon, "PRIMARY");
        group.Items.Add(icons);

        var body = new StackPanel { Spacing = 6, MaxWidth = 330 };
        var moreFlyout = new Flyout { Content = body };
        foreach (var (name, mode) in new[] { ("PlainText", "text"), ("HelperPoint", "virtual"), ("FillArea", "fill") })
        {
            var content = new StackPanel { Spacing = 3 };
            content.Children.Add(new TextBlock { Text = RmpText(name) });
            content.Children.Add(new TextBlock { Text = RmpText(name + "Help"), TextWrapping = TextWrapping.Wrap, Opacity = .75 });
            var button = new Button { Content = content, HorizontalContentAlignment = HorizontalAlignment.Stretch,
                HorizontalAlignment = HorizontalAlignment.Stretch };
            AutomationProperties.SetName(button, RmpText(name));
            AutomationProperties.SetAutomationId(button, "rmp-advanced-" + mode);
            RegisterCommand("rmp.advanced." + mode, async () =>
            {
                moreFlyout.Hide();
                await RunRmpCommandAsync("setMode", new { mode = "misc-node-" + mode });
            }, true, button);
            button.Click += async (_, _) => await ExecuteCommandAsync("rmp.advanced." + mode);
            body.Children.Add(button);
        }
        var more = new RibbonDropDownButton { Label = RmpText("MoreDrawing"), Icon = RibbonIcons.Create("rmp.moreDrawing"),
            Flyout = moreFlyout, Size = RibbonItemSize.Medium };
        AutomationProperties.SetAutomationId(more, "rmp-more-drawing");
        RegisterCommand("rmp.moreDrawing", () => Task.CompletedTask, true, more);
        RegisterRibbonColors(more, more.Icon, "PRIMARY");
        group.Items.Add(more);
    }

    private FrameworkElement BuildRmpFacilityPicker(Flyout flyout)
    {
        var panel = new StackPanel { Spacing = 8, Width = Math.Max(180, Math.Min(320, Root.ActualWidth - 64)) };
        var search = new TextBox { PlaceholderText = RmpText("SearchIcons") };
        AutomationProperties.SetName(search, RmpText("SearchIcons"));
        var list = new ListView { MaxHeight = 350, SelectionMode = ListViewSelectionMode.Single };
        AutomationProperties.SetAutomationId(list, "rmp-facility-picker");
        var choices = _rmpView!.Tools.GetProperty("facilities").EnumerateArray().Select(item => item.Clone()).ToArray();
        void Refresh()
        {
            list.Items.Clear();
            foreach (var choice in choices.Where(item => item.GetProperty("label").GetString()!.Contains(search.Text, StringComparison.CurrentCultureIgnoreCase)))
            {
                var id = choice.GetProperty("id").GetString()!;
                var label = choice.GetProperty("label").GetString()!;
                var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12 };
                row.Children.Add(new Image { Width = 36, Height = 36,
                    Source = new SvgImageSource(new Uri("ms-appx:///Assets/Rmp/images/facilities/" + id + ".svg")) });
                row.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center, TextWrapping = TextWrapping.Wrap, MaxWidth = 230 });
                var item = new ListViewItem { Content = row, Tag = id };
                AutomationProperties.SetName(item, label);
                list.Items.Add(item);
            }
        }
        search.TextChanged += (_, _) => Refresh();
        list.SelectionChanged += async (_, _) =>
        {
            if (list.SelectedItem is not ListViewItem item) return;
            var id = (string)item.Tag;
            flyout.Hide();
            await RunAsync(() => RunRmpCommandAsync("beginInsert", new { insertion = new { kind = "facility", facility = id } }));
        };
        panel.Children.Add(search); panel.Children.Add(list); Refresh();
        return panel;
    }
}
