using CSLModernMapWinUI;

var mapTask = new MapTaskState();
Assert(!mapTask.Active && mapTask.ShowOverlay && mapTask.Status == "就绪", "empty map presentation");
var loading = mapTask.Begin(MapTaskPhase.Loading, "加载城市", true);
Assert(mapTask.Active && mapTask.CanCancel && mapTask.Percent is null && mapTask.Status == "加载中", "loading presentation");
Assert(mapTask.Progress(150, "编译中") && mapTask.Percent == 100, "clamp progress");
Assert(mapTask.Progress(double.NaN, "绘制中") && mapTask.Percent is null, "unknown progress");
Assert(mapTask.Complete(loading) && mapTask.HasMap && !mapTask.ShowOverlay && !mapTask.CanCancel, "map becomes ready");
Assert(!mapTask.Progress(50, "late progress"), "ready map ignores late progress");
var roadUpdate = mapTask.Begin(MapTaskPhase.Generating, "道路交叉关系", false, background: true);
Assert(mapTask.Active && mapTask.Background && !mapTask.ShowOverlay, "road update preserves the visible map");
Assert(mapTask.Progress(40, "更新道路") && mapTask.Percent == 40, "background road progress");
Assert(!mapTask.Complete(loading) && mapTask.Background, "old completion cannot stop road update");
Assert(mapTask.Complete(roadUpdate) && !mapTask.Background && !mapTask.ShowOverlay, "road update finishes without overlay");
var newCity = mapTask.Begin(MapTaskPhase.Loading, "新城市", true, background: true);
Assert(!mapTask.Background && mapTask.ShowOverlay, "new city loading remains blocking");
mapTask.Cancel(newCity);
var firstRender = mapTask.Begin(MapTaskPhase.Generating, "地铁", true, "first");
var secondRender = mapTask.Begin(MapTaskPhase.Generating, "地铁", true, "second");
Assert(!mapTask.Progress(10, "old request", "first"), "ignore previous request progress");
Assert(!mapTask.Complete(firstRender) && !mapTask.Fail("old error", firstRender) && !mapTask.Cancel(firstRender), "ignore previous task completion/error/cancel");
Assert(mapTask.Progress(40, "布局", "second") && mapTask.Percent == 40, "accept active request progress");
Assert(mapTask.Fail("生成失败", secondRender) && mapTask.HasMap && !mapTask.ShowOverlay && !mapTask.CanCancel && mapTask.Percent is null, "failure preserves ready map and clears busy controls");
var retry = mapTask.Begin(MapTaskPhase.Generating, "重试", true);
Assert(mapTask.Cancel(retry) && !mapTask.ShowOverlay && mapTask.Status == "已取消", "cancel preserves ready map");
mapTask.Reset(false);
var firstLoad = mapTask.Begin(MapTaskPhase.Loading, "加载", true);
Assert(mapTask.Cancel(firstLoad) && mapTask.ShowOverlay && !mapTask.Active && mapTask.Percent is null, "cancel first load keeps empty overlay without progress");
var timedOut = mapTask.Begin(MapTaskPhase.Generating, "生成", true);
mapTask.Reset(true);
Assert(!mapTask.Fail("old timeout", timedOut) && !mapTask.ShowOverlay && mapTask.Status == "就绪", "ignore old timeout after context change");
mapTask.Reset(false);
var failureWithoutMap = mapTask.Begin(MapTaskPhase.Generating, "地铁", true);
Assert(mapTask.Fail("生成失败", failureWithoutMap) && !mapTask.Active && !mapTask.ShowOverlay, "first failure removes loading overlay");
var retryAfterFailure = mapTask.Begin(MapTaskPhase.Generating, "重试", true);
Assert(mapTask.Complete(retryAfterFailure) && mapTask.HasMap, "player retry can succeed");
Console.WriteLine("PASS map task states: loading, generating, ready, failure, cancel, unknown progress, stale request and timeout");

var opening = mapTask.Begin(MapTaskPhase.Loading, "加载城市", true);
mapTask.Progress(78, "正在补充细节");
mapTask.PresentBase("地图已可浏览");
Assert(mapTask.Active && mapTask.Background && mapTask.Percent == 78 && mapTask.Detail == "正在补充细节",
    "late base presentation preserves enrichment progress and detail");
mapTask.Progress(95, "正在显示地图");
Assert(mapTask.Active, "generation completion waits for final scene presentation");
Assert(mapTask.Complete(opening) && mapTask.Percent == 100, "final presentation records 100 percent");
mapTask.PresentBase("late base");
Assert(!mapTask.Active, "completion ignores late base");
foreach (var fail in new[] { false, true })
{
    var task = mapTask.Begin(MapTaskPhase.Generating, "刷新地图", true);
    mapTask.Progress(40, "生成中");
    if (fail) mapTask.Fail("失败", task); else mapTask.Cancel(task);
    mapTask.PresentBase("late base");
    Assert(!mapTask.Progress(90, "late progress") && !mapTask.Complete(task) && !mapTask.Active,
        "terminal task ignores late progress and presentation");
}
Console.WriteLine("PASS progressive map presentation lifecycle");


static void Assert(bool condition, string message)
{
    if (!condition) throw new Exception(message);
}
