using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Animation;
using Windows.UI.ViewManagement;

namespace CSLModernMapWinUI;

public sealed partial class DraftWelcomeDialog : ContentDialog
{
    private readonly Storyboard _animation = new() { AutoReverse = true, RepeatBehavior = RepeatBehavior.Forever };
    private readonly UISettings _settings = new();
    private int _manualLevel;
    private bool _paused;

    public DraftWelcomeDialog()
    {
        InitializeComponent();
        Title = Text("Title");
        Intro.Text = Text("Intro");
        ExploreTitle.Text = Text("ExploreTitle");
        ExploreBody.Text = Text("ExploreBody");
        DraftTitle.Text = Text("DraftTitle");
        DraftBody.Text = Text("DraftBody");
        PrimaryButtonText = Text("Enter");
        CloseButtonText = Text("Continue");
        ExploreMap.ConfigureZoom(true, 0, _animation);
        DraftMap.ConfigureZoom(false, 0, _animation);
        Opened += (_, _) =>
        {
            (GetTemplateChild("CloseButton") as Button)?.Focus(FocusState.Programmatic);
            _settings.AnimationsEnabledChanged += MotionChanged;
            ResetPlayback();
        };
        Closed += (_, _) => StopPlayback();
        Unloaded += (_, _) => StopPlayback();
    }

    private static string Text(string key) => UiLanguage.Text("DraftWelcome_" + key);

    private void MotionChanged(UISettings sender, object args) =>
        DispatcherQueue.TryEnqueue(() => { if (IsLoaded) ResetPlayback(); });

    private void StopPlayback()
    {
        _settings.AnimationsEnabledChanged -= MotionChanged;
        _animation.Stop();
    }

    private void ResetPlayback()
    {
        _animation.Stop();
        _manualLevel = 0;
        _paused = false;
        if (_settings.AnimationsEnabled)
        {
            _animation.Begin();
            Playback.Content = Text("Pause");
        }
        else
        {
            ExploreMap.ConfigureZoom(true, 0);
            DraftMap.ConfigureZoom(false, 0);
            Playback.Content = Text("NextLevel");
        }
    }

    private void SamplesSizeChanged(object sender, SizeChangedEventArgs args)
    {
        var narrow = args.NewSize.Width < 410;
        Samples.ColumnSpacing = narrow ? 0 : 16;
        Samples.RowSpacing = narrow ? 12 : 0;
        SecondColumn.Width = narrow ? new GridLength(0) : new GridLength(1, GridUnitType.Star);
        Grid.SetRow(DraftSample, narrow ? 1 : 0);
        Grid.SetColumn(DraftSample, narrow ? 0 : 1);
    }

    private void PlaybackClicked(object sender, RoutedEventArgs args)
    {
        if (!_settings.AnimationsEnabled)
        {
            _manualLevel = (_manualLevel + 1) % (DraftWelcomeMap.LastLevel + 1);
            ExploreMap.ConfigureZoom(true, _manualLevel);
            DraftMap.ConfigureZoom(false, _manualLevel);
            Playback.Content = Text("NextLevel");
        }
        else if (_paused)
        {
            _animation.Resume();
            _paused = false;
            Playback.Content = Text("Pause");
        }
        else
        {
            _animation.Pause();
            _paused = true;
            Playback.Content = Text("Resume");
        }
    }
}
