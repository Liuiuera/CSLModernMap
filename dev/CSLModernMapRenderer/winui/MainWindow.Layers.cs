// 管理主窗口的图层列表和可见性。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using RibbonSpace;
using RibbonSpace.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    // 绑定属于页面和文档，而不是当前选中的地图对象。
    // 清空页面前注销绑定，避免延迟焦点或弹出事件写回。
    private sealed class LayerBinding
    {
        public required string Key;
        public required Control Control;
        public required StackPanel Scope;
        public required int Generation;
        public required Action ReadSnapshot;
        public required Func<bool> Enabled;
        public bool Syncing, Editing;
        public int PendingWrites;
    }
    private readonly List<LayerBinding> _layerBindings = new();
    private Task _layerWriteTail = Task.CompletedTask;
    private readonly Dictionary<string, RibbonToggleButton> _labelChildButtons = new();
    private readonly List<RibbonButton> _mapLayerSettingsButtons = new();
    private readonly Dictionary<string, RibbonToggleButton> _gameNamesButtons = new();
    private RibbonTab? _layersTab;
    private RibbonTab? _styleTab;
    private RibbonGroup? _layerContextGroup;
    private RibbonGroup? _facilitySelectionGroup;
    private RibbonButton? _returnToMapLayers;
    private HashSet<string>? _stylePageVisibleFieldKeys;
    private readonly (string Key, string Title)[] LabelChildren =
    {
        ("road_text", UiLanguage.Text("MainWindow_Layers_LabelChildren_8C919EE4A4")),
        ("district_text", UiLanguage.Text("MainWindow_Layers_LabelChildren_5939962905")),
        ("transit_icon", UiLanguage.Text("MainWindow_Layers_LabelChildren_4C2772DFEA")),
        ("transit_icon_text", UiLanguage.Text("MainWindow_Layers_LabelChildren_9239670615")),
        ("facility_icon", UiLanguage.Text("MainWindow_Layers_LabelChildren_905692C294")),
        ("facility_icon_text", UiLanguage.Text("MainWindow_Layers_LabelChildren_4AA668A329")),
        ("ordinary_icon", UiLanguage.Text("MainWindow_Layers_LabelChildren_5D8DBF6BD5")),
        ("ordinary_icon_text", UiLanguage.Text("MainWindow_Layers_LabelChildren_F82CD9C67B")),
        ("area_icon", UiLanguage.Text("Poi_AreaIcons")),
        ("area_icon_text", UiLanguage.Text("Poi_AreaNames")),
    };

    private bool LabelChildEnabled(string key) => key switch
    {
        "facility_icon_text" => _snapshot.GetProperty("poiVisibility").GetProperty("icons").GetProperty("facility").GetBoolean(),
        "ordinary_icon_text" => _snapshot.GetProperty("poiVisibility").GetProperty("icons").GetProperty("ordinary").GetBoolean(),
        "transit_icon_text" => _snapshot.GetProperty("poiVisibility").GetProperty("icons").GetProperty("transit").GetBoolean(),
        "area_icon_text" => _snapshot.GetProperty("poiVisibility").GetProperty("icons").GetProperty("area").GetBoolean(),
        "area_icon" or "facility_icon" or "ordinary_icon" or "transit_icon" => true,
        _ => LayerVisible("label"),
    };

    private static string LayerTitle(string layer) => RibbonIcons.LayerTitle(layer);

    private void ClearLayerPanel(StackPanel scope)
    {
        if (scope == SettingsPanel)
        {
            _regionPageRequest++; _stylePageVisibleFieldKeys = null;
            SettingsHeader.Children.Clear(); SettingsHeader.Visibility = Visibility.Collapsed;
            OverviewButton.Visibility = Visibility.Visible;
            _refreshPublicTransitSettings = null; _selectPublicTransitPage = null;
            _refreshMetroSettings = null;
        }
        _layerBindings.RemoveAll(binding => binding.Scope == scope);
        scope.Children.Clear();
    }

    private bool IsCurrent(LayerBinding binding) => !_closing &&
        binding.Generation == _documentGeneration && _layerBindings.Contains(binding);

    private LayerBinding BindLayerControl(string key, Control control, StackPanel scope, Action read, Func<bool>? enabled = null)
    {
        var binding = new LayerBinding { Key = key, Control = control, Scope = scope,
            Generation = _documentGeneration, ReadSnapshot = read, Enabled = enabled ?? (() => true) };
        _layerBindings.Add(binding);
        RefreshLayerBinding(binding);
        return binding;
    }

    private void RefreshLayerBinding(LayerBinding binding)
    {
        if (!IsCurrent(binding) || _snapshot.ValueKind != JsonValueKind.Object) return;
        binding.Syncing = true;
        try
        {
            binding.Control.IsEnabled = _documentEnabled && _context == "map" && binding.Enabled();
            if (binding.PendingWrites == 0 && !binding.Editing && !HasFocusedField(binding.Control)) binding.ReadSnapshot();
        }
        catch (Exception error)
        {
            // 错误字段不能污染后续快照，也不能逃逸异步界面事件。
            _layerBindings.Remove(binding);
            binding.Control.IsEnabled = false;
            SetError(UiLanguage.Format("MainWindow_Layers_RefreshLayerBinding_736346CBCE", binding.Key, error.Message));
        }
        finally { binding.Syncing = false; }
    }

    private void RefreshLayerBindings()
    {
        if (_snapshot.ValueKind != JsonValueKind.Object) return;
        foreach (var binding in _layerBindings.ToArray()) RefreshLayerBinding(binding);
        foreach (var child in LabelChildren)
        {
            if (!_labelChildButtons.TryGetValue(child.Key, out var button)) continue;
            button.IsChecked = _snapshot.GetProperty("labelChildren").GetProperty(child.Key).GetBoolean();
            button.IsEnabled = _documentEnabled && _context == "map" && LabelChildEnabled(child.Key);
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(button, child.Title +
                (button.IsChecked == true ? UiLanguage.Text("MainWindow_Layers_RefreshLayerBindings_645432A5BF") : UiLanguage.Text("MainWindow_Layers_RefreshLayerBindings_96E5815C15")));
        }
        foreach (var (key, button) in _gameNamesButtons)
        {
            button.IsChecked = _snapshot.GetProperty("fields").GetProperty(key).GetBoolean();
            button.IsEnabled = _documentEnabled && _context == "map" && LayerVisible("label");
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(button,
                button.Label + (button.IsChecked == true ? UiLanguage.Text("MainWindow_Layers_RefreshLayerBindings_645432A5BF") : UiLanguage.Text("MainWindow_Layers_RefreshLayerBindings_96E5815C15")));
        }
        ApplyRibbonColors();
    }

    private Task QueueLayerActionAsync(Func<Task> write, Func<bool> current)
    {
        var previous = _layerWriteTail;
        async Task Execute()
        {
            await previous;
            if (!current() || _context != "map") return;
            await RunAsync(async () =>
            {
                try { await write(); }
                finally { if (current()) await RefreshSnapshotAsync(); }
            });
        }
        return _layerWriteTail = Execute();
    }

    private async Task CommitLayerBindingAsync(LayerBinding binding, Func<Task> write)
    {
        if (!IsCurrent(binding) || binding.Syncing || !binding.Control.IsEnabled) return;
        binding.PendingWrites++;
        try { await QueueLayerActionAsync(write, () => IsCurrent(binding)); }
        finally
        {
            binding.PendingWrites--;
            RefreshLayerBindings();
        }
    }

    private bool LayerVisible(string layer) => _snapshot.GetProperty("layers").GetProperty(layer).GetBoolean();
    private bool FieldEnabled(string key)
    {
        if (!_snapshot.GetProperty("fieldDependencies").TryGetProperty(key, out var parents)) return true;
        return parents.EnumerateArray().All(parent => LayerVisible(parent.GetString()!));
    }

    private async Task SetLayerVisibleAsync(string layer, bool enabled)
    {
        await _engine.RequestAsync("setLayerVisibility", new { key = layer, enabled });
        SendViewer(LayerVisibilityCommand.Create(layer, enabled, _sceneId, _revision));
        if (layer == "label")
            SendViewer(LayerVisibilityCommand.Create("poi", enabled &&
                _snapshot.GetProperty("poiVisibility").GetProperty("icons").EnumerateObject().Any(item => item.Value.GetBoolean()),
                _sceneId, _revision));
        if (layer is "label" or "poi") await RefreshLivePreviewAsync();
    }

    private void AddLayerVisibility(StackPanel panel, StackPanel scope, string layer)
    {
        var toggle = AppearanceControls.Toggle(UiLanguage.Text("MainWindow_Layers_AddLayerVisibility_6A23362BFF"));
        panel.Children.Add(toggle);
        var binding = BindLayerControl("layer:" + layer, toggle, scope, () => toggle.IsOn = LayerVisible(layer));
        toggle.Toggled += async (_, _) =>
        {
            var enabled = toggle.IsOn;
            await CommitLayerBindingAsync(binding, () => SetLayerVisibleAsync(layer, enabled));
        };
    }

    private void AddRibbonLabelChildren(RibbonGroup group)
    {
        foreach (var child in LabelChildren)
        {
            var iconKey = child.Key switch
            {
                "area_icon" or "facility_icon" or "ordinary_icon" => "building_icon",
                "area_icon_text" or "facility_icon_text" or "ordinary_icon_text" => "building_icon_text",
                _ => child.Key,
            };
            var button = new RibbonToggleButton { Label = child.Title, Icon = RibbonIcons.Create("label." + iconKey),
                // 中号始终是图标加文字。
                Size = RibbonItemSize.Medium };
            button.Click += async (_, _) =>
            {
                var generation = _documentGeneration;
                await QueueLayerActionAsync(async () =>
                {
                    if (!_documentEnabled || !LabelChildEnabled(child.Key)) return;
                    var enabled = !_snapshot.GetProperty("labelChildren").GetProperty(child.Key).GetBoolean();
                    await SetLabelChildAsync(child.Key, enabled);
                }, () => !_closing && generation == _documentGeneration);
            };
            group.Items.Add(button); _labelChildButtons[child.Key] = button;
            ToolTipService.SetToolTip(button, child.Key switch
            {
                "transit_icon" => UiLanguage.Text("MainWindow_Layers_AddRibbonLabelChildren_621D762E4D"),
                "transit_icon_text" => UiLanguage.Text("MainWindow_Layers_AddRibbonLabelChildren_530FC28542"),
                _ => child.Title + UiLanguage.Text("MainWindow_Layers_AddRibbonLabelChildren_7D658F8419"),
            });
            RegisterRibbonColors(button, button.Icon, "PRIMARY", selected: () => _snapshot.GetProperty("labelChildren").GetProperty(child.Key).GetBoolean());
        }
        AddGameNamesButton(group, "road.game_names", UiLanguage.Text("TextIcons_RoadGameNames"));
        AddGameNamesButton(group, "poi.game_names", UiLanguage.Text("TextIcons_BuildingGameNames"));
        var details = new RibbonButton { Label = UiLanguage.Text("MainWindow_xaml_AddLayerGroup_4358ED3763"),
            Icon = new SymbolIcon(Symbol.Setting), Size = RibbonItemSize.Medium };
        details.Click += async (_, _) => await RunAsync(ShowMapPoiSettingsAsync);
        group.Items.Add(details);
        _mapLayerSettingsButtons.Add(details);
        RegisterRibbonColors(details, details.Icon, "PRIMARY");
        ToolTipService.SetToolTip(_layerButtons["label"], UiLanguage.Text("MainWindow_Layers_AddRibbonLabelChildren_86AD8E73C0"));
    }

    private void AddGameNamesButton(RibbonGroup group, string key, string title)
    {
        var button = new RibbonToggleButton { Label = title,
            Icon = new SymbolIcon(Symbol.Rename), Size = RibbonItemSize.Medium };
        _gameNamesButtons[key] = button;
        group.Items.Add(button);
        ToolTipService.SetToolTip(button, title);
        button.Click += async (_, _) =>
        {
            var generation = _documentGeneration;
            await QueueLayerActionAsync(async () =>
            {
                await UpdateFieldAsync(key,
                    !_snapshot.GetProperty("fields").GetProperty(key).GetBoolean());
            }, () => !_closing && generation == _documentGeneration);
        };
        RegisterRibbonColors(button, button.Icon, "PRIMARY", selected: () =>
            _snapshot.GetProperty("fields").GetProperty(key).GetBoolean());
    }

    private async Task SetLabelChildAsync(string key, bool enabled)
    {
        var result = await _engine.RequestAsync("setLabelChild", new { key, enabled,
            documentEpoch = _snapshot.GetProperty("documentEpoch").GetInt32() });
        SendViewer(LayerVisibilityCommand.Create("poi", result.GetProperty("poiVisible").GetBoolean(), _sceneId, _revision));
        await RefreshLivePreviewAsync();
    }

    private void AddLabelChild(StackPanel panel, StackPanel scope, string key)
    {
        var child = LabelChildren.Single(child => child.Key == key);
        var toggle = AppearanceControls.Toggle(child.Title);
        panel.Children.Add(toggle);
        var binding = BindLayerControl("child:" + key, toggle, scope,
            () => toggle.IsOn = _snapshot.GetProperty("labelChildren").GetProperty(key).GetBoolean(),
            () => LabelChildEnabled(key));
        toggle.Toggled += async (_, _) =>
        {
            var enabled = toggle.IsOn;
            await CommitLayerBindingAsync(binding, () => SetLabelChildAsync(key, enabled));
        };
    }

    private static StackPanel LayerSection(StackPanel panel, string title)
    {
        var body = new StackPanel { Spacing = 10, HorizontalAlignment = HorizontalAlignment.Stretch };
        panel.Children.Add(WorkspaceSection.Create(title, body));
        return body;
    }

    private void ShowLayerSettings(string layer, StackPanel? target = null)
    {
        var panel = target ?? SettingsPanel;
        var scope = target is null ? SettingsPanel : InspectorPanel;
        if (target is null)
        {
            ClearLayerPanel(SettingsPanel);
            _stylePageVisibleFieldKeys = new HashSet<string>(StringComparer.Ordinal);
        }
        else panel.Children.Clear();
        var unifiedLabels = target is null && layer is "label" or "poi";
        panel.Children.Add(new TextBlock { Text = unifiedLabels ? UiLanguage.Text("MainWindow_Layers_ShowLayerSettings_236E3F737A") : LayerTitle(layer) + UiLanguage.Text("MainWindow_Layers_ShowLayerSettings_DF3D58C7D8"),
            FontSize = 18, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
        if (_snapshot.ValueKind != JsonValueKind.Object) return;
        if (target is null && !unifiedLabels) AddLayerVisibility(LayerSection(panel, UiLanguage.Text("MainWindow_Layers_ShowLayerSettings_77EDD4A8FC")), scope, layer);
        if (unifiedLabels) BuildLabelSettings(panel, scope);
        else if (layer == "road")
        {
            var overall = LayerSection(panel, UiLanguage.Text("MainWindow_Layers_ShowLayerSettings_0155C1B620"));
            foreach (var key in new[] { "road.width", "road.tunnel", "road.bridge", "road.names" }) AddStyleLayerField(overall, scope, key);
            AddStyleLayerField(overall, scope, "road.accurate_intersections");
            overall.Children.Add(new TextBlock
            {
                Text = UiLanguage.Text("MainWindow_Layers_ShowLayerSettings_7B451F077B"),
                TextWrapping = TextWrapping.Wrap,
            });
            foreach (var (level, name) in new[] { ("highway", UiLanguage.Text("MainWindow_Layers_ShowLayerSettings_534C553126")), ("main", UiLanguage.Text("MainWindow_Layers_ShowLayerSettings_85F71EC8DA")), ("branch", UiLanguage.Text("MainWindow_Layers_ShowLayerSettings_109688E695")),
                         ("special", UiLanguage.Text("MainWindow_Layers_ShowLayerSettings_EDE0519A4C")), ("beautification", UiLanguage.Text("MainWindow_Layers_ShowLayerSettings_35CDE54CEB")) })
            {
                var body = LayerSection(panel, name);
                foreach (var attr in new[] { "fill", "border", "width" }) AddStyleLayerField(body, scope, $"road.level.{level}.{attr}");
            }
        }
        else
        {
            if (layer == "metro" && target is null) AddMapTransitTypes(panel, scope);
            var fields = LayerSection(panel, UiLanguage.Text("MainWindow_Layers_ShowLayerSettings_B693F5603C"));
            foreach (var field in _snapshot.GetProperty("layerFields").GetProperty(layer).EnumerateArray())
            {
                var key = field.GetString()!;
                if (layer == "building" && key == "building.shadow") continue;
                if (key is "poi.density" or "poi.merge" or "poi.distance") continue;
                AddStyleLayerField(fields, scope, key);
            }
            if (layer == "poi")
            {
                var body = LayerSection(panel, UiLanguage.Text("MainWindow_Layers_ShowLayerSettings_C8FFBDF157"));
                body.Children.Add(new TextBlock
                {
                    Text = UiLanguage.Text("MainWindow_Layers_ShowLayerSettings_FF45B08D51"),
                    TextWrapping = TextWrapping.Wrap,
                });
            }
        }
        if (target is null) AddStylePageReset(panel);
    }

    private void ShowLayerStylePage(string layer)
    {
        ShowLayerSettings(layer);
    }

    private void BuildLabelSettings(StackPanel panel, StackPanel scope)
    {
        AddPoiVisibilitySettings(panel, scope);
        var text = LayerSection(panel, UiLanguage.Text("MainWindow_Layers_BuildLabelSettings_14B69BD6EE"));
        foreach (var key in new[] { "road_text", "district_text" }) AddLabelChild(text, scope, key);
        foreach (var (kind, title) in new[] { ("road", UiLanguage.Text("MainWindow_Layers_BuildLabelSettings_9CD48244BA")), ("district", UiLanguage.Text("MainWindow_Layers_BuildLabelSettings_6570FEB07C")), ("building", UiLanguage.Text("MainWindow_Layers_BuildLabelSettings_89B0B5C5FE")),
                     ("stop", UiLanguage.Text("MainWindow_Layers_BuildLabelSettings_ACE021620F")) })
        {
            var body = LayerSection(text, title);
            foreach (var attr in new[] { "font", "size", "color", "outline" }) AddStyleLayerField(body, scope, $"label.{attr}.{kind}");
        }
        var icons = LayerSection(panel, UiLanguage.Text("MainWindow_Layers_BuildLabelSettings_72C2901572"));
        foreach (var key in new[] { "poi.set", "poi.size", "poi.font", "poi.name_size", "poi.name_stroke", "poi.name_stroke_ratio" })
            AddStyleLayerField(icons, scope, key);
        var places = new Button { Content = UiLanguage.Text("MainWindow_Layers_BuildLabelSettings_6E51C695FB") };
        places.Click += async (_, _) => await RunAsync(ShowMapPoiSettingsAsync);
        panel.Children.Add(places);
    }

    private void AddPoiVisibilitySettings(StackPanel panel, StackPanel scope)
    {
        var body = LayerSection(panel, UiLanguage.Text("MainWindow_Layers_AddPoiVisibilitySettings_BAB0CA718D"));
        if (HasCs2Document)
        {
            var facilities = new Button { Content = UiLanguage.Text("MainWindow_Layers_AddPoiVisibilitySettings_1A6C5F368B") };
            body.Children.Add(facilities);
            BindLayerControl("facility-selector", facilities, scope, () => { }, () => HasCs2Document && !_engineBusy);
            facilities.Click += async (_, _) => await RunAsync(ShowFacilitiesAsync);
        }
        body.Children.Add(new TextBlock
        {
            Text = UiLanguage.Text("MainWindow_Layers_AddPoiVisibilitySettings_478CCC95A5"),
            TextWrapping = TextWrapping.Wrap,
        });
        foreach (var (group, title) in new[] { ("transit", UiLanguage.Text("MainWindow_Layers_AddPoiVisibilitySettings_F7CCD76BF8")), ("facility", UiLanguage.Text("MainWindow_Layers_AddPoiVisibilitySettings_666DD3886C")), ("ordinary", UiLanguage.Text("MainWindow_Layers_AddPoiVisibilitySettings_85155A34A5")), ("area", UiLanguage.Text("Poi_Area")) })
        {
            var icons = AppearanceControls.Toggle(title + UiLanguage.Text("MainWindow_Layers_AddPoiVisibilitySettings_0D720EEEA2"));
            var names = AppearanceControls.Toggle(title + UiLanguage.Text("MainWindow_Layers_AddPoiVisibilitySettings_D44E9B3D3B"));
            names.Margin = new Thickness(20, 0, 0, 0);
            body.Children.Add(icons);
            body.Children.Add(names);
            BindPoiVisibilityControl(icons, scope, "icons", group);
            BindPoiVisibilityControl(names, scope, "names", group);
        }
    }

    private void BindPoiVisibilityControl(ToggleSwitch toggle, StackPanel scope, string channel, string group)
    {
        var binding = BindLayerControl($"poi:{channel}:{group}", toggle, scope,
            () => toggle.IsOn = _snapshot.GetProperty("poiVisibility").GetProperty(channel).GetProperty(group).GetBoolean(),
            () => channel == "icons" || _snapshot.GetProperty("poiVisibility").GetProperty("icons").GetProperty(group).GetBoolean());
        toggle.Toggled += async (_, _) =>
        {
            var enabled = toggle.IsOn;
            await CommitLayerBindingAsync(binding, async () =>
            {
                var visibility = JsonNode.Parse(_snapshot.GetProperty("poiVisibility").GetRawText())!.AsObject();
                visibility[channel]![group] = enabled;
                await _engine.RequestAsync("setPoiVisibility", new
                {
                    snapshot = visibility,
                    documentEpoch = _snapshot.GetProperty("documentEpoch").GetInt32(),
                });
                await RefreshLivePreviewAsync();
            });
        };
    }

    private void AddStyleLayerField(StackPanel panel, StackPanel scope, string key)
    {
        if (!_snapshot.GetProperty("fieldSchema").TryGetProperty(key, out _) ||
            !_snapshot.GetProperty("fields").TryGetProperty(key, out _)) return;
        if (scope == SettingsPanel) _stylePageVisibleFieldKeys?.Add(key);
        AddLayerField(panel, scope, key);
    }

    private void AddStylePageReset(StackPanel panel)
    {
        var keys = _stylePageVisibleFieldKeys?.ToArray() ?? [];
        if (keys.Length == 0) return;
        var reset = new Button { Content = UiLanguage.Text("MainWindow_Layers_AddStylePageReset_7F54C9658B"), HorizontalAlignment = HorizontalAlignment.Left };
        reset.Click += async (_, _) => await RunAsync(async () =>
        {
            if (_snapshot.ValueKind != JsonValueKind.Object || !_snapshot.TryGetProperty("documentEpoch", out var epoch))
            {
                SetError(UiLanguage.Text("MainWindow_Layers_AddStylePageReset_47356E84FC"));
                return;
            }
            await _engine.RequestAsync("resetFields", new { keys, documentEpoch = epoch.GetInt32() });
            await RefreshLivePreviewAsync();
            await RefreshSnapshotAsync();
        });
        panel.Children.Add(reset);
    }

    private void AddMapTransitTypes(StackPanel panel, StackPanel scope)
    {
        var body = LayerSection(panel, UiLanguage.Text("MainWindow_Layers_AddMapTransitTypes_8124770B3F"));
        foreach (var type in _snapshot.GetProperty("transitTypes").EnumerateArray())
        {
            var key = type.GetProperty("key").GetString()!;
            var toggle = AppearanceControls.Toggle(type.GetProperty("label").GetString()!);
            body.Children.Add(toggle);
            var binding = BindLayerControl("transit:" + key, toggle, scope,
                () => toggle.IsOn = _snapshot.GetProperty("mapTransitTypes").EnumerateArray().Any(item => item.GetString() == key));
            toggle.Toggled += async (_, _) =>
            {
                var enabled = toggle.IsOn;
                await CommitLayerBindingAsync(binding, async () =>
                {
                    await _engine.RequestAsync("setTransitType", new { key, enabled });
                    await ApplyContextAsync();
                });
            };
        }
    }

    private void AddLayerField(StackPanel panel, StackPanel scope, string key)
    {
        var field = _snapshot.GetProperty("fieldSchema").GetProperty(key);
        var label = field.GetProperty("label").GetString() ?? key;
        var kind = field.GetProperty("kind").GetString();
        JsonElement Value() => _snapshot.GetProperty("fields").GetProperty(key);
        async Task Write(object value)
        {
            await _engine.RequestAsync("setField", new { key, value });
            await RefreshLivePreviewAsync(backgroundRoadUpdate: key == "road.accurate_intersections");
        }
        if (kind == "toggle")
        {
            var toggle = AppearanceControls.Toggle(label); panel.Children.Add(toggle);
            var binding = BindLayerControl(key, toggle, scope, () => toggle.IsOn = Value().GetBoolean(), () => FieldEnabled(key));
            toggle.Toggled += async (_, _) =>
            {
                var enabled = toggle.IsOn;
                await CommitLayerBindingAsync(binding, () => Write(enabled));
            };
        }
        else if (kind == "number" && Value().ValueKind is JsonValueKind.Number or JsonValueKind.Object)
        {
            double Baseline(JsonElement value) => value.ValueKind == JsonValueKind.Object
                ? (value.TryGetProperty("fixed", out var fixedPart) ? fixedPart.GetDouble() : 0)
                    + (value.TryGetProperty("scaled", out var scaledPart) ? scaledPart.GetDouble() : 0)
                : value.GetDouble();
            var number = new NumberBox { Header = label, Minimum = field.GetProperty("low").GetDouble(),
                Maximum = field.GetProperty("high").GetDouble(), SmallChange = .1,
                SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
            panel.Children.Add(number);
            var saved = double.NaN;
            var binding = BindLayerControl(key, number, scope, () =>
            {
                saved = Baseline(Value()); number.Minimum = Math.Min(field.GetProperty("low").GetDouble(), saved);
                number.Maximum = Math.Max(field.GetProperty("high").GetDouble(), saved); number.Value = saved;
            }, () => FieldEnabled(key));
            number.LostFocus += async (_, _) =>
            {
                if (number.Value == saved || !IsCurrent(binding)) return;
                if (!double.IsFinite(number.Value)) { SetError(label + UiLanguage.Text("MainWindow_Layers_AddLayerField_6A2DEA0DF7")); return; }
                var value = number.Value;
                await CommitLayerBindingAsync(binding, () => Write(Value().ValueKind == JsonValueKind.Object
                    ? new { @fixed = 0, scaled = value } : (object)value));
            };
        }
        else if (kind == "color")
        {
            panel.Children.Add(new TextBlock { Text = label });
            var button = new Button(); var picker = new ColorPicker { IsAlphaEnabled = true, IsColorSliderVisible = true };
            var flyout = new Flyout { Content = picker }; button.Flyout = flyout; panel.Children.Add(button);
            var saved = AppearanceControls.ParseColor(Value().GetString()!);
            var binding = BindLayerControl(key, button, scope, () =>
            {
                button.Content = AppearanceControls.ColorContent(Value().GetString()!); picker.Color = saved = AppearanceControls.ParseColor(Value().GetString()!);
            }, () => FieldEnabled(key));
            flyout.Opened += (_, _) => binding.Editing = true;
            flyout.Closed += async (_, _) =>
            {
                binding.Editing = false;
                var color = picker.Color;
                if (!color.Equals(saved))
                {
                    var value = $"#{color.R:X2}{color.G:X2}{color.B:X2}" + (color.A == 255 ? "" : $"{color.A:X2}");
                    await CommitLayerBindingAsync(binding, () => Write(value));
                }
                RefreshLayerBindings();
            };
        }
        else if (kind is "choice" or "font")
        {
            var combo = new ComboBox { Header = label, HorizontalAlignment = HorizontalAlignment.Stretch };
            foreach (var choice in (kind == "font" ? _snapshot.GetProperty("fonts") : field.GetProperty("choices")).EnumerateArray())
                combo.Items.Add(new ComboBoxItem { Content = choice.GetProperty("label").GetString(), Tag = choice.GetProperty("value").Clone() });
            panel.Children.Add(combo);
            var binding = BindLayerControl(key, combo, scope, () =>
            {
                var value = Value();
                var selected = combo.Items.Cast<ComboBoxItem>().FirstOrDefault(item => ((JsonElement)item.Tag).ToString() == value.ToString());
                if (selected is null && kind == "font")
                {
                    selected = new ComboBoxItem { Content = value.GetString(), Tag = value.Clone() }; combo.Items.Add(selected);
                }
                combo.SelectedItem = selected;
                if (kind == "font") combo.FontFamily = new FontFamily(value.GetString()!);
            }, () => FieldEnabled(key));
            combo.SelectionChanged += async (_, _) =>
            {
                if (combo.SelectedItem is ComboBoxItem item) await CommitLayerBindingAsync(binding, () => Write((JsonElement)item.Tag));
            };
        }
        else
        {
            var box = new TextBox { Header = label }; panel.Children.Add(box); var saved = "";
            var binding = BindLayerControl(key, box, scope, () => box.Text = saved =
                Value().ValueKind == JsonValueKind.String ? Value().GetString() ?? "" : Value().GetRawText(), () => FieldEnabled(key));
            box.LostFocus += async (_, _) =>
            {
                if (!IsCurrent(binding) || box.Text == saved) return;
                object value = box.Text;
                if (Value().ValueKind is JsonValueKind.Object or JsonValueKind.Array)
                {
                    try { using var parsed = JsonDocument.Parse(box.Text); value = parsed.RootElement.Clone(); }
                    catch (JsonException) { SetError(label + UiLanguage.Text("MainWindow_Layers_AddLayerField_06A7DF7914")); return; }
                }
                await CommitLayerBindingAsync(binding, () => Write(value));
            };
        }
    }
}
