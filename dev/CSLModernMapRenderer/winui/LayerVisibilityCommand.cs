// 将图层可见性操作封装为界面命令。
namespace CSLModernMapWinUI;

// 预览分组使用后端键；metro 是普通地图交通图层，transit 是独立公共交通视图。
internal sealed record LayerVisibilityCommand(string type, string layer, bool visible, string sceneId, int revision)
{
    internal static LayerVisibilityCommand Create(string layer, bool visible, string sceneId, int revision) =>
        new("setLayerVisibility", layer, visible, sceneId, revision);
}
