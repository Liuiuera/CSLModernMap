using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Animation;

namespace CSLModernMapWinUI;

public sealed partial class DraftWelcomeMap : UserControl
{
    internal const int LastLevel = 3;
    private readonly (Canvas Marker, int FirstLevel)[] _places;

    public DraftWelcomeMap()
    {
        InitializeComponent();
        StationName.Text = UiLanguage.Text("DraftWelcome_Station");
        ParkName.Text = UiLanguage.Text("DraftWelcome_Park");
        ShopName.Text = UiLanguage.Text("DraftWelcome_Shop");
        PierName.Text = UiLanguage.Text("DraftWelcome_Pier");
        MarketName.Text = UiLanguage.Text("DraftWelcome_Market");
        CafeName.Text = UiLanguage.Text("DraftWelcome_Cafe");
        ClinicName.Text = UiLanguage.Text("DraftWelcome_Clinic");
        _places = [(Station, 0), (Park, 1), (Pier, 1), (Shop, 2), (Market, 2), (Cafe, 3), (Clinic, 3)];
        foreach (var (marker, _) in _places) marker.RenderTransform = new ScaleTransform();
    }

    internal void ConfigureZoom(bool explore, int level, Storyboard? animation = null)
    {
        static double Zoom(int step) => 1 + 0.4 * step;
        void Track(DependencyObject target, DependencyProperty property, string name, Func<int, double> value)
        {
            target.SetValue(property, value(level));
            if (animation is null) return;
            var track = new DoubleAnimationUsingKeyFrames();
            void Frame(int ms, double at) => track.KeyFrames.Add(new EasingDoubleKeyFrame {
                KeyTime = TimeSpan.FromMilliseconds(ms), Value = at, EasingFunction = new SineEase { EasingMode = EasingMode.EaseInOut }
            });
            Frame(0, value(0));
            // 自动反向会重复两个端点的停顿，因此端点各留半拍。
            int time = 350;
            Frame(time, value(0));
            for (int step = 1; step <= LastLevel; step++)
            {
                Frame(time += 1000, value(step));
                Frame(time += step == LastLevel ? 350 : 700, value(step));
            }
            Storyboard.SetTarget(track, target);
            Storyboard.SetTargetProperty(track, name);
            animation.Children.Add(track);
        }
        void Scale(ScaleTransform transform, Func<int, double> value)
        {
            Track(transform, ScaleTransform.ScaleXProperty, "ScaleX", value);
            Track(transform, ScaleTransform.ScaleYProperty, "ScaleY", value);
        }
        Scale(Camera, Zoom);
        if (!explore) return;
        foreach (var (marker, firstLevel) in _places)
        {
            Scale((ScaleTransform)marker.RenderTransform, step => (1 + 0.08 * step) / Zoom(step));
            if (firstLevel > 0) Track(marker, OpacityProperty, "Opacity", step => step >= firstLevel ? 1 : 0);
        }
    }
}
