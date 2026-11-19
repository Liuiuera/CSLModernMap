using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MapWorkflowDialog : ContentDialog
{
    private readonly Button _draftOption;
    internal bool? UseDraft { get; private set; }
    internal bool RememberChoice => Remember.IsChecked == true;

    internal MapWorkflowDialog(MapWorkflow workflow)
    {
        InitializeComponent();
        var export = workflow == MapWorkflow.Export;
        _draftOption = export ? SecondaryOption : PrimaryOption;
        Title = UiLanguage.Text(export ? "ExportChoice_Title" : "ThemeChoice_Title");
        PrimaryIcon.Symbol = export ? Symbol.Save : Symbol.Map;
        SecondaryIcon.Symbol = export ? Symbol.Edit : Symbol.Setting;
        PrimaryTitle.Text = UiLanguage.Text(export ? "ExportChoice_Direct" : "ThemeChoice_Interactive");
        PrimaryDescription.Text = UiLanguage.Text(export ? "ExportChoice_DirectHelp" : "ThemeChoice_Description");
        SecondaryTitle.Text = UiLanguage.Text(export ? "ExportChoice_Edit" : "ThemeChoice_Traditional");
        if (export) SecondaryDescription.Text = UiLanguage.Text("ExportChoice_EditHelp");
        else SecondaryDescription.Visibility = Visibility.Collapsed;
        Remember.Content = UiLanguage.Text("Workflow_Remember");
        Unavailable.Text = UiLanguage.Text("ThemeChoice_Unavailable");
        CloseButtonText = UiLanguage.Text("Artwork_Cancel");
        AutomationProperties.SetAutomationId(PrimaryOption, export ? "export.chooseDirect" : "theme.chooseInteractive");
        AutomationProperties.SetAutomationId(SecondaryOption, export ? "export.chooseEdit" : "theme.chooseTraditional");
        AutomationProperties.SetName(PrimaryOption, PrimaryTitle.Text);
        AutomationProperties.SetName(SecondaryOption, SecondaryTitle.Text);
        AutomationProperties.SetHelpText(PrimaryOption, PrimaryDescription.Text);
        AutomationProperties.SetHelpText(SecondaryOption, SecondaryDescription.Text);
    }

    internal void SetDraftAvailable(bool available)
    {
        _draftOption.IsEnabled = available;
        Unavailable.Visibility = available ? Visibility.Collapsed : Visibility.Visible;
    }

    private void SelectOption(object sender, RoutedEventArgs args)
    {
        UseDraft = sender == _draftOption;
        Hide();
    }
}
