"""图层基类：统一各图层的渲染接口。

所有图层渲染器继承 Layer，实现 render() 方法。
renderer.py 按注册顺序调用。

接口契约（CMM 直读）：
- supports(document: MapDocument) —— 图层是否适用于该文档；
- prepare(session: DocumentSession, job: RenderJob) —— 整次渲染预处理；
- render(ctx: RenderContext, document: MapDocument) —— 绘制；
- finalize_output(image, document, session=None) —— 成品后处理（可选）。
图层只消费 MapDocument 与 DocumentSession 派生服务，不再接收旧模型。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..style import Style
from ..detail import DetailMode, DetailPolicy, resolve_detail_policy

# 坐标变换函数：游戏坐标 (x, z) -> 像素坐标 (px, py)
Transform = Callable[[float, float], Any]


def abstract_level(zoom: float) -> int:
    """按放大倍数返回抽象级别（用于小图导出逐级简化）。

    0 = 全细节（zoom>=1，正常/特写渲染）
    1 = 轻度（zoom>=0.5，如 2048 全图）：丢弃步行道/美化道路
    2 = 中度（zoom>=0.25，如 1024 全图）：只留宽道路/大面积建筑，只保留区划名
    3 = 强抽象（zoom<0.25，如 512 全图）：只留主干道路，跳过建筑
    低分辨率下保留主体结构、剔除噪声，保证缩小后依然清晰可读。
    """
    return resolve_detail_policy(DetailMode.AUTO, zoom).abstract_level


@dataclass
class RenderContext:
    """渲染上下文：画布、变换、样式。"""

    image: Any            # PIL 图像对象。
    draw: Any             # PIL 图像绘制对象。
    transform: Transform  # 坐标变换函数
    style: Style          # 样式配置
    # 画布覆盖的世界坐标范围（游戏单位）。全图渲染为整个 17280×17280 世界，
    # 区域渲染为选中区域。供图层只采样可见部分（如地形层避免内存爆炸）。
    world_x0: float = -8640.0
    world_z0: float = -8640.0
    world_x1: float = 8640.0
    world_z1: float = 8640.0
    # 放大倍数：当前渲染的像素密度相对"全图 4096 基准密度"的比值。
    # 全图渲染时 = 边长/4096；区域渲染时 = (密度)/(4096/17280)，
    # 使线宽/字号/图标等比放大（1 表示与 4096 全图设计一致）。
    zoom: float = 1.0
    # 最终输出尺寸对应的放大倍数（抗锯齿渲染时 < zoom）。
    # 文字字号与抽象分级按最终尺寸计算，避免 2× 渲染下采样后文字变小变糊。
    final_zoom: float = 1.0
    # 地图内容密度与最终输出像素解耦。auto 保留历史 final_zoom 推导规则；
    # 手动档在整图、AA 和分块上下文中保持相同策略。
    detail_mode: DetailMode = DetailMode.AUTO
    detail_policy: DetailPolicy | None = None
    # 输出旋转角度（顺时针度数，0/90/180/270）。文字/图标等图形图层按此
    # 角度重新渲染，保证旋转后清晰且朝向正确（而非对成品图后处理旋转）。
    rotation: int = 0
    # 图层内部进度回调 (frac 0~1, 阶段说明文字)。由 renderer 注入并映射到
    # 整体进度区间；图层内长任务（等值线/平滑/逐多边形填充）可细分上报，
    # 让进度条平滑推进（低配专项#5/#7）。
    progress: Optional[Callable[[float, str], None]] = None
    # 分块渲染时，示意图等需要全局布局的图层使用整图尺寸和 tile 偏移。
    canvas_width: int = 0
    canvas_height: int = 0
    tile_offset_x: int = 0
    tile_offset_y: int = 0
    # 一次 Renderer.render 调用内共享的预处理资源。分块上下文必须持有同一
    # dict，避免道路求交等全图工作在每个瓦片重复执行。
    shared: dict = field(default_factory=dict)
    # 当前文档会话（DocumentSession）：图层经它读取跨作业缓存的派生服务
    # （道路场景/水域几何/距离场/山影等）。
    session: Any = None
    # 现代作业装配入口可显式提供 RenderJob；None 用于迁移期间兼容旧版直接调用。
    render_job: Any = None

    def __post_init__(self) -> None:
        if self.detail_policy is None:
            self.detail_policy = resolve_detail_policy(
                self.detail_mode, self.final_zoom)

    def report(self, frac: float, msg: str) -> None:
        """图层内部进度上报（frac 0~1，msg 为阶段说明）。"""
        if self.progress is not None:
            self.progress(frac, msg)


class Layer:
    """图层基类。

    子类需实现 render(ctx, document) 方法（document 为 MapDocument）。
    可选实现 supports(document) 判断本图层是否适用（默认 True）。
    late = True 的图层（文字等）在抗锯齿渲染时于最终尺寸画布上绘制，
    避免 2× 下采样导致文字变小变糊。
    """

    name = "layer"
    late = False
    # full_canvas = True 的图层是"整图连续场"（地形/水面等）：需要全图一次性
    # 渲染才能无缝（采样对齐/等值线闭合/层级正确）。分块渲染时它们先整图渲染
    # 成底图，瓦片图层（道路/建筑等矢量层）再逐瓦片绘制拼合。
    full_canvas = False

    def prepare(self, session, job) -> None:
        """可选的整次渲染预处理钩子；默认无需准备。"""
        return None

    def render(self, ctx: RenderContext, document) -> None:
        raise NotImplementedError

    def supports(self, document) -> bool:
        return True
