// 记录地图任务的界面状态。
namespace CSLModernMapWinUI;

internal enum MapTaskPhase { Empty, Loading, Generating, Ready, Canceled, Error }

internal sealed class MapTaskState
{
    internal int Generation { get; private set; }
    internal MapTaskPhase Phase { get; private set; } = MapTaskPhase.Empty;
    internal bool HasMap { get; private set; }
    internal bool CanCancel { get; private set; }
    internal bool Background { get; private set; }
    internal string Title { get; private set; } = UiLanguage.Text("MapTaskState_Title_3BFFE14F48");
    internal string Detail { get; private set; } = "";
    internal string? RequestId { get; private set; }
    internal double? Percent { get; private set; }
    internal bool Active => Phase is MapTaskPhase.Loading or MapTaskPhase.Generating;
    internal bool ShowOverlay => Active && !Background || !HasMap && Phase != MapTaskPhase.Error;
    internal string Status => Phase switch
    {
        MapTaskPhase.Loading => UiLanguage.Text("MapTaskState_Status_D04FCBDA73"),
        MapTaskPhase.Generating => UiLanguage.Text("MapTaskState_Status_969EF41F7E"),
        MapTaskPhase.Canceled => UiLanguage.Text("MapTaskState_Status_A37778F17C"),
        MapTaskPhase.Error => UiLanguage.Text("MapTaskState_Status_01AD2BC5D2"),
        _ => UiLanguage.Text("MapTaskState_Status_B073DB9C0B"),
    };

    internal int Begin(MapTaskPhase phase, string title, bool canCancel, string? requestId = null, bool background = false)
    {
        if (phase is not (MapTaskPhase.Loading or MapTaskPhase.Generating)) throw new ArgumentException("Not a map task", nameof(phase));
        Generation++;
        Phase = phase;
        Background = background && HasMap && phase == MapTaskPhase.Generating;
        Title = title;
        Detail = "";
        Percent = null;
        CanCancel = canCancel;
        RequestId = requestId;
        return Generation;
    }

    internal bool Progress(double? percent, string message, string? requestId = null)
    {
        if (!Active || requestId is not null && requestId != RequestId) return false;
        Percent = percent is { } value && double.IsFinite(value) ? Math.Clamp(value, 0, 100) : null;
        Detail = message;
        return true;
    }

    internal void PresentBase(string title)
    {
        if (!Active) return;
        HasMap = true;
        Phase = MapTaskPhase.Generating;
        Background = true;
        Title = title;
    }

    internal bool Complete(int generation)
    {
        if (!Active || generation != Generation) return false;
        HasMap = true;
        Phase = MapTaskPhase.Ready;
        ClearTask();
        Percent = 100;
        Title = "";
        return true;
    }

    internal bool Fail(string message, int generation)
    {
        if (generation != Generation) return false;
        Phase = MapTaskPhase.Error;
        ClearTask();
        Title = message;
        return true;
    }

    internal bool Cancel(int generation)
    {
        if (!Active || generation != Generation) return false;
        Phase = MapTaskPhase.Canceled;
        ClearTask();
        Title = HasMap ? "" : UiLanguage.Text("MapTaskState_Cancel_F23F3D1996");
        return true;
    }

    internal void Reset(bool hasMap)
    {
        Generation++;
        HasMap = hasMap;
        Phase = hasMap ? MapTaskPhase.Ready : MapTaskPhase.Empty;
        Title = hasMap ? "" : UiLanguage.Text("MapTaskState_Reset_3BFFE14F48");
        ClearTask();
    }

    private void ClearTask()
    {
        Background = false;
        Detail = "";
        Percent = null;
        CanCancel = false;
        RequestId = null;
    }
}
