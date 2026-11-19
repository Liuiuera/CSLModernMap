using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

internal sealed class DraftDensityInput : Grid
{
    private readonly CheckBox _densityEnabled = new();
    private readonly ComboBox _densityTier = new() { HorizontalAlignment = HorizontalAlignment.Stretch };
    internal DraftDensityInput(bool stacked = false)
    {
        foreach (var (value, label) in PoiProfileEditorPanel.DensityChoices)
            _densityTier.Items.Add(new ComboBoxItem { Content = label, Tag = value });
        _densityEnabled.Checked += (_, _) => _densityTier.IsEnabled = true;
        _densityEnabled.Unchecked += (_, _) => _densityTier.IsEnabled = false;
        AutomationProperties.SetAutomationId(_densityEnabled, "editor.densityEnabled");
        AutomationProperties.SetAutomationId(_densityTier, "editor.densityTier");
        AutomationProperties.SetName(_densityTier, UiLanguage.Text("CityEditor_IconDensity"));
        _densityEnabled.Content = new TextBlock {
            Text = UiLanguage.Text("PoiProfileEditorPanel_Build_C563296C44"), TextWrapping = TextWrapping.Wrap };
        if (stacked)
        {
            RowSpacing = 4;
            RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            SetRow(_densityTier, 1);
        }
        else
        {
            ColumnSpacing = 8;
            ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            _densityTier.Width = 112;
            SetColumn(_densityTier, 1);
        }
        Children.Add(_densityEnabled);
        Children.Add(_densityTier);
    }

    internal void Load(JsonElement poiProfile)
    {
        _densityEnabled.IsChecked = poiProfile.GetProperty("density_enabled").GetBoolean();
        _densityTier.SelectedItem = _densityTier.Items.Cast<ComboBoxItem>()
            .Single(item => (string)item.Tag == poiProfile.GetProperty("density_tier").GetString());
        _densityTier.IsEnabled = _densityEnabled.IsChecked == true;
    }

    internal JsonObject Snapshot() => new() {
        ["density_enabled"] = _densityEnabled.IsChecked == true,
        ["density_tier"] = (string)((ComboBoxItem)_densityTier.SelectedItem).Tag };
}
