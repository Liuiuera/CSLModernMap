// 同步主题图库选项与界面状态。
using System.Collections.ObjectModel;
using RibbonSpace.Controls;

namespace CSLModernMapWinUI;

internal sealed class ThemeGalleryBinding(ObservableCollection<ThemeGalleryChoice> choices,
    Func<string, Task> select, Func<object?>? editingSession = null)
{
    private readonly List<RibbonGallery> _galleries = new();
    private bool _syncing, _pending;
    private string? _current, _next;
    internal Task PendingSelection { get; private set; } = Task.CompletedTask;

    internal void Bind(RibbonGallery gallery)
    {
        gallery.ItemsSource = choices;
        gallery.CategorySelector = item => ((ThemeGalleryChoice)item).Category;
        _galleries.Add(gallery);
        gallery.ItemClick += async (_, args) =>
        {
            if (_syncing || args.Item is not ThemeGalleryChoice choice) return;
            if (_pending) { _next = choice.Id; return; }
            if (choice.Id != _current) await (PendingSelection = SelectAsync(choice.Id));
        };
        Synchronize(_current);
    }

    private async Task SelectAsync(string id)
    {
        var editor = editingSession?.Invoke();
        _pending = true;
        try
        {
            do {
                _next = null;
                await select(id);
                if (_next is not { } next || next == _current || editor is null || editor != editingSession?.Invoke()) break;
                id = next;
            } while (true);
        }
        finally { _pending = false; _next = null; Synchronize(_current); }
    }

    internal void Synchronize(string? id, Action? updateChoices = null)
    {
        _current = id;
        _syncing = true;
        try
        {
            updateChoices?.Invoke();
            var selected = choices.FirstOrDefault(choice => choice.Id == id);
            foreach (var gallery in _galleries) gallery.SelectedItem = selected;
        }
        finally { _syncing = false; }
    }
}
