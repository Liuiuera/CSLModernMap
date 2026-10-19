"""渲染器：组合所有图层，按序渲染到画布上。

渲染顺序（自下而上）：
    地形底色 → 地形晕渲 → 水域 → 森林 → 区划 → 道路(低到高)
    → 铁路 → 交通 → 建筑 → 地铁 → 公交站 → 各类标注

每个图层是一个独立模块（layers/），本模块只负责编排。

对超大分辨率（> TILE_SIZE）默认启用"分块渲染 + 合并"机制，
将画布切成 N×N 瓦片各自独立渲染，再拼合回全图，
峰值内存从 W×H×3 降低到 (TILE_SIZE+2×BLEED)²×3 级别。
"""
from __future__ import annotations

import math
import copy
import os
import time
from typing import Any, Callable, List, Optional

import numpy as np
from PIL import Image, ImageDraw

from ._paths import writable_dir
from .i18n import tr
from .layers.base import RenderContext
from .progress import ProgressEvent
from .style import Style
from .system_info import total_memory_gb

# 进度回调：(总进度, 阶段说明, 可选的 (图层名, 图层内进度))。
# 第三个参数仅供支持双进度条的调用方使用，旧的两参数回调仍然有效。
ProgressCallback = Optional[Callable[..., None]]

# 分块渲染参数
# - TILE_SIZE：每块目标边长（不抗锯齿时实际像素；抗锯齿时 2× 该值后下采样到此值）
# - BLEED：每块向外扩展重叠像素数，防止跨块的道路/建筑在接缝处断裂
# - AUTO_THRESHOLD：超过该尺寸自动启用分块（小于等于时直接整体渲染，更快）
TILE_SIZE = 4096
TILE_BLEED = 64
AUTO_THRESHOLD = 6144  # <= 6K 图整体渲染，内存仍可控

# 整图渲染峰值内存安全上限（字节）。关闭内存友好时，若整图路径的
# 估算峰值超过该上限，仍会自动切分块兜底，防止最大参数把内存打爆。
SAFE_FULL_IMAGE_PEAK = 2 * 1024 ** 3  # 2GB
# 整图渲染每像素估算字节数：内部画布 RGB 3B + 地形 float32 高度 4B
# + 色表 uint8 3B + 各图层中间开销/副本，保守取值。
EST_BYTES_PER_PIXEL = 24

# 磁盘画布（渲染降内存）：合并画布落到磁盘映射（OS 分页管理），
# 峰值内存从整图 W×H×3 降到瓦片级。低于该像素数时用内存画布（无额外开销）。
DISK_BACK_MIN_PIXELS = 6144 * 6144

# 普通 4K 以上输出按空间分块并交由独立进程处理；主要几何遍历是 Python 代码，线程池会受 GIL 限制并阻塞 Tk 界面。
PARALLEL_MIN_PIXELS = 4096 * 4096
# 瓦片上限 16（4×4 分块，tile 最小 1024）：worker 数对齐最大瓦片数，
# 多核机器可把 16 块瓦片全部并行，不再人为限制在 6。
MAX_PARALLEL_WORKERS = 16

_TILE_WORKER_RENDERER = None
_TILE_WORKER_STATE = None
_TILE_WORKER_BASE_SHM = None
_TILE_WORKER_BASE_ARRAY = None


_LAYER_CN_NAMES = {
    "terrain": "地形", "water": "水面", "built_area": "建成区",
    "poi_area": "POI足迹",
    "road": "道路", "railway": "铁路", "traffic": "交通",
    "building": "建筑", "po": "PO魔物", "transport": "交通线路",
    "poi": "POI图标", "label": "文字", "watermark": "水印",
    "district": "区划", "zone": "功能区", "land_use": "用地类型",
}


def _cn(name: str) -> str:
    """进度消息的本地化图层名（未知图层仍退回原名）。"""
    key = f"gui.layer.{name}"
    text = tr(key)
    return _LAYER_CN_NAMES.get(name, name) if text == key else text


def _layer_progress_cb(
    layer_name: str, idx: int, total: int,
    progress_start: float, progress_total: float,
    progress: ProgressCallback,
):
    """把图层内部进度 (frac 0~1, 阶段说明) 映射到整体进度区间。"""

    def cb(frac: float, msg: str) -> None:
        p = progress_start + (
            (idx + min(max(frac, 0.0), 1.0)) / max(total, 1)
        ) * progress_total
        _report_progress(progress, min(p, 1.0), tr(
                         "gui.progress.layer_detail", layer=_cn(layer_name),
                         msg=msg),
                         (layer_name, min(max(frac, 0.0), 1.0)))

    return cb


def _report_progress(progress, pct: float, message: str, stage=None) -> None:
    """向接收端报告进度；支持时发送 ProgressEvent，否则兼容旧版回调协议。"""
    if progress is None:
        return
    if hasattr(progress, "_sink"):
        phase_frac = None
        if stage is not None:
            phase_frac = max(0.0, min(1.0, float(stage[1])))
        progress._sink(ProgressEvent(
            phase=getattr(progress, "phase", "render"),
            phase_fraction=phase_frac,
            overall_fraction=max(0.0, min(1.0, float(pct))),
            message=message,
        ))
        return
    if stage is not None and getattr(progress, "supports_stage_progress", False):
        progress(pct, message, stage)
    else:
        progress(pct, message)


def _hex_color(value: str) -> tuple:
    """#RRGGBB / #RGB → (r, g, b)；解析失败回退浅灰。"""
    c = (value or "").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except ValueError:
        return (246, 247, 249)


def _close_memmap(mm) -> None:
    """显式关闭 memmap 底层 mmap 并释放引用。

    numpy 的 memmap 不保证 GC 时自动关闭文件句柄（已实测：del+gc 后文件仍被
    占用），Windows 下会导致临时文件无法删除。这里直接关闭底层 mmap。
    """
    if mm is None:
        return
    try:
        base = getattr(mm, "base", None)
        if base is not None and hasattr(base, "close"):
            base.close()
    except Exception:  # noqa: BLE001
        pass


def _tile_worker_init(state: dict, base_shm_name: str | None,
                      base_shape: tuple | None) -> None:
    """初始化常驻分块工作进程，每个进程只接收一次地图、预处理几何和图层实例。"""
    global _TILE_WORKER_RENDERER, _TILE_WORKER_STATE
    global _TILE_WORKER_BASE_SHM, _TILE_WORKER_BASE_ARRAY

    if isinstance(state, tuple):
        # 父进程只序列化一次城市会话，子进程并行反序列化，避免每个工作进程重复遍历和管道传输。
        import pickle
        from multiprocessing.shared_memory import SharedMemory

        state_memory = SharedMemory(name=state[0])
        try:
            with state_memory.buf[:state[1]] as payload:
                state = pickle.loads(payload)
        finally:
            state_memory.close()

    renderer = Renderer(
        state["style"], width=state["width"], height=state["height"],
        rotation=state["rotation"], detail_mode=state["detail_mode"],
        render_job=state["render_job"],
    )
    renderer._layers = state["layers"]
    renderer._render_shared = state["shared"]
    renderer.transparent = state["transparent"]
    renderer._active_session = state.get("session")
    _TILE_WORKER_RENDERER = renderer
    _TILE_WORKER_STATE = state
    if base_shm_name and base_shape:
        from multiprocessing.shared_memory import SharedMemory

        _TILE_WORKER_BASE_SHM = SharedMemory(name=base_shm_name)
        _TILE_WORKER_BASE_ARRAY = np.ndarray(
            base_shape, dtype=np.uint8,
            buffer=_TILE_WORKER_BASE_SHM.buf,
        )


def _tile_worker_render(idx: int) -> tuple:
    """渲染一个分块并返回原始像素和轻量耗时信息。"""
    renderer = _TILE_WORKER_RENDERER
    state = _TILE_WORKER_STATE
    W, H = state["width"], state["height"]
    tile_size = state["tile_size"]
    cols = math.ceil(W / tile_size)
    ci, ri = idx % cols, idx // cols
    px0, py0 = ci * tile_size, ri * tile_size
    px1, py1 = min(px0 + tile_size, W), min(py0 + tile_size, H)
    tw, th = px1 - px0, py1 - py0
    scale_aa = state["scale_aa"]
    ipx0, ipy0 = px0 * scale_aa, py0 * scale_aa
    ipx1, ipy1 = px1 * scale_aa, py1 * scale_aa
    bleed_scaled = state["bleed"] * scale_aa
    bpx0 = max(0, ipx0 - bleed_scaled)
    bpy0 = max(0, ipy0 - bleed_scaled)
    bpx1 = min(state["internal_w"], ipx1 + bleed_scaled)
    bpy1 = min(state["internal_h"], ipy1 + bleed_scaled)
    btw, bth = bpx1 - bpx0, bpy1 - bpy0

    full_transform = _build_transform(
        state["internal_w"], state["internal_h"],
        state["region"], rotation=state["rotation"],
    )
    tile_transform = _build_tile_transform(
        full_transform, full_w=state["internal_w"],
        full_h=state["internal_h"], offset_x=bpx0, offset_y=bpy0,
        tile_w=btw, tile_h=bth,
    )
    base_patch = None
    if _TILE_WORKER_BASE_ARRAY is not None:
        # 只从共享内存复制当前分块的底图像素，随后工作进程可安全地用 PIL 绘制。
        aa = scale_aa
        fx0, fy0 = bpx0 // aa, bpy0 // aa
        fx1 = min((bpx1 + aa - 1) // aa, W)
        fy1 = min((bpy1 + aa - 1) // aa, H)
        patch_array = np.array(
            _TILE_WORKER_BASE_ARRAY[fy0:fy1, fx0:fx1], copy=True,
        )
        if aa > 1:
            patch_array = np.repeat(
                np.repeat(patch_array, aa, axis=0), aa, axis=1,
            )
            patch_array = patch_array[:bth, :btw]
        base_patch = Image.fromarray(patch_array, "RGB")

    renderer.last_stats = {}
    started = time.perf_counter()
    tile_img = renderer._render_early_onto(
        document=state["document"], img_w=btw, img_h=bth,
        region=state["region"], final_size=state["final_size"],
        layers=state["layers"], progress=None, progress_start=0.0,
        progress_total=1.0, stats_prefix=f"tile{idx}_",
        aa_downsample=scale_aa, preset_transform=tile_transform,
        preset_background=base_patch,
    )
    left = (ipx0 - bpx0) // scale_aa
    top = (ipy0 - bpy0) // scale_aa
    crop = tile_img.crop((left, top, left + tw, top + th))
    if crop.size != (tw, th):
        crop = crop.resize((tw, th), Image.LANCZOS)
    return (
        idx, px0, py0, px1, py1, crop.mode, crop.size, crop.tobytes(),
        time.perf_counter() - started, dict(renderer.last_stats),
    )


class Renderer:
    """渲染管线。"""

    def __init__(self, style: Style, width: int = 4096, height: int = 4096,
                 rotation: int = 0, detail_mode="auto", render_job=None):
        from .detail import normalize_detail_mode

        self.style = style
        self.width = width
        self.height = height
        # 注册的图层渲染器（按顺序）
        self._layers: List = []
        # 最近一次渲染的各阶段耗时统计（秒）
        self.last_stats: dict = {}
        # 分块瓦片大小（像素，0 = 自动）。GUI/CLI 可显式传入。
        self.tile_size: int = 0
        self.tile_bleed: int = TILE_BLEED
        # 0 = 按硬件自动；1 = 强制串行；>1 = 指定进程数。库级默认保持
        # 串行，避免嵌入方未使用 __main__ guard 时在 Windows 意外递归 spawn；
        # GUI/CLI 正式入口会显式启用自动并行。
        self.parallel_workers: int = 1
        # 内存友好：默认关闭（关闭时即便尺寸很大也不分块，行为与旧版一致）
        # 开启后：超大图自动分块，或按显式 tile_size 分块
        self.memory_friendly: bool = False
        # 磁盘画布（渲染降内存）：开启后超大图合并画布落到磁盘映射，
        # 峰值内存从整图 W×H×3 降到瓦片级；输出有少量磁盘 IO。
        self.disk_backing: bool = False
        self._disk_mm = None      # 当前磁盘画布的 np.memmap
        self._disk_path = None    # 当前磁盘画布临时文件路径
        # 输出旋转角度（顺时针度数，0/90/180/270）：在坐标变换层生效，
        # 所有图层（地形/道路/建筑/文字/图标）都在旋转后的坐标系里重新渲染，
        # 而非对成品图做 PIL 后处理旋转（那会让文字发虚、图标朝向错位）。
        from .export_region import output_rotation
        self.rotation = output_rotation(rotation)
        self.detail_mode = normalize_detail_mode(detail_mode)
        # 透明画布（公共交通视图等叠加场景）：画布以 RGBA 透明为底，
        # 渲染结果可直接 alpha_composite 到成品图上。与磁盘画布/底图模式互斥
        # （透明时自动跳过 disk_backing，无 full_canvas 图层时 use_base=False）。
        self.transparent: bool = False
        self._render_shared: dict = {}
        # 文档会话缓存：同一文档的多次渲染（全图/特写/瓦片）共享派生服务。
        self._sessions: dict = {}
        self._active_session = None
        self.cancel_check = lambda: None
        # 现代装配入口可提供标准 CMM RenderJob；None 兼容旧版直接调用。
        self.render_job = render_job
        # 界面渐进预览不得跨进程传整张画布，也不能让预览失败中断正式渲染。
        self.preview_callback = None
        self._preview_last_at = None

    def _publish_preview(self, image, phase: str) -> None:
        callback = self.preview_callback
        if callback is None:
            return
        now = time.monotonic()
        if self._preview_last_at is not None and now - self._preview_last_at < 0.4:
            return
        self._preview_last_at = now
        try:
            # 磁盘画布通过 NumPy 写入；生成预览时只采样当前后备存储，不物化整张高分辨率图。
            mm = getattr(image, "_cslm_disk_mm", None)
            if mm is not None:
                step = max(1, math.ceil(max(image.size) / 1024))
                image = Image.fromarray(np.asarray(mm[::step, ::step]).copy())
            scale = min(1.0, 1024.0 / max(image.size))
            size = tuple(max(1, round(value * scale)) for value in image.size)
            preview = image.resize(size, Image.Resampling.BILINEAR,
                                   reducing_gap=3.0)
            callback(preview, phase)
        except Exception:
            pass

    def add_layer(self, layer) -> None:
        """注册图层渲染器（layers/ 下的实例）。"""
        self._layers.append(layer)

    def _resolve_tile(self) -> int:
        """确定实际瓦片边长：显式指定优先；否则按输出尺寸与可用内存自适应。

        关键约束（低配专项实测教训）：瓦片必须足够小——大瓦片会让单块内部
        画布（tile×AA）与各图层中间 buffer 再次变大，分块失去意义。
        上限 = min(4096, 输出边长//4)，保证至少 4×4 分块；内存 < 8GB 再压到 2K。
        """
        if self.tile_size > 0:
            return self.tile_size
        # 瓦片上限 = min(4096, 输出边长//4)：16K→4K、32K→4K；
        # 实测 2K 瓦片峰值几乎无下降但耗时大幅增加（图层固定开销每块重复），
        # 4K 是速度/内存的平衡点（单块内部画布 tile×AA ≈ 200MB 级）
        cap = max(1024, min(4096, max(self.width, self.height) // 4))
        mem_gb = total_memory_gb()
        if mem_gb is None or mem_gb >= 16:
            return cap
        if mem_gb >= 8:
            return min(cap, 4096)
        return min(cap, 2048)

    def _resolve_parallel_workers(self, n_tiles: int | None = None) -> int:
        """按 CPU/内存选择保守的渲染进程数，并为 GUI 留出一个核心。"""
        if self.parallel_workers == 1:
            return 1
        if self.parallel_workers == 0 and self.tile_size == 0 and max(self.width, self.height) <= 4096:
            return 1
        if self.width * self.height < PARALLEL_MIN_PIXELS:
            return 1
        if self.parallel_workers > 1:
            wanted = self.parallel_workers
        else:
            cores = max(os.cpu_count() or 2, 2)
            mem_gb = total_memory_gb()
            # 内存档位放宽：低配保守、中配 8、高配直上 16（对齐最大瓦片数）。
            # 渲染器独立分发、玩家机器普遍 ≥16GB，无需再按 2/4/6 保守收敛。
            memory_cap = (
                4 if mem_gb is not None and mem_gb < 8 else
                8 if mem_gb is not None and mem_gb < 16 else
                MAX_PARALLEL_WORKERS
            )
            wanted = min(max(cores - 1, 1), memory_cap)
        wanted = min(max(int(wanted), 1), MAX_PARALLEL_WORKERS)
        if n_tiles is not None:
            wanted = min(wanted, max(int(n_tiles), 1))
        return wanted

    # ------------------------------------------------------------------
    # 磁盘画布（渲染降内存）：合并画布落盘，瓦片经 numpy 直写映射
    # ------------------------------------------------------------------
    def _new_disk_canvas(self, W: int, H: int, background):
        """创建磁盘映射画布，返回 (PIL Image, np.memmap, 临时路径)。

        PIL 图像与 memmap 共享读取（Image.frombuffer + memoryview），
        瓦片写入走 numpy 切片直接落到磁盘映射，由 OS 分页管理——
        大图合并阶段内存峰值 ≈ 瓦片级，而非整图 W×H×3。
        """
        import tempfile as _tf

        try:
            render_dir = writable_dir() / "cache" / "render"
            render_dir.mkdir(parents=True, exist_ok=True)
            fd, path = _tf.mkstemp(
                prefix="canvas_", suffix=".bin", dir=str(render_dir))
        except OSError:
            # 缓存目录不可写 → 回退系统临时目录
            fd, path = _tf.mkstemp(prefix="cslm_canvas_", suffix=".bin")
        os.close(fd)
        mm = np.memmap(path, dtype=np.uint8, mode="w+", shape=(H, W, 3))
        bg = (_hex_color(background) if isinstance(background, str)
              else tuple(background[:3]))
        mm[...] = bg
        img = Image.frombuffer(
            "RGB", (W, H), memoryview(mm), "raw", "RGB", 0, 1)
        # 标记盘背属性（供渲染管线物化 / 调用方清理识别）
        img._cslm_disk = True
        img._cslm_disk_mm = mm
        self._disk_mm = mm
        self._disk_path = path
        return img, mm, path

    def _drop_disk_canvas(self) -> None:
        """释放磁盘画布映射并删除临时文件（物化到内存后调用）。"""
        mm, path = self._disk_mm, self._disk_path
        self._disk_mm = None
        self._disk_path = None
        if mm is not None:
            _close_memmap(mm)
            del mm
        import gc

        gc.collect()
        if path:
            try:
                os.remove(path)
            except OSError:
                pass

    def render(
        self,
        document: Any,
        progress: ProgressCallback = None,
        region: tuple | None = None,
        final_size: int | None = None,
        aa_downsample: int = 1,
        session: Any = None,
    ) -> Image.Image:
        """执行渲染；只接受 :class:`MapDocument`，不再自动转换旧模型。

        ``session``：可选的 DocumentSession；未提供时按文档创建并复用
        （同一文档多次渲染共享派生缓存）。
        """
        from .cmm.model import MapDocument
        if not isinstance(document, MapDocument):
            raise TypeError(
                "Renderer.render() requires a MapDocument; "
                f"got {type(document).__name__}. Legacy MapData is no longer "
                "accepted — load via csmodernmap.cmm.load_document()."
            )
        from .cmm.session import DocumentSession
        if session is None or session.document is not document:
            session = self._sessions.get(id(document))
            if session is None or session.document is not document:
                session = DocumentSession(document=document)
                self._sessions = {id(document): session}
        self._active_session = session
        self._preview_last_at = None
        return self._render_impl(
            document=document,
            progress=progress,
            region=region,
            final_size=final_size,
            aa_downsample=aa_downsample,
        )

    def _render_impl(
        self,
        document,
        progress: ProgressCallback = None,
        region: tuple | None = None,
        final_size: int | None = None,
        aa_downsample: int = 1,
    ) -> Image.Image:
        """执行渲染，返回 PIL Image。

        region 为 None 时渲染整个 17280×17280 世界；
        否则渲染游戏坐标区域 (x0, z0, x1, z1)（自动扩展为正方形，保持无畸变）。
        final_size：传统"全图 AA 后下采样"时的最终边长；
                   若使用 aa_downsample 参数，建议令 final_size == None。
        aa_downsample：超采样倍率（1 = 不超采样，2 = 2×AA，推荐）。
                       会让内部每块按 aa_downsample× 尺寸渲染再下采样，
                       在分块渲染下峰值内存仅 (tile×aa_downsample)²。

        分块规则（必须两个条件之一才会真正分块）：
          1. 显式指定了 tile_size (> 0)
          2. self.memory_friendly=True 且尺寸 > AUTO_THRESHOLD（瓦片按可用内存自适应）
        默认 memory_friendly=False：始终整图渲染，简单稳定。
        """
        tile = self._resolve_tile()
        # 分块条件：显式 tile_size OR（（内存友好 或 磁盘画布）AND 超大图）
        use_tiled = self.tile_size > 0 or (
            self._resolve_parallel_workers() > 1
        ) or (
            (self.memory_friendly or self.disk_backing)
            and max(self.width, self.height) > AUTO_THRESHOLD
        )

        # 规范化：如果 aa_downsample > 1 且未传 final_size，
        # 则把自身宽高视为"目标输出尺寸"，内部按 self.width/height × aa 渲染。
        internal_w = self.width
        internal_h = self.height
        if aa_downsample and aa_downsample > 1 and final_size is None:
            internal_w = self.width * aa_downsample
            internal_h = self.height * aa_downsample

        # 后期图层（late=True）：如果有 AA（final_size != width
        # 或 aa_downsample > 1），最终在下采样后的尺寸画布上绘制。
        late = [l for l in self._layers if getattr(l, "late", False)]
        early = [l for l in self._layers if not getattr(l, "late", False)]

        # 全图预处理只执行一次；后续整图/瓦片/late 上下文共享结果。
        t_total = time.time()
        self.last_stats = {"disk_backing_active": False}
        prepare_started = time.perf_counter()
        self._render_shared = {}
        session = self._active_session
        if session is not None:
            # prepare 钩子需要样式参数：经会话临时字段传递（见 Layer.prepare）。
            session._prepare_style = self.style
            from .detail import resolve_detail_policy

            prepare_zoom = _build_transform(
                self.width, self.height, region, rotation=self.rotation).zoom
            if final_size:
                prepare_zoom *= final_size / self.width
            session._prepare_detail_policy = resolve_detail_policy(self.detail_mode, prepare_zoom)
        for layer in self._layers:
            self.cancel_check()
            if layer.supports(document):
                prepare = getattr(layer, "prepare", None)
                if callable(prepare):
                    prepare(session, self.render_job)

        self.last_stats["prepare"] = time.perf_counter() - prepare_started

        # 内存兜底：即使未开内存友好，若整图渲染的估算峰值超过安全上限，
        # 也自动切换分块渲染（结果逐像素一致，仅内存更省），并标记统计。
        if not use_tiled:
            aa = aa_downsample if aa_downsample and aa_downsample > 1 else 1
            est = (self.width * aa) * (self.height * aa) * EST_BYTES_PER_PIXEL
            if est > SAFE_FULL_IMAGE_PEAK:
                use_tiled = True
                self.last_stats["auto_tiled_fallback"] = True

        # 两种 AA 路径同时存在时，优先走 aa_downsample 内部路径
        using_aa = aa_downsample and aa_downsample > 1
        if not use_tiled:
            # 常规路径：一张整图渲染全部 early 图层
            image = self._render_early_onto(
                document=document,
                img_w=internal_w,
                img_h=internal_h,
                region=region,
                final_size=final_size,
                layers=early,
                progress=progress,
                progress_start=0.0,
                progress_total=len(early) / max(len(self._layers), 1),
                stats_prefix="",
                aa_downsample=aa_downsample if using_aa else 1,
            )
            if using_aa:
                image = (image.reduce(2) if image.size == (self.width * 2, self.height * 2)
                         else image.resize((self.width, self.height), Image.LANCZOS))
        else:
            image = self._render_tiled(
                document=document,
                layers=early,
                progress=progress,
                region=region,
                final_size=final_size,
                tile_size=tile,
                bleed=self.tile_bleed,
                aa_downsample=aa_downsample if using_aa else 1,
                internal_w=internal_w,
                internal_h=internal_h,
            )

        # 后期图层（文字/POI图标/铁路标注等）在合并后的结果上绘制
        result = self._render_late(
            image=image,
            document=document,
            region=region,
            final_size=final_size,
            layers=late,
            progress=progress,
            total_layers=len(self._layers),
            done_layers=len(early),
        )
        # 成品后处理钩子：在所有图层完成后，允许独立版式组件改变最终画布尺寸。
        # 普通图层没有该方法，因此不改变既有渲染路径。
        for layer in self._layers:
            finalize = getattr(layer, "finalize_output", None)
            if callable(finalize):
                result = finalize(result, document, session)
        self.last_stats["total"] = time.time() - t_total
        return result

    # ------------------------------------------------------------------
    # 单张画布渲染 early 图层（可用于整图或单瓦片）
    # ------------------------------------------------------------------
    def _render_early_onto(
        self,
        *,
        document,
        img_w: int,
        img_h: int,
        region: tuple | None,
        final_size: int | None,
        layers: list,
        progress: ProgressCallback,
        progress_start: float,
        progress_total: float,
        stats_prefix: str,
        aa_downsample: int = 1,
        preset_transform=None,
        preset_background=None,  # 底图模式：瓦片画布的预填底色（final 尺寸底图区域）
    ) -> Image.Image:
        """在一张 (img_w, img_h) 画布上渲染所有 early 图层并返回。

        aa_downsample: 1 = 原图返回；>1 = 在完成绘制后整体下采样 aa_downsample 倍，
                       返回 (img_w // aa_downsample, img_h // aa_downsample) 的图像。
                       分块渲染时每块完成自己的 AA 下采样，再合并。
        preset_transform: 可选，一个预先构造好的 world→pixel 变换（含 scale/region/zoom 属性）。
                          传 None 时按 (img_w, img_h, region) 构建。
                          分块模式下每块瓦片应传入预先构造的 transform，
                          确保每块 scale 与整图一致，避免拼合出现像素错位。
        progress_start/progress_total: 把本部分的进度投影到整体的
        [progress_start, progress_start+progress_total] 区间。
        """
        if (preset_transform is not None
                and any(getattr(layer, "full_canvas", False)
                        for layer in layers)):
            raise RuntimeError(
                "连续底图图层不得在瓦片画布中渲染；"
                "请先通过 _render_base_onto 完成整图底图。"
            )
        # preset_background：分块渲染底图模式传入的底图区域（final 尺寸），
        # 放大到瓦片 internal 尺寸作为画布底色——地形/水面整图渲染一次，
        # 瓦片只画矢量图层，保证底图无缝。
        if preset_background is not None:
            if preset_background.size != (img_w, img_h):
                image = preset_background.resize(
                    (img_w, img_h), Image.BILINEAR)
            else:
                image = preset_background.copy()
        elif self.transparent:
            image = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        else:
            image = Image.new(
                "RGB",
                (img_w, img_h),
                self.style.get("canvas.background", "#F6F7F9"),
            )
        draw = ImageDraw.Draw(image)
        if preset_transform is not None:
            transform = preset_transform
            # 瓦片模式：用瓦片真实世界区域（含 bleed）；整图模式退回 region
            x0, z0, x1, z1 = getattr(transform, "region_tile", transform.region)
        else:
            transform = _build_transform(
                img_w, img_h, region, rotation=self.rotation
            )
            x0, z0, x1, z1 = transform.region
        if final_size and self.width > 0:
            # final_zoom 始终以"最终整图的密度"为基准，
            # 与最终画布尺寸和 world 范围有关，与当前瓦片无关。
            f_transform = _build_transform(
                self.width, self.height, region, rotation=self.rotation
            )
            final_zoom = (
                f_transform.zoom * (final_size / self.width)
                if final_size and self.width != final_size
                else f_transform.zoom
            )
        elif aa_downsample and aa_downsample > 1:
            # LOD 和线宽使用最终画布尺度，避免随瓦片尺寸变化。
            out_transform = _build_transform(
                self.width, self.height, region, rotation=self.rotation
            )
            final_zoom = out_transform.zoom
        else:
            final_zoom = transform.zoom
        ctx = RenderContext(
            image=image,
            draw=draw,
            transform=transform,
            style=self.style,
            world_x0=x0,
            world_z0=z0,
            world_x1=x1,
            world_z1=z1,
            zoom=transform.zoom,
            final_zoom=final_zoom,
            detail_mode=self.detail_mode,
            rotation=self.rotation,
            canvas_width=int(getattr(transform, "full_width", image.width)),
            canvas_height=int(getattr(transform, "full_height", image.height)),
            tile_offset_x=int(getattr(transform, "offset_x", 0)),
            tile_offset_y=int(getattr(transform, "offset_y", 0)),
            shared=self._render_shared,
            session=self._active_session,
            render_job=self.render_job,
        )
        for idx, layer in enumerate(layers):
            self.cancel_check()
            if layer.supports(document):
                t0 = time.time()
                # 注入图层内部进度通道（映射到本图层整体进度区间）
                if progress is not None:
                    ctx.progress = _layer_progress_cb(
                        layer.name, idx, len(layers),
                        progress_start, progress_total, progress)
                else:
                    ctx.progress = None
                start = progress_start + (
                    idx / max(len(layers), 1)
                ) * progress_total
                _report_progress(
                    progress, min(start, 1.0),
                    tr("gui.progress.render_layer", layer=_cn(layer.name)),
                    (layer.name, 0.0),
                )
                layer.render(ctx, document)
                self.last_stats[f"{stats_prefix}{layer.name}"] = time.time() - t0
                if preset_transform is None:
                    self._publish_preview(image, layer.name)
                if progress:
                    p = progress_start + (
                        (idx + 1) / max(len(layers), 1)
                    ) * progress_total
                    _report_progress(progress, min(p, 1.0),
                                     tr("gui.progress.render_layer",
                                        layer=_cn(layer.name)),
                                     (layer.name, 1.0))
        if aa_downsample and aa_downsample > 1:
            out_w = img_w // aa_downsample
            out_h = img_h // aa_downsample
            image = (image.reduce(2) if aa_downsample == 2 and image.size == (out_w * 2, out_h * 2)
                     else image.resize((out_w, out_h), Image.LANCZOS))
        return image

    # ------------------------------------------------------------------
    # 底图渲染（full_canvas 图层整图一次）：无缝地形/水面
    # ------------------------------------------------------------------
    def _render_base_onto(
        self, canvas: Image.Image, *,
        document, region, final_size, base_layers: list,
        progress: ProgressCallback, early_share: float,
    ) -> None:
        """整图一次性渲染 full_canvas 图层（地形/水面）到合并画布作为底图。

        连续场图层（等值线闭合、采样相位对齐、岛湖层级正确）必须整图渲染，
        否则瓦片化会产生接缝/碎裂；之后瓦片只画矢量图层并以底图区域为底色。
        """
        if any(not getattr(layer, "full_canvas", False)
               for layer in base_layers):
            raise RuntimeError(
                "_render_base_onto 只能接收 full_canvas 连续底图图层。"
            )
        draw = ImageDraw.Draw(canvas)
        transform = _build_transform(
            canvas.width, canvas.height, region,
            rotation=self.rotation,
        )
        x0, z0, x1, z1 = transform.region
        if final_size and self.width > 0:
            f_transform = _build_transform(
                self.width, self.height, region,
                rotation=self.rotation,
            )
            final_zoom = (
                f_transform.zoom * (final_size / self.width)
                if final_size and self.width != final_size
                else f_transform.zoom
            )
        else:
            final_zoom = transform.zoom
        ctx = RenderContext(
            image=canvas,
            draw=draw,
            transform=transform,
            style=self.style,
            world_x0=x0,
            world_z0=z0,
            world_x1=x1,
            world_z1=z1,
            zoom=transform.zoom,
            final_zoom=final_zoom,
            detail_mode=self.detail_mode,
            rotation=self.rotation,
            shared=self._render_shared,
            session=self._active_session,
            render_job=self.render_job,
        )
        total = max(len(base_layers), 1)
        for idx, layer in enumerate(base_layers):
            self.cancel_check()
            if layer.supports(document):
                t0 = time.time()
                if progress is not None:
                    ctx.progress = _layer_progress_cb(
                        layer.name, idx, total,
                        0.0, early_share, progress)
                else:
                    ctx.progress = None
                _report_progress(
                    progress,
                    early_share * idx / total,
                    tr("gui.progress.render_base_layer",
                       layer=_cn(layer.name)),
                    (layer.name, 0.0),
                )
                layer.render(ctx, document)
                self.last_stats[f"base_{layer.name}"] = time.time() - t0
                self._publish_preview(canvas, layer.name)
                _report_progress(
                    progress,
                    early_share * (idx + 1) / total,
                    tr("gui.progress.render_base_layer",
                       layer=_cn(layer.name)),
                    (layer.name, 1.0),
                )

    @staticmethod
    def _tile_base_patch(
        canvas: Image.Image,
        bpx0_in: int, bpy0_in: int, bpx1_in: int, bpy1_in: int,
        btw_in: int, bth_in: int, scale_aa: int,
    ) -> Image.Image | None:
        """从底图裁切瓦片（含 bleed）对应区域，放大到瓦片 internal 尺寸。

        用最近邻放大（每 aa×aa 块同色）而非 BILINEAR：瓦片 internal 像素
        (px,py) 严格对应底图 final 像素 (px0 + px//aa)——相邻瓦片在重叠区
        取到完全相同的底图颜色，消除瓦片边界接缝（BILINEAR 放大让相邻
        瓦片的重采样相位不同，边界会出现色带/错位）。
        """
        aa = scale_aa if scale_aa and scale_aa > 1 else 1
        fx0 = bpx0_in // aa
        fy0 = bpy0_in // aa
        fx1 = (bpx1_in + aa - 1) // aa
        fy1 = (bpy1_in + aa - 1) // aa
        fx0 = max(fx0, 0)
        fy0 = max(fy0, 0)
        fx1 = min(fx1, canvas.width)
        fy1 = min(fy1, canvas.height)
        if fx1 <= fx0 or fy1 <= fy0:
            return None
        patch = canvas.crop((fx0, fy0, fx1, fy1))
        if aa > 1 and patch.size != (btw_in, bth_in):
            arr = np.asarray(patch, dtype=np.uint8)
            arr = np.repeat(np.repeat(arr, aa, axis=0), aa, axis=1)
            patch = Image.fromarray(arr, "RGB")
            if patch.size != (btw_in, bth_in):
                patch = patch.crop((0, 0, btw_in, bth_in))
        return patch

    def _tile_session(self):
        from .cmm.session import DocumentSession
        from .preview_road_order import VERSION
        source = self._active_session
        if source is None:
            return None
        document = copy.copy(source.document)
        # 去掉宿主会话回链，避免瓦片重新序列化预览缓存。
        document.__dict__.pop("_session", None)
        session = DocumentSession(document)
        for service, params in (
                ("road_scene", {}),
                ("road_order", {"version": VERSION,
                                "accurate": bool(self.style.get("roads.accurate_intersections", True))})):
            value = source.service_cache.get(service, params)
            if value is not None:
                session.service_cache.put(service, params, value)
        return session

    def _render_tiles_parallel(
        self, *, canvas: Image.Image, document, layers: list,
        region, final_size, tile_size: int, bleed: int,
        aa_downsample: int, internal_w: int, internal_h: int,
        n_tiles: int, early_share: float, base_frac: float,
        use_base: bool, progress: ProgressCallback,
    ) -> bool:
        """在独立进程中按空间分块渲染；各进程按原图层顺序完成自己的分块，避免整图层并行合成改变边缘透明度。"""
        workers = self._resolve_parallel_workers(n_tiles)
        if workers <= 1 or not layers:
            return False

        import multiprocessing as mp

        # 外部守护进程调用方不能再启动子进程；保持现有分块计划并串行执行。
        if mp.current_process().daemon:
            return False

        tile_session = self._tile_session()
        state = {
            "style": self.style,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "detail_mode": self.detail_mode,
            "render_job": self.render_job,
            "layers": layers,
            "shared": self._render_shared,
            "transparent": self.transparent,
            "document": tile_session.document if tile_session is not None else document,
            "session": tile_session,
            "region": region,
            "final_size": final_size,
            "tile_size": tile_size,
            "bleed": bleed,
            "scale_aa": (aa_downsample
                         if aa_downsample and aa_downsample > 1 else 1),
            "internal_w": internal_w,
            "internal_h": internal_h,
        }
        # 启动子进程前识别无法序列化的自定义图层，不为探测重复序列化大型地图。
        try:
            import pickle

            pickle.dumps(
                (self.style, layers, self.render_job),
                protocol=pickle.HIGHEST_PROTOCOL,
            )
        except Exception:  # noqa: BLE001 - 自定义图层继续使用串行路径
            return False

        shm = None
        state_shm = None
        shm_name = None
        base_shape = None
        try:
            if use_base:
                from multiprocessing.shared_memory import SharedMemory

                base = np.asarray(canvas.convert("RGB"), dtype=np.uint8)
                shm = SharedMemory(create=True, size=base.nbytes)
                shared_base = np.ndarray(
                    base.shape, dtype=np.uint8, buffer=shm.buf,
                )
                shared_base[:] = base
                shm_name, base_shape = shm.name, base.shape

            from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED

            started = time.perf_counter()
            tile_phase_start = early_share * base_frac
            _report_progress(
                progress, tile_phase_start,
                tr("gui.progress.render_tiles", n=n_tiles),
                ("tiles", 0.0),
            )
            context = mp.get_context("spawn")
            from multiprocessing.shared_memory import SharedMemory

            setup_started = time.perf_counter()
            self.cancel_check()
            payload = pickle.dumps(state, protocol=pickle.HIGHEST_PROTOCOL)
            self.cancel_check()
            state_shm = SharedMemory(create=True, size=len(payload))
            state_shm.buf[:len(payload)] = payload
            state_descriptor = (state_shm.name, len(payload))
            del payload
            self.last_stats["parallel_state_setup"] = time.perf_counter() - setup_started
            merge_seconds = 0.0
            with ProcessPoolExecutor(
                max_workers=workers, mp_context=context,
                initializer=_tile_worker_init,
                initargs=(state_descriptor, shm_name, base_shape),
            ) as pool:
                futures = []
                completed = 0
                try:
                    for idx in range(n_tiles):
                        self.cancel_check()
                        futures.append(pool.submit(_tile_worker_render, idx))
                    pending = set(futures)
                    while pending:
                        self.cancel_check()
                        done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                        for future in done:
                            self.cancel_check()
                            (idx, px0, py0, px1, py1, mode, size, raw,
                             _worker_elapsed, tile_stats) = future.result()
                            merge_started = time.perf_counter()
                            crop = Image.frombytes(mode, size, raw)
                            if self._disk_mm is not None:
                                self._disk_mm[py0:py1, px0:px1] = np.asarray(crop)
                            else:
                                canvas.paste(crop, (px0, py0))
                            merge_seconds += time.perf_counter() - merge_started
                            self.last_stats.update(tile_stats)
                            completed += 1
                            self._publish_preview(canvas, "tiles")
                            pct = early_share * (
                                base_frac + (1 - base_frac)
                                * completed / n_tiles
                            )
                            _report_progress(
                                progress, pct,
                                tr("gui.progress.merge_tile", i=idx + 1,
                                   n=n_tiles, col=(idx % math.ceil(
                                       self.width / tile_size)) + 1,
                                   row=(idx // math.ceil(
                                       self.width / tile_size)) + 1),
                                ("tiles", completed / n_tiles),
                            )
                except BaseException:
                    for future in futures:
                        future.cancel()
                    raise
            self.last_stats["parallel_tiles"] = time.perf_counter() - started
            self.last_stats["parallel_merge"] = merge_seconds
            self.last_stats["parallel_workers"] = workers
            return True
        finally:
            if state_shm is not None:
                state_shm.close()
                state_shm.unlink()
            if shm is not None:
                shm.close()
                try:
                    shm.unlink()
                except FileNotFoundError:
                    pass

    # ------------------------------------------------------------------
    # 分块渲染
    # ------------------------------------------------------------------
    def _render_tiled(
        self,
        *,
        document,
        layers: list,
        progress: ProgressCallback,
        region: tuple | None,
        final_size: int | None,
        tile_size: int,
        bleed: int,
        aa_downsample: int = 1,
        internal_w: int | None = None,
        internal_h: int | None = None,
    ) -> Image.Image:
        """将整张 W×H 画布切成若干 tile_size × tile_size 瓦片，每块独立渲染后合并。

        瓦片带 bleed 重叠边：每块实际渲染 (tile + 2*bleed) 像素，
        paste 时裁切掉 bleed，避免跨块的线段/多边形出现接缝断裂。

        关键精度约束：每块瓦片使用与整图完全一致的 scale / world region / zoom
        （通过 `_build_tile_transform` 基于整图变换平移构造），
        因此合并结果与"不分块整图渲染"逐像素一致。

        aa_downsample > 1 时，每块像素瓦片 (tile*aa) 渲染，
        然后每块内部完成下采样，再拼合。
        峰值内存 ≈ (tile*aa + 2*bleed)² × 3 bytes，而不是 (W*aa)²×3。
        """
        W, H = self.width, self.height
        if internal_w is None:
            internal_w = W
        if internal_h is None:
            internal_h = H
        cols = math.ceil(W / tile_size)
        rows = math.ceil(H / tile_size)
        n_tiles = cols * rows
        # early 图层共 len(layers) 项，整体进度占比按 [early / all] 估算
        early_share = max(len(layers), 1) / max(len(self._layers), 1)
        # full_canvas 图层（地形/建成区/水面等连续场）与矢量图层分组：
        # 底图整图渲染一次（无缝），瓦片只画矢量图层并以底图区域为底色。
        base_layers = [l for l in layers if getattr(l, "full_canvas", False)]
        tile_layers = [l for l in layers if not getattr(l, "full_canvas", False)]
        if any(getattr(layer, "full_canvas", False) for layer in tile_layers):
            raise RuntimeError(
                "连续底图图层被错误分配到瓦片渲染组。"
            )
        use_base = bool(base_layers)
        base_frac = 0.06 if use_base else 0.0

        # 最终输出画布（按 W×H = 用户目标尺寸）
        if use_base:
            # 底图模式：画布即底图（地形/水面整图渲染），瓦片在其上拼合
            canvas = Image.new(
                "RGB", (W, H), self.style.get("canvas.background", "#F6F7F9"))
            self._render_base_onto(
                canvas, document=document, region=region,
                final_size=final_size, base_layers=base_layers,
                progress=progress, early_share=early_share * base_frac,
            )
        elif self.transparent:
            # 透明画布（公共交通视图 transport 层）：不落盘、不底图
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        elif self.disk_backing and W * H >= DISK_BACK_MIN_PIXELS:
            # 磁盘画布：超大图合并画布落到磁盘映射，峰值内存降到瓦片级
            canvas, self._disk_mm, self._disk_path = self._new_disk_canvas(
                W, H, self.style.get("canvas.background", "#F6F7F9"))
        else:
            canvas = Image.new(
                "RGB", (W, H), self.style.get("canvas.background", "#F6F7F9")
            )

        self.last_stats["disk_backing_active"] = self._disk_mm is not None

        # 整张 internal 画布的变换（若 aa，为 super 尺寸；否则等于 full 尺寸）
        full_transform = _build_transform(
            internal_w, internal_h, region, rotation=self.rotation
        )

        scale_aa = aa_downsample if aa_downsample and aa_downsample > 1 else 1
        bleed_scaled = bleed * scale_aa

        if self._render_tiles_parallel(
            canvas=canvas, document=document, layers=tile_layers,
            region=region, final_size=final_size, tile_size=tile_size,
            bleed=bleed, aa_downsample=aa_downsample,
            internal_w=internal_w, internal_h=internal_h,
            n_tiles=n_tiles, early_share=early_share,
            base_frac=base_frac, use_base=use_base, progress=progress,
        ):
            return canvas

        # 连续底图保持不可变；从部分合成结果裁剪会使后续分块边缘带入已绘制的道路或图标。
        base_canvas = canvas.copy() if use_base else None
        for idx in range(n_tiles):
            self.cancel_check()
            ci, ri = idx % cols, idx // cols
            # 瓦片在 **最终画布** 上的矩形（不含 bleed，也非超采样）
            px0 = ci * tile_size
            py0 = ri * tile_size
            px1 = min(px0 + tile_size, W)
            py1 = min(py0 + tile_size, H)
            tw, th = px1 - px0, py1 - py0

            # 转到 internal（超采样）坐标：瓦片在 internal 画布上的范围
            ipx0, ipy0 = px0 * scale_aa, py0 * scale_aa
            ipx1, ipy1 = px1 * scale_aa, py1 * scale_aa
            # 扩展 bleed（internal 像素为单位）
            bpx0_in = max(0, ipx0 - bleed_scaled)
            bpy0_in = max(0, ipy0 - bleed_scaled)
            bpx1_in = min(internal_w, ipx1 + bleed_scaled)
            bpy1_in = min(internal_h, ipy1 + bleed_scaled)
            btw_in, bth_in = bpx1_in - bpx0_in, bpy1_in - bpy0_in

            # 构造瓦片的 transform：scale 与整图 internal 完全一致，
            # 仅做像素平移（world 映射到瓦片局部坐标）
            tile_transform = _build_tile_transform(
                full_transform,
                full_w=internal_w,
                full_h=internal_h,
                offset_x=bpx0_in,
                offset_y=bpy0_in,
                tile_w=btw_in,
                tile_h=bth_in,
            )

            tile_start = early_share * (base_frac + (1 - base_frac) * idx / n_tiles)
            tile_end = early_share * (base_frac + (1 - base_frac) * (idx + 1) / n_tiles)
            tile_progress = None
            if progress:

                def _cb(p: float, msg: str, stage=None, _ts=tile_start, _te=tile_end) -> None:
                    _report_progress(progress, _ts + (_te - _ts) * p, tr(
                        "gui.progress.tile_detail", i=idx + 1, n=n_tiles,
                        msg=msg), stage)

                _cb.supports_stage_progress = True
                tile_progress = _cb
            tile_img = self._render_early_onto(
                document=document,
                img_w=btw_in,
                img_h=bth_in,
                region=region,
                final_size=final_size,
                layers=tile_layers,  # 底图图层已整图渲染，瓦片只画矢量层
                progress=tile_progress,
                progress_start=0.0,
                progress_total=1.0,
                stats_prefix=f"tile{idx}_",
                aa_downsample=aa_downsample if aa_downsample and aa_downsample > 1 else 1,
                preset_transform=tile_transform,
                preset_background=(
                    self._tile_base_patch(
                        base_canvas, bpx0_in, bpy0_in, bpx1_in, bpy1_in,
                        btw_in, bth_in, scale_aa,
                    ) if use_base else None),
            )

            # tile_img 已经是下采样后尺寸 (btw_in // scale_aa, bth_in // scale_aa)
            # 从中裁切"对应 tile (tw, th) 的准确子矩形"：
            #   瓦片 (px0, py0) 在 tile_img 内的偏移 = (ipx0 - bpx0_in) // scale_aa
            left_off = (ipx0 - bpx0_in) // scale_aa
            top_off = (ipy0 - bpy0_in) // scale_aa
            right_off = left_off + tw
            bottom_off = top_off + th
            crop_box = (left_off, top_off, right_off, bottom_off)
            crop_tile = tile_img.crop(crop_box)
            # 大小校验：应该正好 (tw, th)，否则兜底 resize
            if crop_tile.size != (tw, th):
                crop_tile = crop_tile.resize((tw, th), Image.LANCZOS)
            if self._disk_mm is not None:
                # 磁盘画布：numpy 切片直写映射（落盘由 OS 分页管理）
                self._disk_mm[py0:py1, px0:px1] = np.asarray(crop_tile)
            else:
                canvas.paste(crop_tile, (px0, py0))

            self._publish_preview(canvas, "tiles")

            if progress:
                progress(
                    tile_end,
                    tr("gui.progress.merge_tile", i=idx + 1, n=n_tiles,
                       col=ci + 1, row=ri + 1),
                )

        return canvas

    # ------------------------------------------------------------------
    # 后期图层：在合并后的最终画布一次性绘制
    # ------------------------------------------------------------------
    def _render_late(
        self,
        *,
        image: Image.Image,
        document,
        region: tuple | None,
        final_size: int | None,
        layers: list,
        progress: ProgressCallback,
        total_layers: int,
        done_layers: int,
    ) -> Image.Image:
        # 磁盘画布：无条件先物化到内存一次（必须放在"无后期图层"早退之前）。
        # Image.frombuffer 并不共享 numpy memmap 的写入（实测：写入 mm 后
        # frombuffer 图像仍是初始背景快照），若不物化，无后期图层时返回的
        # image 就是全背景空图；物化从 memmap 读回拼合结果，保证有内容。
        if getattr(image, "_cslm_disk", False):
            mm = getattr(image, "_cslm_disk_mm", None)
            if mm is not None:
                image = Image.fromarray(mm)  # 复制物化，新图不再依赖盘背
                del mm  # 先释放本地 memmap 引用，_drop 里才能删临时文件
            image._cslm_disk = False
            self._drop_disk_canvas()
        if not layers:
            return image
        result = image
        # 抗锯齿：先下采样到最终尺寸，再绘制文字层
        if final_size and final_size != self.width:
            result = image.resize((final_size, max(1, round(self.height * final_size / self.width))), Image.LANCZOS)
        f_transform = _build_transform(
            result.width, result.height, region, rotation=self.rotation
        )
        x0, z0, x1, z1 = f_transform.region
        f_ctx = RenderContext(
            image=result,
            draw=ImageDraw.Draw(result),
            transform=f_transform,
            style=self.style,
            world_x0=x0,
            world_z0=z0,
            world_x1=x1,
            world_z1=z1,
            zoom=f_transform.zoom,
            final_zoom=f_transform.zoom,
            detail_mode=self.detail_mode,
            rotation=self.rotation,
            shared=self._render_shared,
            session=self._active_session,
            render_job=self.render_job,
        )
        total = max(total_layers, 1)
        done = done_layers
        for idx, layer in enumerate(layers):
            self.cancel_check()
            if layer.supports(document):
                t0 = time.time()
                if progress is not None:
                    f_ctx.progress = _layer_progress_cb(
                        layer.name, done, total, 0.0, 1.0, progress)
                else:
                    f_ctx.progress = None
                _report_progress(
                    progress, done / total,
                    tr("gui.progress.render_layer", layer=_cn(layer.name)),
                    (layer.name, 0.0),
                )
                f_ctx.shared["capture_rmp"] = bool(getattr(self, "capture_rmp", False))
                layer.render(f_ctx, document)
                if "rmp_snapshot" in f_ctx.shared:
                    self.last_rmp_snapshot = f_ctx.shared["rmp_snapshot"]
                if layer.name == "transport" and "transit_layout_report" in f_ctx.shared:
                    self.last_stats["transit_layout"] = f_ctx.shared["transit_layout_report"]
                self.last_stats[layer.name] = time.time() - t0
                self._publish_preview(result, layer.name)
                done += 1
                _report_progress(
                    progress, done / total,
                    tr("gui.progress.render_layer", layer=_cn(layer.name)),
                    (layer.name, 1.0),
                )
        return result


def _build_transform(
    width: int, height: int, region: tuple | None = None,
    rotation: int = 0,
):
    """构建游戏坐标 → 像素坐标的变换（返回可调用函数）。

    region 为 None 时渲染整个 17280×17280 世界；
    否则渲染游戏坐标区域 (x0, z0, x1, z1)，保持选区边界和统一像素比例。

    复刻 CSLMapView 的方向映射（反编译 + IoU 验证确认）：
    1. TranslateFromXML：u=(x+8640)*s, v=W-(z+8640)*s（Z 翻转，南在上）
    2. CSLMapView 最终输出再整体逆时针旋转 90°（PIL ROTATE_90）
       → 等价于 px=W-(z+8640)*s, py=W-(x+8640)*s
    即世界 z 轴映射到像素 x 轴（取反），世界 x 轴映射到像素 y 轴（取反）。

    rotation：输出图像额外顺时针旋转的度数（0/90/180/270）。
    旋转在像素坐标层施加（绕画布中心），所有图层经 transform 后自动跟随旋转，
    文字/图标在各自图层内按同一旋转量重新渲染，保证清晰不失真。
    """
    from .export_region import normalize_bounds, DEFAULT_WORLD, output_rotation
    x0, z0, x1, z1 = normalize_bounds(region or DEFAULT_WORLD)
    rot = output_rotation(rotation)
    pcx, pcy = width / 2.0, height / 2.0
    cx, cz = (x0 + x1) / 2.0, (z0 + z1) / 2.0
    c, s = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[rot]
    span_w = abs(c) * (z1-z0) + abs(s) * (x1-x0)
    span_h = abs(s) * (z1-z0) + abs(c) * (x1-x0)
    scale = min(width / span_w, height / span_h)

    def transform(x: float, z: float):
        dx, dy = -(z-cz)*scale, -(x-cx)*scale
        return pcx + dx*c - dy*s, pcy + dx*s + dy*c

    def inverse(px, py):
        dx, dy = px-pcx, py-pcy
        return cx - (-dx*s + dy*c)/scale, cz - (dx*c + dy*s)/scale

    transform.inverse = inverse
    transform.scale = scale
    # 画布实际覆盖的世界范围（精确区域，无扩方）
    transform.region = (x0, z0, x1, z1)
    # 放大倍数 = 像素密度 / 全图 4096 基准密度。全图渲染时 = min(w,h)/4096；
    # 区域渲染时反映真实放大倍数，供图层等比放大线宽/字号/图标。
    transform.zoom = scale / (4096.0 / 17280.0)
    return transform


def _build_tile_transform(
    base_transform,
    full_w: int,
    full_h: int,
    offset_x: int,
    offset_y: int,
    tile_w: int,
    tile_h: int,
):
    """基于整图 base_transform 构造子瓦片的变换，保证 scale 与 world region 完全一致。

    base_transform 把 world → 整图像素 (0..full_w, 0..full_h)。
    子瓦片需要 world → 瓦片局部像素 (0..tile_w, 0..tile_h)，
    等价于「先整图变换，再减去 (offset_x, offset_y)」。
    scale / region / zoom 属性直接从 base 复用，保证渲染效果与整图一致。

    旋转已由 base_transform 施加，这里只平移到瓦片局部坐标。
    """
    def transform(x: float, z: float):
        px, py = base_transform(x, z)
        return px-offset_x, py-offset_y

    def inverse(px, py):
        return base_transform.inverse(px+offset_x, py+offset_y)

    transform.inverse = inverse
    transform.scale = base_transform.scale
    transform.region = base_transform.region
    transform.zoom = base_transform.zoom
    corners = [inverse(px, py) for px, py in
               ((0, 0), (tile_w, 0), (0, tile_h), (tile_w, tile_h))]
    transform.region_tile = (min(p[0] for p in corners), min(p[1] for p in corners),
                             max(p[0] for p in corners), max(p[1] for p in corners))
    transform.full_width = full_w
    transform.full_height = full_h
    transform.offset_x = offset_x
    transform.offset_y = offset_y
    return transform
