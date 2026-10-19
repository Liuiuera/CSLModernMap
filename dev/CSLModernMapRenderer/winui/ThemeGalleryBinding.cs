// 同步主题图库选项与界面状态。
using System.Collections.ObjectModel;
using RibbonSpace.Controls;

namespace CSLModernMapWinUI;

internal sealed class ThemeGalleryBinding(ObservableCollection<ThemeGalleryChoice> choices, Func<string, Task> select)
{
    private readonly List<RibbonGallery> _galleries = new();
    private bool _syncing, _pending;
    private string? _current;

    internal void Bind(RibbonGallery gallery)
    {
        gallery.ItemsSource = choices;
        _galleries.Add(gallery);
        gallery.ItemClick += async (_, args) =>
        {
            if (_syncing || _pending || args.Item is not ThemeGalleryChoice choice || choice.Id == _current) return;
            _pending = true;
            try { await select(choice.Id); }
            finally { _pending = false; Synchronize(_current); }
        };
        Synchronize(_current);
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
