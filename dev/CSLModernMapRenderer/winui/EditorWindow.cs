// 承载地图编辑页面及其窗口交互。
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Markup;
using Microsoft.UI.Composition.SystemBackdrops;
using Windows.Graphics;

namespace CSLModernMapWinUI;

// 提供可调整尺寸、支持异步取消关闭的编辑器窗口。
internal sealed class EditorWindow : Window
{
    private readonly TaskCompletionSource _completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private bool _allowClose, _checkingClose;
    public Grid Root { get; }
    public Grid Actions { get; } = new() { ColumnSpacing = 8, RowSpacing = 8 };
    public Func<Task<bool>>? CanCloseAsync { get; set; }
    public Control? InputCommitTarget { get; set; }
    private bool _actionsRightAligned;
    public bool ActionsRightAligned
    {
        get => _actionsRightAligned;
        set { _actionsRightAligned = value; LayoutActions(); }
    }
    public EditorWindow(string title, double width, double height, bool translucent = false, Window? owner = null, bool modal = false)
    {
        Title = title;
        var effects = new Windows.UI.ViewManagement.UISettings().AdvancedEffectsEnabled;
        var highContrast = new Windows.UI.ViewManagement.AccessibilitySettings().HighContrast;
        var useMaterial = translucent && effects && !highContrast && DesktopAcrylicController.IsSupported();
        var surface = useMaterial ? "WorkspaceSidebarSurfaceBrush" : "ApplicationPageBackgroundThemeBrush";
        Root = (Grid)XamlReader.Load($"<Grid xmlns=\"http://schemas.microsoft.com/winfx/2006/xaml/presentation\" Padding=\"16\" RowSpacing=\"12\" Background=\"{{ThemeResource {surface}}}\" />");
        Root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        Root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        Grid.SetRow(Actions, 1);
        Root.Children.Add(Actions);
        Content = Root;
        if (useMaterial) SystemBackdrop = new WorkspaceBackdrop();

        var escape = new Microsoft.UI.Xaml.Input.KeyboardAccelerator { Key = Windows.System.VirtualKey.Escape };
        escape.Invoked += async (_, args) => { args.Handled = true; await RequestCloseAsync(); };
        Root.KeyboardAccelerators.Add(escape);
        Root.SizeChanged += (_, _) => LayoutActions();
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);
        if (owner is not null) SetOwner(owner, modal);
        var dpi = GetDpiForWindow(hwnd) / 96.0;
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        AppWindow.Resize(new SizeInt32((int)Math.Min(width * dpi, area.Width), (int)Math.Min(height * dpi, area.Height)));
        AppWindow.Closing += (sender, args) =>
        {
            if (_allowClose) return;
            args.Cancel = true;
            _ = RequestCloseAsync();
        };
        Closed += (_, _) => _completion.TrySetResult();
    }
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(nint hwnd);
    [System.Runtime.InteropServices.DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern nint SetWindowLongPtr(nint hwnd, int index, nint value);
    public void SetOwner(Window owner, bool modal = false)
    {
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);
        var ownerHandle = WinRT.Interop.WindowNative.GetWindowHandle(owner);
        var previous = SetWindowLongPtr(hwnd, -8 /* GWLP_HWNDPARENT */, ownerHandle);
        var error = System.Runtime.InteropServices.Marshal.GetLastPInvokeError();
        if (previous == 0 && error != 0)
            throw new System.ComponentModel.Win32Exception(error, "Unable to set the editor window owner.");
        ((OverlappedPresenter)AppWindow.Presenter).IsModal = modal;
    }
    public void SetBody(UIElement body) => Root.Children.Insert(0, body);
    public Button AddAction(string label, Func<Task> action, bool manageEnabled = true)
    {
        var button = new Button { Content = label };
        button.Click += async (_, _) =>
        {
            if (manageEnabled) button.IsEnabled = false;
            try { await action(); }
            finally { if (manageEnabled) button.IsEnabled = true; }
        };
        Actions.Children.Add(button);
        button.RegisterPropertyChangedCallback(UIElement.VisibilityProperty, (_, _) => LayoutActions());
        LayoutActions();
        return button;
    }
    private void LayoutActions()
    {
        var actions = Actions.Children.OfType<FrameworkElement>().Where(action => action.Visibility == Visibility.Visible).ToArray();
        var columns = Math.Max(1, Math.Min(actions.Length, (int)(Root.ActualWidth / 160)));
        Actions.HorizontalAlignment = ActionsRightAligned ? HorizontalAlignment.Right : HorizontalAlignment.Stretch;
        Actions.ColumnDefinitions.Clear(); Actions.RowDefinitions.Clear();
        for (var i = 0; i < columns; i++) Actions.ColumnDefinitions.Add(new ColumnDefinition
            { Width = ActionsRightAligned ? GridLength.Auto : new GridLength(1, GridUnitType.Star) });
        for (var i = 0; i < actions.Length; i++)
        {
            if (i % columns == 0) Actions.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            Grid.SetRow(actions[i], i / columns); Grid.SetColumn(actions[i], i % columns);
        }
    }
    public async Task ShowAsync() { Activate(); await _completion.Task; }
    public void CommitFocusedInput() => (InputCommitTarget ?? Actions.Children.OfType<Button>().FirstOrDefault())?.Focus(FocusState.Programmatic);
    public async Task<bool> RequestCloseAsync()
    {
        if (_allowClose) return true;
        if (_checkingClose) return false;
        _checkingClose = true;
        try
        {
            if (CanCloseAsync is not null && !await CanCloseAsync()) return false;
            ForceClose();
            return true;
        }
        finally { _checkingClose = false; }
    }
    public void ForceClose() { _allowClose = true; Close(); }
}

// 在列布局与标签页间移动同一批控件，不丢弃草稿。
internal sealed class ResponsiveEditorPanels : Grid
{
    private readonly (string title, FrameworkElement body)[] _panels;
    private readonly TabView _tabs = new() { IsAddTabButtonVisible = false };
    private bool? _wide;
    public ResponsiveEditorPanels(params (string title, FrameworkElement body)[] panels)
    {
        _panels = panels;
        ColumnSpacing = 12;
        SizeChanged += (_, _) => ArrangePanels();
        Loaded += (_, _) => ArrangePanels();
    }
    private void ArrangePanels()
    {
        var wide = ActualWidth >= 1000;
        if (_wide == wide) return;
        _wide = wide;
        foreach (TabViewItem tab in _tabs.TabItems) tab.Content = null;
        _tabs.TabItems.Clear();
        Children.Clear(); ColumnDefinitions.Clear();
        if (wide)
        {
            for (var i = 0; i < _panels.Length; i++)
            {
                ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(i == 0 ? 0.65 : 1, GridUnitType.Star) });
                Grid.SetColumn(_panels[i].body, i);
                Children.Add(_panels[i].body);
            }
        }
        else
        {
            foreach (var (title, body) in _panels)
            {
                Grid.SetColumn(body, 0);
                _tabs.TabItems.Add(new TabViewItem { Header = title, Content = body, IsClosable = false });
            }
            Children.Add(_tabs);
        }
    }
}
