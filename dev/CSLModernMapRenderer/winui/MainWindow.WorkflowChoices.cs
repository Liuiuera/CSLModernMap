namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private MapWorkflowDialog? _workflowDialog;
    private bool CanChooseDraft => CommandEnabled("map.edit_city", _commands["map.edit_city"]);

    private async Task<bool?> ChooseWorkflowAsync(MapWorkflow workflow)
    {
        if (_workflowDialog is not null || _openingArtwork) return null;
        var saved = UserData.ReadWorkflowChoice(_ribbonLayoutDirectory, workflow);
        if (saved is false || saved is true && CanChooseDraft) return saved;

        var generation = _documentGeneration;
        var context = _context;
        var dialog = new MapWorkflowDialog(workflow) { XamlRoot = Root.XamlRoot, RequestedTheme = Root.ActualTheme };
        dialog.SetDraftAvailable(CanChooseDraft);
        _workflowDialog = dialog;
        try { await dialog.ShowAsync(); }
        finally { _workflowDialog = null; }
        if (_closing || _allowMainClose || generation != _documentGeneration || context != _context ||
            dialog.UseDraft is not bool useDraft || useDraft && !CanChooseDraft) return null;
        if (dialog.RememberChoice)
            UserData.RememberWorkflowChoice(_ribbonLayoutDirectory, workflow, useDraft);
        return useDraft;
    }

    private async Task ChooseThemeEditorAsync()
    {
        if (_artworkEditor is not null) { await EditThemeAsync(); return; }
        switch (await ChooseWorkflowAsync(MapWorkflow.Theme))
        {
            case true: await ExecuteCommandAsync("map.edit_city"); break;
            case false: await EditThemeAsync(); break;
        }
    }

    private async Task ChooseExportAsync(string? format = null, string? context = null)
    {
        if (_cityArtwork) { await ShowExportAsync(format); return; }
        if (_exportWindow is { } existing) { existing.Activate(); return; }
        switch (await ChooseWorkflowAsync(MapWorkflow.Export))
        {
            case true: await ExecuteCommandAsync("map.edit_city"); break;
            case false: await ShowExportAsync(format, context); break;
        }
    }
}
