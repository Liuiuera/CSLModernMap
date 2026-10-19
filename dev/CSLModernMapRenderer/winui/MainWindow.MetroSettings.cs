// 处理地铁示意图设置界面的状态与操作。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.Globalization.NumberFormatting;
using Windows.System;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private Action? _refreshMetroSettings;

    private void ShowMetroSettings(JsonElement schema)
    {
        string Text(string key) => UiLanguage.Text("MetroSettings_" + key);
        var page = new StackPanel { Spacing = 12 };
        SettingsPanel.Children.Add(page);
        SettingsHeader.Visibility = Visibility.Visible;
        OverviewButton.Visibility = Visibility.Collapsed;
        var generation = _documentGeneration;
        bool Current() => !_closing && generation == _documentGeneration && _context == "metro"
            && SettingsPanel.Children.Contains(page);
        var syncing = true;
        var controls = new List<(Control Control, Action Read, Func<bool> Enabled)>();
        JsonElement Value(string key, string? child = null) => child is null
            ? ContextOptions(true).GetProperty(key) : ContextOptions(true).GetProperty(key).GetProperty(child);
        Task Write(string key, object value, string? child = null) => child is null
            ? _engine.RequestAsync("setTransitOption", new { metro = true, key, value })
            : WriteContextChildAsync(true, key, child, value);
        Task Queue(Func<Task> write) => QueueContextWriteAsync(true, write, Current);
        bool Editable(Control control)
        {
            if (syncing || !Current() || !control.IsEnabled) return false;
            for (FrameworkElement? element = control; element is not null; element = element.Parent as FrameworkElement)
                if (element.Visibility == Visibility.Collapsed) return false;
            return true;
        }
        void Bind(Control control, string key, Action read, Func<bool>? enabled = null)
        {
            AutomationProperties.SetAutomationId(control, "metro-" + key);
            controls.Add((control, read, enabled ?? (() => true)));
            read();
        }
        TextBlock Label(string text) => new() { Text = text, TextWrapping = TextWrapping.Wrap };
        StackPanel Section(StackPanel parent, string title)
        {
            var body = new StackPanel { Spacing = 6 };
            body.Children.Add(new TextBlock { Text = title, FontSize = 14,
                FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
            parent.Children.Add(body); return body;
        }
        CheckBox Check(string key, string label, string? child = null)
        {
            var box = new CheckBox { Content = Label(label), MinWidth = 0, HorizontalAlignment = HorizontalAlignment.Stretch };
            Bind(box, child ?? key, () => box.IsChecked = Value(key, child).GetBoolean());
            box.Checked += Changed; box.Unchecked += Changed;
            async void Changed(object sender, RoutedEventArgs args)
            {
                if (!Editable(box)) return;
                var value = box.IsChecked == true;
                await Queue(() => Write(key, value, child));
            }
            return box;
        }
        FrameworkElement Number(string key, string label, Func<bool>? enabled = null,
            bool slider = false, double multiplier = 1, double step = 1, string? child = null)
        {
            // adaptive_strength 是唯一嵌套数值选项，协议范围为零到一。
            var minimum = child is null ? schema.GetProperty(key).GetProperty("low").GetDouble() * multiplier : 0;
            var maximum = child is null ? schema.GetProperty(key).GetProperty("high").GetDouble() * multiplier : multiplier;
            var message = UiLanguage.Format("MetroSettings_NumberRange", label, minimum, maximum);
            var input = new NumberBox { Minimum = minimum, Maximum = maximum, SmallChange = step,
                MinWidth = 0, HorizontalAlignment = HorizontalAlignment.Stretch,
                ValidationMode = NumberBoxValidationMode.Disabled,
                SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
            AutomationProperties.SetName(input, label);
            var saved = Value(key, child).GetDouble() * multiplier;
            Slider? range = slider ? new Slider { Minimum = minimum, Maximum = maximum,
                StepFrequency = step, VerticalAlignment = VerticalAlignment.Center } : null;
            Bind(input, child ?? key, () =>
            {
                input.Value = saved = Value(key, child).GetDouble() * multiplier;
                if (range is not null) range.Value = input.Value;
            }, enabled);
            async Task Commit(TextBox? editor = null)
            {
                if (!Editable(input)) return;
                var amount = editor is null ? input.Value
                    : ((INumberParser)input.NumberFormatter).ParseDouble(editor.Text.Trim()) ?? double.NaN;
                if (!double.IsFinite(amount) || amount < minimum || amount > maximum)
                {
                    OperationError.Severity = InfoBarSeverity.Warning;
                    OperationError.Message = message; OperationError.IsOpen = true;
                    return;
                }
                if (OperationError.Message == message) OperationError.IsOpen = false;
                if (amount == saved) return;
                input.Value = saved = amount;
                await Queue(() => Write(key, amount / multiplier, child));
            }
            input.LostFocus += async (_, args) => await Commit(args.OriginalSource as TextBox);
            // NumberBox 在按键抬起时处理回车。
            input.AddHandler(UIElement.KeyUpEvent, new KeyEventHandler(async (_, args) =>
            {
                if (args.Key == VirtualKey.Enter) await Commit(args.OriginalSource as TextBox);
            }), handledEventsToo: true);
            if (range is null) { input.Header = Label(label); return input; }
            input.Width = 76;
            AutomationProperties.SetName(range, label);
            Bind(range, (child ?? key) + "-slider", () => range.Value = input.Value, enabled);
            range.ValueChanged += (_, args) => { if (!syncing) input.Value = args.NewValue; };
            input.ValueChanged += (_, _) =>
            {
                if (!syncing && double.IsFinite(input.Value) && input.Value >= minimum && input.Value <= maximum)
                    range.Value = input.Value;
            };
            range.PointerCaptureLost += async (_, _) => await Commit();
            range.LostFocus += async (_, _) => await Commit();
            var row = new Grid { ColumnSpacing = 8 };
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            row.Children.Add(range); Grid.SetColumn(input, 1); row.Children.Add(input);
            var field = new StackPanel { Spacing = 2 };
            field.Children.Add(Label(label)); field.Children.Add(row); return field;
        }
        SettingsHeader.Children.Add(new TextBlock { Text = RmpText("GenerationSettings"), FontSize = 18,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        page.Children.Add(new TextBlock { Text = RmpText("GenerationHelp"), TextWrapping = TextWrapping.Wrap });
        var types = Section(page, Text("TransitTypes"));
        var choose = new Button { Content = Text("ChooseLines"), HorizontalAlignment = HorizontalAlignment.Stretch };
        choose.Click += async (_, _) => await RunAsync(ShowTransitLinesAsync);
        types.Children.Add(choose); AddLineSelectionSummary(types, true);
        Bind(choose, "choose-lines", () => { });
        AddTransitTypes(types, true, Current);
        var merge = Section(page, Text("StationMerge"));
        merge.Children.Add(Check("merge_enabled", Text("MergeEnabled")));
        merge.Children.Add(Number("merge_range", Text("MergeRange"), () => Value("merge_enabled").GetBoolean()));
        var layout = Section(page, Text("Arrangement"));
        layout.Children.Add(Number("spacing", Text("StationSpacing"), step: 1));
        layout.Children.Add(Check("schematic_options", Text("AdaptiveLayout"), "adaptive_scale"));
        layout.Children.Add(Number("schematic_options", Text("AdaptiveStrength"), () => Value("schematic_options", "adaptive_scale").GetBoolean(),
            slider: true, multiplier: 100, child: "adaptive_strength"));
        var water = Section(page, Text("Background"));
        water.Children.Add(Check("schematic_water", Text("Water")));
        water.Children.Add(Number("water_smoothing", Text("WaterSmoothing"), () => Value("schematic_water").GetBoolean(), slider: true, step: .5));
        page.Children.Add(Check("facilities_enabled", Text("ShowFacilities")));
        page.Children.Add(new TextBlock { Text = RmpText("ApplyWarning"), TextWrapping = TextWrapping.Wrap });
        var apply = new Button { Content = RmpText("Apply"), HorizontalAlignment = HorizontalAlignment.Stretch };
        Bind(apply, "apply", () => { }, () => CommandEnabled("view.metro.refresh", _commands["view.metro.refresh"]));
        apply.Loaded += (_, _) =>
        {
            RegisterCommand("view.metro.refresh", GenerateRmpAsync, true, apply);
            UpdateCommandStates();
        };
        apply.Unloaded += (_, _) => _commands["view.metro.refresh"].Controls.Remove(apply);
        apply.Click += async (_, _) => await ExecuteCommandAsync("view.metro.refresh");
        page.Children.Add(apply);
        _refreshMetroSettings = () =>
        {
            if (!Current()) return;
            syncing = true;
            try
            {
                foreach (var (control, read, enabled) in controls)
                {
                    control.IsEnabled = _documentEnabled && !RmpBlocked && enabled();
                    if (!HasFocusedField(control)) read();
                }
            }
            finally { syncing = false; }
        };
        _refreshMetroSettings();
    }
}
