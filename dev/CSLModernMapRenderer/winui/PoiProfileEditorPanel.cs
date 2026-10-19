// 编辑不同视图的 POI 显示配置。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

internal sealed class PoiProfileEditorPanel
{
    private readonly Func<JsonObject, Task> _changed;
    private readonly Action<string>? _validationMessage;
    private readonly List<string> _visibleKeys = new();
    private readonly List<Action> _checks = new();
    private readonly StackPanel _root = new() { Spacing = 12 };
    private readonly TextBlock _status = new() { TextWrapping = TextWrapping.Wrap };
    private JsonObject _profile;

    public PoiProfileEditorPanel(string target, JsonElement profile,
        Func<JsonObject, Task> changed, Action<string>? validationMessage = null)
    {
        _profile = JsonNode.Parse(profile.GetRawText())!.AsObject();
        _changed = changed; _validationMessage = validationMessage;
        Build();
    }

    public FrameworkElement Content => _root;
    public IReadOnlyList<string> VisibleKeys => _visibleKeys;
    public JsonObject Snapshot => (JsonObject)_profile.DeepClone();

    internal static (string Value, string Label)[] DensityChoices =>
    [
        ("extremely_sparse", UiLanguage.Text("PoiDensity_ExtremelySparse")),
        ("very_sparse", UiLanguage.Text("PoiDensity_VerySparse")),
        ("sparse", UiLanguage.Text("PoiProfileEditorPanel_Build_E3521DB829")),
        ("standard", UiLanguage.Text("PoiProfileEditorPanel_Build_6BEA77ACEF")),
        ("dense", UiLanguage.Text("PoiProfileEditorPanel_Build_61AACC9FD8")),
        ("very_dense", UiLanguage.Text("PoiDensity_VeryDense")),
        ("extremely_dense", UiLanguage.Text("PoiDensity_ExtremelyDense")),
    ];

    public void Commit()
    {
        foreach (var check in _checks) check();
    }

    public void Load(JsonElement profile)
    {
        _profile = JsonNode.Parse(profile.GetRawText())!.AsObject();
        _root.Children.Clear(); _visibleKeys.Clear(); _checks.Clear();
        _status.Text = ""; Build();
    }

    private void Build()
    {
        var master = AddToggle("merge_enabled", UiLanguage.Text("PoiProfileEditorPanel_Build_FBA7C62AC0"));
        _root.Children.Add(master);
        var gated = new List<(CheckBox Toggle, NumberBox? Distance)>();
        foreach (var (group, title, description) in new[]
        {
            ("transit", UiLanguage.Text("PoiProfileEditorPanel_Build_2480389050"), UiLanguage.Text("PoiProfileEditorPanel_Build_9FB9D886FD")),
            ("ordinary", UiLanguage.Text("PoiProfileEditorPanel_Build_9965945D5D"), UiLanguage.Text("PoiProfileEditorPanel_Build_2BDBD22B8E")),
            ("facility", UiLanguage.Text("PoiProfileEditorPanel_Build_8A16E15823"), UiLanguage.Text("PoiProfileEditorPanel_Build_FFA0A4B20C")),
        })
        {
            var toggle = AddToggle($"groups.{group}.merge_enabled", title);
            var copy = new StackPanel { Spacing = 4 };
            copy.Children.Add(toggle); copy.Children.Add(Help(description));
            NumberBox? distance = group == "facility" ? null
                : AddNumber($"groups.{group}.distance_m", UiLanguage.Text("PoiProfileEditorPanel_Build_8CC740C6CC"), 0, 500);
            var row = distance is null ? (FrameworkElement)copy : new AppearanceFieldGrid(copy, distance);
            _root.Children.Add(ExportDesign.Divider(row));
            gated.Add((toggle, distance));
        }
        var density = AddToggle("density_enabled", UiLanguage.Text("PoiProfileEditorPanel_Build_C563296C44"));
        _root.Children.Add(ExportDesign.Divider(density));
        var tier = AddChoice("density_tier", UiLanguage.Text("PoiProfileEditorPanel_Build_163850E3D5"),
            DensityChoices);
        _root.Children.Add(tier);
        _root.Children.Add(AddToggle("overlap_avoidance", UiLanguage.Text("PoiProfileEditorPanel_Build_3C5D5EA4AB")));
        var advanced = new StackPanel { Spacing = 10 };
        var differentNames = AddToggle("allow_different_station_names", UiLanguage.Text("PoiProfileEditorPanel_Build_A2FBF72184"));
        var children = AddToggle("png_internal_buildings", UiLanguage.Text("Poi_PngInternalBuildings"));
        advanced.Children.Add(differentNames); advanced.Children.Add(children);
        _visibleKeys.Add("children_min_zoom");
        var zoom = AppearanceControls.Number(UiLanguage.Text("PoiProfileEditorPanel_Build_28D5C72226"), Read("children_min_zoom"), 0, 2,
            value => Set("children_min_zoom", value), _checks);
        advanced.Children.Add(zoom);
        advanced.Children.Add(Help(UiLanguage.Text("PoiProfileEditorPanel_Build_00AB0A0BBD")));
        _root.Children.Add(WorkspaceSection.Create(UiLanguage.Text("PoiProfileEditorPanel_Build_CBF9A18431"), advanced, expanded: false));
        _root.Children.Add(_status);

        void ApplyGates()
        {
            foreach (var (toggle, distance) in gated)
            {
                toggle.IsEnabled = master.IsChecked == true;
                if (distance is not null) distance.IsEnabled = master.IsChecked == true && toggle.IsChecked == true;
            }
            differentNames.IsEnabled = master.IsChecked == true && gated[0].Toggle.IsChecked == true;
            tier.IsEnabled = density.IsChecked == true;
            zoom.IsEnabled = true;
        }
        foreach (var toggle in gated.Select(item => item.Toggle).Append(master).Append(density).Append(children))
        {
            toggle.Checked += (_, _) => ApplyGates(); toggle.Unchecked += (_, _) => ApplyGates();
        }
        ApplyGates();
    }

    private static TextBlock Help(string text)
    {
        var copy = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap };
        ExportDesign.Secondary(copy); return copy;
    }

    private CheckBox AddToggle(string path, string title)
    {
        _visibleKeys.Add(path);
        var toggle = new CheckBox { Content = new TextBlock { Text = title, TextWrapping = TextWrapping.Wrap },
            IsChecked = Read(path).GetValue<bool>() };
        async void Changed(object sender, RoutedEventArgs args)
        {
            Set(path, JsonValue.Create(toggle.IsChecked == true)); await SaveAsync();
        }
        toggle.Checked += Changed; toggle.Unchecked += Changed;
        return toggle;
    }

    private NumberBox AddNumber(string path, string title, double minimum, double maximum)
    {
        _visibleKeys.Add(path);
        var number = new NumberBox { Header = title, Minimum = minimum, Maximum = maximum,
            SmallChange = 1, Value = Read(path).GetValue<double>(),
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
        void CommitNumber()
        {
            if (!double.IsFinite(number.Value) || number.Value < minimum || number.Value > maximum)
                throw new InvalidOperationException(string.Format(UiLanguage.Text("PoiProfileEditorPanel_AddNumber_0BB1EFDA65"),
                    title, minimum, maximum));
            Set(path, JsonValue.Create(number.Value));
        }
        _checks.Add(CommitNumber);
        number.ValueChanged += async (_, _) =>
        {
            if (!double.IsFinite(number.Value) || number.Value < minimum || number.Value > maximum) return;
            CommitNumber(); await SaveAsync();
        };
        return number;
    }

    private ComboBox AddChoice(string path, string title, (string Value, string Label)[] choices)
    {
        _visibleKeys.Add(path);
        var combo = new ComboBox { Header = title, HorizontalAlignment = HorizontalAlignment.Stretch };
        foreach (var (value, label) in choices)
        {
            var item = new ComboBoxItem { Content = label, Tag = value };
            combo.Items.Add(item); if (value == Read(path).GetValue<string>()) combo.SelectedItem = item;
        }
        combo.SelectionChanged += async (_, _) =>
        {
            Set(path, JsonValue.Create((string)((ComboBoxItem)combo.SelectedItem).Tag)); await SaveAsync();
        };
        return combo;
    }

    private JsonNode Read(string path)
    {
        JsonNode node = _profile;
        foreach (var part in path.Split('.')) node = node[part]!;
        return node;
    }

    private void Set(string path, JsonNode? value)
    {
        var parts = path.Split('.');
        JsonObject node = _profile;
        foreach (var part in parts[..^1]) node = node[part]!.AsObject();
        node[parts[^1]] = value;
    }

    private async Task SaveAsync()
    {
        try { await _changed(Snapshot); }
        catch (Exception error) { _status.Text = error.Message; _validationMessage?.Invoke(error.Message); }
    }
}
