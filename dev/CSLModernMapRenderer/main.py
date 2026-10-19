"""命令行入口。

用法：
    python main.py parse <文件>                 # 解析存档，输出数据统计
    python main.py render <文件> [选项]         # 渲染整张地图为 PNG
    python main.py metro <文件> [选项]          # 渲染纯地铁示意图为 PNG
    python main.py random <文件> [选项]         # 随机特写：随机渲染一块城市建成区
    python main.py interchange <文件> [选项]    # 立交特写：识别立交桥并拼图
    python main.py export-web <文件> [选项]     # 导出 MapLibre 单文件网页地图 HTML（资源全部内嵌）
    python main.py list-styles                  # 列出可用主题

详见 `python main.py -h`。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from csmodernmap.i18n import AVAILABLE_LANGS, detect_lang, set_lang

# 所有子命令名（判断裸存档路径时使用，避免与子命令重名）
_SUBCOMMANDS = {
    "engine", "parse", "render", "random", "interchange",
    "spot", "transit", "metro", "list-styles",
    "export-web", "self-check",
}


def cmd_parse(args: argparse.Namespace) -> int:
    """解析存档并输出统计信息。"""
    from csmodernmap import parser as map_parser
    from csmodernmap.cmm.session import DocumentSession

    if not Path(args.file).exists():
        print(f"错误：存档文件不存在：{args.file}")
        print("提示：请检查路径是否正确，或用绝对路径重试。")
        return 1
    t0 = time.time()
    print(f"解析 {args.file} ...")
    document = map_parser.parse_document(args.file)
    session = DocumentSession(document=document)
    mapdata = session._legacy_view()
    elapsed = time.time() - t0

    print(f"\n=== {mapdata.city_name or '(未命名城市)'} ===")
    print(f"  生成时间: {mapdata.generated}")
    print(f"  海平面: {mapdata.sea_level}")
    print(f"  解析耗时: {elapsed:.1f}s")
    print()
    print(f"  地形网格: {mapdata.terrain.width}x{mapdata.terrain.height}")
    print(f"  节点: {len(mapdata.nodes)}")
    print(f"  道路线段: {len(mapdata.segments)}")
    print(f"  建筑: {len(mapdata.buildings)}")
    print(f"  区划: {len(mapdata.districts)}")
    print(f"  公园: {len(mapdata.parks)}")
    print(f"  森林: {len(mapdata.forests)}")
    print(f"  交通线路: {len(mapdata.transports)}")
    print()

    if mapdata.districts:
        print("  区划列表:")
        for d in mapdata.districts:
            print(f"    - {d.name} (id={d.id}, {len(d.points)} 个边界点)")
    if mapdata.transports:
        print("  交通线路:")
        for t in mapdata.transports:
            print(f"    - {t.name} ({t.type}, {len(t.stops)} 站, 颜色 {t.color})")

    # 道路类别统计
    from collections import Counter
    icls_counter = Counter(s.icls for s in mapdata.segments)
    print("\n  道路类别统计 (icls):")
    for icls, cnt in icls_counter.most_common(15):
        print(f"    {icls}: {cnt}")

    # 建筑功能区统计
    service_counter = Counter(b.service for b in mapdata.buildings)
    print("\n  建筑功能区统计 (service):")
    for srv, cnt in service_counter.most_common(15):
        print(f"    {srv}: {cnt}")

    print(f"\n解析完成，共 {len(mapdata.buildings) + len(mapdata.segments) + len(mapdata.nodes)} 个实体。")
    return 0


def _load_style(args: argparse.Namespace):
    """按 --style 加载主题，不存在时提示并返回 None。"""
    from csmodernmap._paths import resource_dir
    from csmodernmap.style import METRO_STYLE_FILE, load_style

    style_path = resource_dir("styles") / f"{args.style}.yaml"
    if not style_path.exists():
        # 列出可用主题及其友好名，便于用户选择（排除地铁图专用配置）
        styles_dir = resource_dir("styles")
        available = []
        for p in sorted(styles_dir.glob("*.yaml")):
            if p.name == METRO_STYLE_FILE:
                continue
            try:
                s = load_style(str(p))
                friendly = s.get("name", "") or ""
                available.append(
                    f"{p.stem}" + (f" ({friendly})" if friendly else "")
                )
            except Exception:  # noqa: BLE001
                available.append(p.stem)
        print(f"错误：主题“{args.style}”不存在。")
        print(f"可用主题：{', '.join(available)}")
        return None
    s = load_style(
        str(style_path),
        font_scale=getattr(args, "font_scale", 1.0),
    )
    return s


def _apply_station_fix_arg(args: argparse.Namespace, style, mapdata=None) -> None:
    """将显式站点附属文件传入交通渲染。"""
    path = getattr(args, "station_fix", None)
    if path is None:
        return
    transports = style.data.setdefault("transports", {})
    station = transports.setdefault("station", {})
    station["sidecar"] = str(path)
    if mapdata is not None:
        # POI 交通投影由独立图层构建；把路径保存在内存解析结果中，让它与 TransportLayer 使用同一附属文件。
        mapdata._station_fix_path = str(path)


def _check_file(file: str) -> bool:
    """检查存档文件是否存在，给出友好提示。"""
    if Path(file).exists():
        return True
    print(f"错误：存档文件不存在：{file}")
    print("提示：请检查路径是否正确，或用绝对路径重试。")
    print("     支持 .mmap / .mmap.gz（并兼容旧 .mmap）。")
    return False


def _cli_progress(pct: float, msg: str) -> None:
    """CLI 进度条（pct 为 0~100 绝对百分比），结尾换行。"""
    sys.stdout.write(f"\r网页导出 {float(pct):3.0f}% {msg}   ")
    sys.stdout.flush()
    if float(pct) >= 100:
        sys.stdout.write("\n")


# 图层注册表由 layers.registry 统一维护；这里保留旧 tuple API 供 CLI 逻辑使用。
_LAYER_REGISTRY = None  # 懒加载，避免循环导入


def _get_layer_registry():
    """懒加载图层注册表（中文名 / 英文名 → 类）。

    每条目 (中文名, 英文名, 类, 默认启用)。
    default=False 的图层为独立功能（如行政区划/用地类型），
    仅在 --layers 显式指定时渲染，不随默认渲染叠加。
    """
    global _LAYER_REGISTRY
    if _LAYER_REGISTRY is not None:
        return _LAYER_REGISTRY
    from csmodernmap.layers.registry import legacy_layer_registry
    _LAYER_REGISTRY = legacy_layer_registry()
    return _LAYER_REGISTRY


def _build_renderer(
    style, render_size: int, layers: str = None, tile: int = 0,
    memory_friendly: bool = False,
    disk_backing: bool = False,       # 磁盘画布：大图合并落盘降内存
    no_watermark: bool = False,       # 新增：禁用水印
    watermark_text: str | None = None,  # 新增：自定义附加文字
    rotate: int = 0,                  # 新增：输出旋转角度（0/90/180/270）
    transit_colored: bool = False,    # 公共交通图：公共交通建筑按线路色上色
    detail: str = "auto",            # 地图内容密度，与输出尺寸独立
):
    """按图层顺序构建渲染器。

    layers: 逗号分隔的图层名（中/英文均可），None 表示全部启用。
    名称前后空白会被忽略；不识别的名称会给出提示。
    tile: 分块瓦片大小（像素），0=自动。
    memory_friendly: 内存友好模式。True 时超大图自动分块，控制峰值内存。
    no_watermark: True 时跳过水印图层。
    watermark_text: 自定义附加文字（渲染到水印中）。
    rotate: 输出顺时针旋转角度（0/90/180/270），文字/图标在旋转坐标系中重新渲染。
    """
    registry = _get_layer_registry()
    name_to_cls = {}
    from csmodernmap.layers.registry import layer_aliases
    aliases = layer_aliases()
    for cn, en, cls, _default in registry:
        name_to_cls[cn] = cls
        name_to_cls[en] = cls
        name_to_cls[en.lower()] = cls
    for alias, canonical in aliases.items():
        for _cn, en, cls, _default in registry:
            if en == canonical:
                name_to_cls[alias] = cls
                name_to_cls[str(alias).lower()] = cls
                break
    from csmodernmap.layers.watermark_layer import WatermarkLayer
    from csmodernmap.renderer import Renderer

    renderer = Renderer(
        style, width=render_size, height=render_size,
        rotation=int(rotate or 0),
        detail_mode=detail,
    )
    renderer.parallel_workers = 0
    renderer.tile_size = int(tile or 0)
    renderer.memory_friendly = bool(memory_friendly)
    # 低内存模式自动启用磁盘画布（一体化降内存）
    renderer.disk_backing = bool(disk_backing or memory_friendly)

    def _new_layer(cls):
        """创建图层实例；水印层额外透传自定义文字。"""
        if transit_colored and cls.__name__ == "BuildingLayer":
            return cls(transit_colored=True)
        layer = cls()
        if cls is WatermarkLayer and watermark_text:
            layer._custom_text = watermark_text
        return layer

    if layers:
        wanted = [s.strip() for s in layers.split(",") if s.strip()]
        selected = []
        unknown = []
        for w in wanted:
            if w in name_to_cls:
                selected.append(name_to_cls[w])
            else:
                unknown.append(w)
        if unknown:
            print(f"警告：未识别的图层名（已忽略）：{', '.join(unknown)}")
            valid = ", ".join(
                f"{cn}/{en}" for cn, en, _cls, _default in registry)
            print(f"     可用图层：{valid}")
        # 按 registry 顺序加入，保证渲染层级正确
        added = set()
        for cn, en, cls, _default in registry:
            if cls in selected and cls not in added:
                if no_watermark and cls is WatermarkLayer:
                    continue
                renderer.add_layer(_new_layer(cls))
                added.add(cls)
    else:
        # 未指定 --layers：渲染全部默认启用图层（独立功能图层默认关闭）
        for _cn, _en, cls, default in registry:
            if not default:
                continue
            if no_watermark and cls is WatermarkLayer:
                continue
            renderer.add_layer(_new_layer(cls))
    from csmodernmap.render_jobs import build_render_job
    renderer.render_job = build_render_job(
        "normal", layers=tuple(layer.name for layer in renderer._layers),
        width=render_size, height=render_size,
        zoom=render_size / 4096.0, detail_mode=detail,
    )
    return renderer


def _attach_traffic(mapdata, traffic_path: str) -> None:
    """挂交通快照到文档。

    单一数据包策略（v3.0）：
      1) --traffic 显式指定外部 JSON → 覆盖加载
      2) .mmap 内嵌 <TrafficSnapshotJson>（loader 已自动填入 document.traffic）
         → 直接使用，无需额外操作
      3) 都没有 → 跳过
    """
    from csmodernmap.traffic import TrafficSnapshot

    if traffic_path:
        # 外部 JSON 覆盖
        snap = TrafficSnapshot.load(traffic_path)
        mapdata.traffic = snap
        n = len(snap.segments) if snap and snap.segments else 0
        print(f"[交通] 使用外部 JSON（覆盖）: {traffic_path} ({n} 段)")
        return

    # 检查内嵌
    snap = getattr(mapdata, "traffic", None)
    segments = getattr(snap, "segments", None)
    if snap and segments:
        n = len(segments)
        paused = getattr(snap, "simulation_paused", False)
        tag = "，模拟已暂停" if paused else ""
        print(f"[交通] 使用 csmodernmap 内嵌快照（{n} 段）{tag}")
        return
    print("[交通] 无交通数据（未内嵌且未指定 --traffic）")


def _open_session(document):
    """为一个文档建立唯一的派生服务会话。

    同一命令内的全部渲染（底图/叠加/特写/拼图）与工具函数共享该会话：
    旧模型兼容视图只构造一次，道路场景/水域/山影等派生结果跨渲染复用。
    """
    from csmodernmap.cmm import DocumentSession

    return DocumentSession(document=document)


def cmd_render(args: argparse.Namespace) -> int:
    """渲染地图为 PNG。"""
    if not _check_file(args.file):
        return 1
    from csmodernmap import export
    from csmodernmap import parser as map_parser
    from csmodernmap.services.station import StationSidecarError
    from PIL import Image  # noqa: F401  保留以防遗漏
    t0 = time.time()
    print(f"解析 {args.file} ...")
    mapdata = map_parser.parse_document(args.file)
    _attach_traffic(mapdata, getattr(args, "traffic", None))
    session = _open_session(mapdata)

    print(f"加载主题 {args.style} ...")
    style = _load_style(args)
    if style is None:
        return 1
    _apply_station_fix_arg(args, style, mapdata)
    if getattr(args, "no_poi_names", False):
        style.data.setdefault("poi_icons", {})["show_name"] = False

    region = None
    if args.region:
        parts = [float(v.strip()) for v in args.region.split(",")]
        if len(parts) != 4:
            print("错误: --region 需为 x0,z0,x1,z1 四个数值（游戏坐标）")
            return 1
        region = tuple(parts)
        x0, z0, x1, z1 = region
        print(f"渲染区域: x {x0:.0f}~{x1:.0f}, z {z0:.0f}~{z1:.0f}")

    # 通过 aa_downsample 让 renderer 内部透明处理分块 AA，峰值内存恒定为 tile²
    aa_rate = 2 if args.aa else 1
    renderer = _build_renderer(
        style, args.size, args.layers, tile=args.tile_size,
        memory_friendly=args.memory_friendly,
    disk_backing=bool(getattr(args, "disk_backing", False)),
        no_watermark=getattr(args, "no_watermark", False),
        watermark_text=getattr(args, "watermark_text", None),
        rotate=getattr(args, "rotate", 0),
        detail=getattr(args, "detail", "auto"),
    )
    tile_px = int(getattr(renderer, "tile_size", 0) or 0)
    if tile_px:
        tile_msg = f"，瓦片 {tile_px}px"
    elif max(args.size, args.size) > 6144:
        tile_msg = "，自动分块 (4K 瓦片)"
    else:
        tile_msg = ""
    print(f"渲染中 ({args.size}x{args.size}{tile_msg}, AA={args.aa}) ...")
    try:
        image = renderer.render(
            mapdata, region=region, aa_downsample=aa_rate,
            session=session,
        )
    except StationSidecarError as exc:
        print(f"错误：站点侧车未应用，渲染已停止：{exc}")
        return 1
    if renderer.last_stats.get("auto_tiled_fallback"):
        print("  提示：整图渲染峰值内存超上限，已自动切换分块渲染（结果一致）")
    export.export_png(image, args.output)
    print(f"完成: {args.output} (总耗时 {time.time() - t0:.1f}s)")
    return 0


def cmd_transit(args: argparse.Namespace) -> int:
    """公共交通图：全图（剔除交通线路）→ 淡白遮罩 → 叠加全部公共交通线路/站点。

    与 GUI「公共交通图」按钮同源的两段式合成（transit 单元 + 透明画布）。
    """
    if not _check_file(args.file):
        return 1
    import copy

    from csmodernmap import export
    from csmodernmap import parser as map_parser
    from csmodernmap import transit
    from csmodernmap.layers.poi_icon_layer import POIIconLayer
    from csmodernmap.layers.transport_layer import TransportLayer
    from csmodernmap.renderer import Renderer
    from csmodernmap.services.station import StationSidecarError
    from PIL import Image
    t0 = time.time()
    print(f"解析 {args.file} ...")
    mapdata = map_parser.parse_document(args.file)
    _attach_traffic(mapdata, getattr(args, "traffic", None))
    session = _open_session(mapdata)
    utility_map = session._legacy_view()

    print(f"加载主题 {args.style} ...")
    style = _load_style(args)
    if style is None:
        return 1
    _apply_station_fix_arg(args, style, mapdata)
    if getattr(args, "no_poi_names", False):
        style.data.setdefault("poi_icons", {})["show_name"] = False

    region = None
    if args.region:
        parts = [float(v.strip()) for v in args.region.split(",")]
        if len(parts) != 4:
            print("错误: --region 需为 x0,z0,x1,z1 四个数值（游戏坐标）")
            return 1
        region = tuple(parts)

    # 线路类型：--transit-types 指定；未指定时默认全选注册表类型。
    if args.transit_types:
        types = transit.resolve_enabled(
            [s.strip() for s in args.transit_types.split(",")])
    else:
        types = transit.resolve_enabled(list(transit.TYPE_REGISTRY))
    if not types:
        print("错误: 没有可渲染的公共交通类型（存档无线路或 --transit-types 无效）")
        return 1

    aa_rate = 2 if args.aa else 1
    # 段 1：全图（剔除交通线路层 + 普通 POI——公共交通 POI 由叠加层单独标注）
    base_layers = None
    if args.layers:
        from csmodernmap.layers.registry import canonical_layer_key

        base_layers = [s.strip() for s in args.layers.split(",")
                       if s.strip() and canonical_layer_key(s.strip()) not in {
                           "transit", "poi"}]
    else:
        # 公共交通图的底图始终剔除普通交通层和普通 POI，避免叠加两次。
        base_layers = [en for _cn, en, cls, _default in _get_layer_registry()
                       if cls not in (TransportLayer, POIIconLayer)]
    renderer = _build_renderer(
        style, args.size, ",".join(base_layers) if base_layers else None,
        tile=args.tile_size, memory_friendly=args.memory_friendly,
        disk_backing=bool(getattr(args, "disk_backing", False)),
        no_watermark=True,  # 底图不画水印：合成交通叠加后在最上层统一画一次
        watermark_text=getattr(args, "watermark_text", None),
        rotate=getattr(args, "rotate", 0),
        detail=getattr(args, "detail", "auto"),
    )
    from csmodernmap.render_jobs import build_transit_jobs
    base_job, overlay_job = build_transit_jobs(
        width=args.size, height=args.size,
        zoom=args.size / 4096.0,
        detail_mode=getattr(args, "detail", "auto"),
        base_layers=tuple(base_layers or ()),
    )
    renderer.render_job = base_job
    print(f"渲染底图（剔除交通线路）({args.size}x{args.size}, AA={args.aa}) ...")
    img = renderer.render(
        mapdata, region=region, aa_downsample=aa_rate,
        session=session,
    )
    # 淡白遮罩
    mask = float(args.transit_mask)
    if mask > 0:
        img = Image.blend(img, Image.new("RGB", img.size, (255, 255, 255)),
                          mask)
    # 段 2：公共交通叠加（透明画布，线路 + 站点）
    s2 = copy.deepcopy(style)
    cfg = s2.data.setdefault("transports", {})
    cfg["enabled"] = types
    station_cfg = dict(cfg.get("station") or {})
    station_cfg["enabled"] = True
    station_cfg.setdefault("merge_range", 48.0)
    station_cfg.setdefault("size", {"fixed": 2.5, "scaled": 5.0})
    cfg["station"] = station_cfg
    renderer2 = Renderer(
        s2, width=args.size, height=args.size,
        rotation=int(getattr(args, "rotate", 0) or 0),
        detail_mode="detailed",
    )
    renderer2.parallel_workers = 0
    renderer2.transparent = True
    # 公共交通 POI 图标/名称在最高层（后加），线路与站点圆点先画——
    # 图标/文字不被线路、圆点遮挡；圆点靠图标垂直偏移避让（poi_icons.station_avoid）
    renderer2.add_layer(TransportLayer(transit_view=True))
    renderer2.add_layer(POIIconLayer(transit_only=True, force_detail=True))
    renderer2.render_job = overlay_job
    print(f"渲染公共交通叠加（{', '.join(types)}）...")

    def _p_tr(pct: float, msg: str) -> None:
        sys.stdout.write(f"\r公共交通叠加 {pct * 100:3.0f}% {msg}   ")
        sys.stdout.flush()

    try:
        img_tr = renderer2.render(mapdata, region=region, aa_downsample=aa_rate,
                                  progress=_p_tr, session=session)
    except StationSidecarError as exc:
        sys.stdout.write("\n")
        print(f"错误：站点侧车未应用，渲染已停止：{exc}")
        return 1
    sys.stdout.write("\n")
    sys.stdout.flush()
    img = img.convert("RGBA")
    img.alpha_composite(img_tr)
    img = img.convert("RGB")
    # 合成完成后在最上层绘制水印（保证水印不被交通线路/站点遮挡）
    if not getattr(args, "no_watermark", False):
        from csmodernmap.layers.watermark_layer import WatermarkLayer

        WatermarkLayer.draw_on_image(
            img, style, utility_map,
            render_scale=img.size[0] / 4096,
            custom_text=getattr(args, "watermark_text", None),
        )
    export.export_png(img, args.output)
    print(f"完成: {args.output} (总耗时 {time.time() - t0:.1f}s)")
    return 0


def cmd_metro(args: argparse.Namespace) -> int:
    """渲染纯地铁示意图：线路、站点、站名、图例与城市标题。"""
    if not _check_file(args.file):
        return 1
    import copy

    from PIL import Image, ImageColor

    from csmodernmap import export
    from csmodernmap import parser as map_parser
    from csmodernmap import transit
    from csmodernmap._paths import resource_dir
    from csmodernmap.layers.transport_layer import TransportLayer
    from csmodernmap.metro_features import (
        metro_content_canvas_size, recommended_metro_output_size)
    from csmodernmap.renderer import Renderer
    from csmodernmap.services.station import StationSidecarError
    from csmodernmap.style import load_metro_style
    t0 = time.time()
    print(f"解析 {args.file} ...")
    mapdata = map_parser.parse_document(args.file)
    session = _open_session(mapdata)
    utility_map = session._legacy_view()

    print("加载地铁示意图主题 ...")
    style = load_metro_style(
        str(resource_dir("styles")),
        font_scale=getattr(args, "font_scale", 1.0),
    )
    _apply_station_fix_arg(args, style, mapdata)
    s2 = copy.deepcopy(style)
    cfg = s2.data.setdefault("transports", {})
    requested = [s.strip() for s in (args.metro_types or "").split(",")
                 if s.strip()]
    if not requested:
        requested = ["Metro", "Train", "Monorail"]
    types = transit.resolve_enabled(requested)
    available = {
        transit.normalize_type(t.type) or t.type
        for t in utility_map.transports
    }
    types = [key for key in types if key in available]
    if not types:
        print("错误：存档中没有可渲染的地铁线路，请检查 --metro-types。")
        return 1
    cfg["enabled"] = types
    cfg["abstract"] = True
    cfg["octilinear"] = True
    station_cfg = copy.deepcopy(cfg.get("station") or {})
    station_cfg["enabled"] = True
    # 接驳设施图标：开关与大小倍率与 GUI 地铁示意图对话框一致。
    facilities_cfg = copy.deepcopy(station_cfg.get("facilities") or {})
    facilities_cfg["enabled"] = not bool(
        getattr(args, "no_facilities", False))
    facilities_scale = max(0.5, min(float(
        getattr(args, "facilities_scale", 1.0) or 1.0), 2.0))
    if facilities_scale != 1.0:
        for key in ("size", "offset"):
            value = facilities_cfg.get(key)
            if isinstance(value, dict):
                facilities_cfg[key] = {
                    k: (v * facilities_scale if isinstance(v, (int, float))
                        else v)
                    for k, v in value.items()
                }
            elif isinstance(value, (int, float)):
                facilities_cfg[key] = value * facilities_scale
    station_cfg["facilities"] = facilities_cfg
    cfg["station"] = station_cfg
    label_cfg = copy.deepcopy(cfg.get("label") or {})
    label_cfg["enabled"] = True
    cfg["label"] = label_cfg

    requested_size = int(args.size)
    size = (recommended_metro_output_size(utility_map, types)
            if requested_size <= 0 else max(requested_size, 128))
    rotation = int(getattr(args, "rotate", 0) or 0)
    canvas_w, canvas_h = metro_content_canvas_size(
        utility_map, types, size, rotation=rotation)
    aa_rate = 2 if args.aa else 1
    renderer = Renderer(
        s2, width=canvas_w, height=canvas_h,
        rotation=rotation,
    )
    renderer.parallel_workers = 0
    renderer.tile_size = int(getattr(args, "tile_size", 0) or 0)
    renderer.memory_friendly = bool(getattr(args, "memory_friendly", False))
    renderer.disk_backing = bool(getattr(args, "disk_backing", False))
    renderer.transparent = True
    renderer.add_layer(TransportLayer(transit_view=True, metro=True))
    from csmodernmap.render_jobs import build_render_job
    renderer.render_job = build_render_job(
        "metro", layers=("transit",), width=canvas_w, height=canvas_h,
        zoom=float(size) / 4096.0, detail_mode="detailed",
        derive_params={"schematic": True, "transparent": True},
    )
    print(f"渲染地铁示意图（{', '.join(types)}，"
          f"{canvas_w}x{canvas_h}，AA={args.aa}）...")

    def _progress(pct: float, msg: str) -> None:
        sys.stdout.write(f"\r地铁示意图 {pct * 100:3.0f}% {msg}   ")
        sys.stdout.flush()
    try:
        image = renderer.render(
            mapdata, region=None, aa_downsample=aa_rate, progress=_progress,
            session=session,
        )
    except StationSidecarError as exc:
        sys.stdout.write("\n")
        print(f"错误：站点侧车未应用，渲染已停止：{exc}")
        return 1
    sys.stdout.write("\n")
    sys.stdout.flush()
    bg = style.get("canvas.background", "#FAFAF8")
    try:
        bg_rgb = ImageColor.getrgb(str(bg))
    except ValueError:
        bg_rgb = (250, 250, 248)
    out = Image.new("RGBA", image.size, bg_rgb + (255,))
    out.alpha_composite(image.convert("RGBA"))
    export.export_png(out.convert("RGB"), args.output)
    print(f"完成: {args.output} (总耗时 {time.time() - t0:.1f}s)")
    return 0


def cmd_random(args: argparse.Namespace) -> int:
    """随机特写：按建筑密度随机选一块城市建成区，渲染高清局部图。"""
    if not _check_file(args.file):
        return 1
    import random

    from csmodernmap import export
    from csmodernmap import parser as map_parser
    from PIL import Image  # noqa: F401

    from csmodernmap.regions import random_region

    t0 = time.time()
    print(f"解析 {args.file} ...")
    mapdata = map_parser.parse_document(args.file)
    _attach_traffic(mapdata, getattr(args, "traffic", None))
    session = _open_session(mapdata)
    utility_map = session._legacy_view()

    print(f"加载主题 {args.style} ...")
    style = _load_style(args)
    if style is None:
        return 1
    if getattr(args, "no_poi_names", False):
        style.data.setdefault("poi_icons", {})["show_name"] = False

    rng = random.Random(args.seed)
    region = random_region(utility_map, rng=rng)
    x0, z0, x1, z1 = region
    span = max(x1 - x0, z1 - z0)
    print(f"随机区域: x {x0:.0f}~{x1:.0f}, z {z0:.0f}~{z1:.0f} ({span:.0f} 单位)")

    aa_rate = 2 if args.aa else 1
    renderer = _build_renderer(
        style, args.size, args.layers, tile=args.tile_size,
        memory_friendly=args.memory_friendly,
    disk_backing=bool(getattr(args, "disk_backing", False)),
        no_watermark=getattr(args, "no_watermark", False),
        watermark_text=getattr(args, "watermark_text", None),
        rotate=getattr(args, "rotate", 0),
    )
    print(f"渲染中 ({args.size}x{args.size}, AA={args.aa}) ...")
    image = renderer.render(
        mapdata, region=region, aa_downsample=aa_rate, session=session,
    )
    export.export_png(image, args.output)
    print(f"完成: {args.output} (总耗时 {time.time() - t0:.1f}s)")
    return 0


def cmd_interchange(args: argparse.Namespace) -> int:
    """立交特写：识别立交桥，逐处区域渲染后拼成一张图。"""
    if not _check_file(args.file):
        return 1
    from PIL import Image  # noqa: F401

    from csmodernmap import export
    from csmodernmap import parser as map_parser
    from csmodernmap.interchanges import render_interchange_mosaic

    t0 = time.time()
    print(f"解析 {args.file} ...")
    mapdata = map_parser.parse_document(args.file)
    _attach_traffic(mapdata, getattr(args, "traffic", None))
    session = _open_session(mapdata)
    utility_map = session._legacy_view()

    print(f"加载主题 {args.style} ...")
    style = _load_style(args)
    if style is None:
        return 1
    if getattr(args, "no_poi_names", False):
        style.data.setdefault("poi_icons", {})["show_name"] = False

    def render_region(region, size):
        aa_rate = 2 if args.aa else 1
        renderer = _build_renderer(
            style, size, args.layers, tile=args.tile_size,
            memory_friendly=args.memory_friendly,
    disk_backing=bool(getattr(args, "disk_backing", False)),
            no_watermark=True,  # 立交小图不画水印，拼图完成后统一画一次
            watermark_text=getattr(args, "watermark_text", None),
            rotate=getattr(args, "rotate", 0),
        )
        return renderer.render(mapdata, region=region, aa_downsample=aa_rate,
                               session=session)

    def progress(pct: float, msg: str) -> None:
        print(f"  [{pct * 100:5.1f}%] {msg}")

    mosaic, ics = render_interchange_mosaic(
        utility_map, render_region, tile_size=args.tile,
        max_count=args.top, progress=progress,
    )
    if not ics:
        print("未识别到立交")
        return 1
    print(f"识别立交 {len(ics)} 处，拼图尺寸 {mosaic.size}")
    # 拼图完成后在最顶层统一画一次水印（小图阶段已禁用，避免每个格子都带水印）
    if not getattr(args, "no_watermark", False):
        from csmodernmap.layers.watermark_layer import WatermarkLayer

        WatermarkLayer.draw_on_image(
            mosaic, style, utility_map,
            render_scale=mosaic.size[0] / 4096,
            custom_text=getattr(args, "watermark_text", None),
        )
    export.export_png(mosaic, args.output)
    print(f"完成: {args.output} (总耗时 {time.time() - t0:.1f}s)")
    return 0


def _layers_no_traffic(layers: str | None) -> str | None:
    """图层字符串去掉交通图层；None=全部图层 → 显式列出全部（排除交通）。"""
    from csmodernmap.layers.registry import canonical_layer_key

    if layers:
        wanted = [w for w in (s.strip() for s in layers.split(",")) if w]
        return ",".join(w for w in wanted
                        if canonical_layer_key(w) != "traffic")
    # 未指定 --layers：只列默认启用图层（与默认渲染一致，独立功能图层不参与对比）
    return ",".join(
        en for _cn, en, _cls, default in _get_layer_registry()
        if en != "traffic" and default
    )


def cmd_spot(args: argparse.Namespace) -> int:
    """特写拼图：按取景主题（随机/堵车）取景多块区域，拼成网格图。

    --mode random      按建筑密度加权随机取景（同 random 子命令，但支持多块拼图）
    --mode congestion  按拥堵值加权随机取景（最堵区域，需交通快照）
    --mode top         聚类最堵路段簇取 Top N（榜单，需交通快照）
    """
    if not _check_file(args.file):
        return 1
    import random as _random

    from csmodernmap import export
    from csmodernmap import parser as map_parser
    from csmodernmap.spots import (
        compose_compare,
        has_traffic,
        render_spot_mosaic,
        sample_congestion_spots,
        sample_random_spots,
        top_congestion_spots,
    )

    t0 = time.time()
    print(f"解析 {args.file} ...")
    mapdata = map_parser.parse_document(args.file)
    _attach_traffic(mapdata, getattr(args, "traffic", None))
    session = _open_session(mapdata)
    utility_map = session._legacy_view()

    print(f"加载主题 {args.style} ...")
    style = _load_style(args)
    if style is None:
        return 1
    if getattr(args, "no_poi_names", False):
        style.data.setdefault("poi_icons", {})["show_name"] = False

    if args.mode in ("congestion", "top") and not has_traffic(utility_map):
        print("错误：当前存档没有交通快照（未内嵌且未指定 --traffic），无法生成堵车特写")
        return 1
    rng = _random.Random(args.seed) if args.seed is not None else None
    if args.mode == "random":
        regions = sample_random_spots(utility_map, count=args.count, rng=rng)
    elif args.mode == "congestion":
        regions = sample_congestion_spots(utility_map, count=args.count, rng=rng)
    else:  # 顶端。
        regions = top_congestion_spots(utility_map, count=args.count)
    if not regions:
        print("未找到可用取景区")
        return 1
    if not args.labels:
        for r in regions:
            r["label"] = None
    print(f"取景 {len(regions)} 处区域（mode={args.mode}）")

    def render_region(region, size):
        aa_rate = 2 if args.aa else 1
        renderer = _build_renderer(
            style, size, args.layers, tile=args.tile_size,
            memory_friendly=args.memory_friendly,
    disk_backing=bool(getattr(args, "disk_backing", False)),
            no_watermark=True,  # 单格不画水印，拼图完成后统一画
            watermark_text=getattr(args, "watermark_text", None),
            rotate=getattr(args, "rotate", 0),
        )
        return renderer.render(mapdata, region=region, aa_downsample=aa_rate,
                               session=session)

    def progress(pct: float, msg: str) -> None:
        print(f"  [{pct * 100:5.1f}%] {msg}")

    if args.compare:
        if len(regions) != 1:
            print("错误：--compare 交通对比仅支持单张（--count 1）")
            return 1
        if not has_traffic(utility_map):
            print("错误：交通对比需要交通快照（未内嵌且未指定 --traffic）")
            return 1
        reg = regions[0]["region"]
        img_on = render_region(reg, args.tile)
        off_layers = _layers_no_traffic(getattr(args, "layers", None))
        renderer_off = _build_renderer(
            style, args.tile, off_layers, tile=args.tile_size,
            memory_friendly=args.memory_friendly,
    disk_backing=bool(getattr(args, "disk_backing", False)),
            no_watermark=True,
            watermark_text=getattr(args, "watermark_text", None),
            rotate=getattr(args, "rotate", 0),
        )
        img_off = renderer_off.render(
            mapdata, region=reg, aa_downsample=2 if args.aa else 1,
            session=session,
        )
        image = compose_compare(img_on, img_off)
    else:
        image = render_spot_mosaic(
            regions, render_region, tile_size=args.tile, progress=progress
        )
    print(f"拼图尺寸 {image.size}")
    # 拼图完成后在最顶层统一画一次水印（小图阶段已禁用，避免每个格子都带水印）
    if not getattr(args, "no_watermark", False):
        from csmodernmap.layers.watermark_layer import WatermarkLayer

        WatermarkLayer.draw_on_image(
            image, style, utility_map,
            render_scale=image.size[0] / 4096,
            custom_text=getattr(args, "watermark_text", None),
        )
    export.export_png(image, args.output)
    print(f"完成: {args.output} (总耗时 {time.time() - t0:.1f}s)")
    return 0




def cmd_export_web(args: argparse.Namespace) -> int:
    """导出 MapLibre 单文件网页地图 HTML（资源全部内嵌，双击即看）。"""
    if not _check_file(args.file):
        return 1
    from csmodernmap import parser as map_parser
    t0 = time.time()
    print(f"解析 {args.file} ...")
    mapdata = map_parser.parse_document(args.file)

    print(f"加载主题 {args.style} ...")
    style = _load_style(args)
    if style is None:
        return 1

    print("生成网页地图（MapLibre 单文件，资源全部内嵌）...")
    from csmodernmap.web_export_v2 import export_web
    out = export_web(
        mapdata, style, city=mapdata.city_name,
        out_path=args.output,
        options={
            "visibility": {"poi": not args.no_poi},
            "rotation": args.rotation,
            **({"revision": args.revision} if args.revision is not None else {}),
        },
        callbacks={"progress": _cli_progress},
    )
    out = Path(args.output)
    mb = out.stat().st_size / 1024 / 1024
    print(
        f"完成: {out} ({mb:.1f} MB, 总耗时 {time.time() - t0:.1f}s)\n"
        f"直接用浏览器打开该 HTML 即可缩放浏览（MapLibre 矢量，零服务零联网）"
    )
    return 0


def cmd_list_styles(args: argparse.Namespace) -> int:
    """列出可用主题。"""
    from csmodernmap._paths import resource_dir
    from csmodernmap.style import load_styles_dir

    styles_dir = resource_dir("styles")
    styles = load_styles_dir(str(styles_dir))
    print(f"可用主题（共 {len(styles)} 个）：")
    print()
    for name, style in styles.items():
        friendly = style.get("name", "") or ""
        desc = style.get("description", "") or ""
        # 文件名 + 中文名 + 描述，对齐显示
        title = f"  {name}"
        if friendly:
            title += f"  · {friendly}"
        print(title)
        if desc:
            print(f"      {desc}")
    print()
    print("用法：python main.py render <存档> --style <主题名>")
    return 0


def cmd_engine(_args: argparse.Namespace) -> int:
    """运行 WinUI 的逐行 JSON 引擎；标准输出专供协议帧。"""
    from csmodernmap.winui_engine import main as engine_main

    return engine_main()


def _app_version() -> str:
    try:
        from csmodernmap import __version__

        return str(__version__)
    except Exception:
        return "unknown"


def cmd_self_check(args: argparse.Namespace) -> int:
    """验证打包引擎导入及所需运行时资源。"""
    import importlib
    import json
    from csmodernmap._paths import bundle_root

    modules = ["csmodernmap.winui_engine", "csmodernmap.winui_workflows",
               "csmodernmap.theme_metadata", "csmodernmap.web_export_v2"]
    for name in modules:
        importlib.import_module(name)
    root = bundle_root()
    resources = ["styles", "resources/i18n", "viewer/dist/index.html",
                 "csmodernmap/services/poi/poi_mapping.yaml",
                 "csmodernmap/services/poi/building_type_inventory.yaml",
                 "csmodernmap/labeling/rules.yaml"]
    missing = [name for name in resources if not (root / name).exists()]
    report = {"version": _app_version(), "ok": not missing,
              "modules": modules, "missing_resources": missing}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    print(text)
    return 1 if missing else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="CSLModernMap 渲染器：解析《城市天际线》存档并渲染地图图片",
        epilog=(
            "示例：\n"
            "  python main.py list-styles                  # 查看可用主题\n"
            "  python main.py parse 存档.mmap            # 查看存档数据统计\n"
            "  python main.py render 存档.mmap -o out.png --size 4096 --aa\n"
            "  python main.py render 存档.mmap --layers 地形,水域,道路   # 只渲指定图层\n"
            "  python main.py spot 存档.mmap --mode random --count 12     # 随机特写 12 宫格拼图\n"
            "  python main.py spot 存档.mmap --mode top --count 12        # 最堵路段 Top12 榜单\n"
            "  python main.py self-check                  # 自检：共享引擎与资源检查\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--lang", choices=AVAILABLE_LANGS, help="界面语言（覆盖已保存的选择）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_engine = sub.add_parser("engine", help="WinUI JSON-lines engine process")
    p_engine.set_defaults(func=cmd_engine)

    p_self_check = sub.add_parser(
        "self-check",
        help="导入自检：验证共享引擎与资源在当前环境能否加载（打包后必跑）",
    )
    p_self_check.add_argument(
        "--report", default=None, metavar="PATH",
        help="自检报告写入路径（默认写到程序目录的 self-check.json）",
    )
    p_self_check.set_defaults(func=cmd_self_check)

    p_parse = sub.add_parser("parse", help="解析存档并输出数据统计")
    p_parse.add_argument("file", help=".mmap 或 .mmap.gz 文件路径")
    p_parse.set_defaults(func=cmd_parse)

    p_render = sub.add_parser("render", help="渲染地图为 PNG")
    p_render.add_argument("file", help=".mmap 或 .mmap.gz 文件路径")
    p_render.add_argument("--style", default="amap", help="主题名（默认 amap，可用 list-styles 查看）")
    p_render.add_argument("--size", type=int, default=4096, help="输出边长像素（默认 4096）")
    p_render.add_argument(
        "--detail", default="auto",
        choices=["auto", "overview", "standard", "detailed"],
        help="地图内容密度（默认 auto），与 --size 独立",
    )
    p_render.add_argument("--aa", action="store_true", help="抗锯齿：按 2× 尺寸渲染后下采样；分块模式每块独立 AA，内存恒定")
    p_render.add_argument(
        "--disk-backing", action="store_true",
        help="磁盘缓存：超大图合并画布落盘（降内存，适合 16K/32K 大图）",
    )
    p_render.add_argument(
        "--memory-friendly", "--mf",
        dest="memory_friendly", action="store_true",
        help="内存友好模式：分辨率>6K 自动分块渲染，峰值内存从 (size^2) 降到 (tile^2)；默认关闭（整图渲染）。",
    )
    p_render.add_argument(
        "--tile-size",
        type=int,
        default=0,
        help="分块渲染瓦片边长像素（0=自动：>6K 自动分 4K 瓦片）。"
             "越小越省内存，但总耗时略增。推荐 1024/2048/4096。"
             "需同时开启 --memory-friendly 或显式设置非 0 值才分块。",
    )
    p_render.add_argument(
        "--layers",
        default=None,
        help="只渲染指定图层（逗号分隔，中/英文均可），不填=全部默认图层。"
             "可选：地形/terrain, 建成区/built_area, 用地类型/land_use, 水域/water, "
             "道路/road, 交通/traffic, 建筑/building, 区划/district, 功能区/zone, "
             "公共交通/transit（兼容地铁/metro）, POI图标/poi, 文字/label, 水印/watermark。"
             "（用地类型/区划/功能区为独立功能，需显式指定才会渲染）",
    )
    p_render.add_argument(
        "--no-poi-names",
        action="store_true",
        help="关闭 POI 图标右侧的随机地名文字",
    )
    p_render.add_argument(
        "--no-watermark",
        action="store_true",
        help="关闭左下角水印",
    )
    p_render.add_argument(
        "--watermark-text",
        default=None,
        help="水印追加自定义文字（如城市名备注）",
    )
    p_render.add_argument(
        "--rotate",
        type=int,
        default=0,
        choices=[0, 90, 180, 270],
        help="输出顺时针旋转角度（0/90/180/270，默认 0）；"
             "文字与图标在旋转后的坐标系中重新渲染，而非对成品图后处理旋转",
    )
    p_render.add_argument(
        "--traffic",
        default=None,
        help="（可选，覆盖优先）CSLModernMap 外部交通快照 JSON 路径。v4.0+ 的 .mmap 已内嵌交通快照，\n"
             "无需再指定本参数；显式指定时会覆盖 csmodernmap 内嵌数据用于拥堵色带渲染。",
    )
    p_render.add_argument(
        "--station-fix", default=None, metavar="JSON",
        help="站点 Stop 锚定修正侧车路径；不指定时自动读取存档旁的 .stations.json",
    )
    p_render.add_argument(
        "--region",
        default=None,
        help="渲染区域 x0,z0,x1,z1（游戏坐标，可选；不填渲染整张地图）",
    )
    p_render.add_argument("-o", "--output", default="output.png", help="输出文件路径")
    p_render.add_argument("--font-scale", type=float, default=1.0,
                   help="全局字号倍率（如 1.2；1.0=不缩放，作用于渲染文字/标签）")
    p_render.set_defaults(func=cmd_render)

    p_transit = sub.add_parser(
        "transit",
        help="公共交通图：全图 → 淡白遮罩 → 叠加全部公共交通线路/站点")
    p_transit.add_argument("file", help=".mmap 或 .mmap.gz 文件路径")
    p_transit.add_argument("--style", default="amap", help="主题名（默认 amap）")
    p_transit.add_argument("--size", type=int, default=4096,
                           help="输出边长像素（默认 4096）")
    p_transit.add_argument(
        "--detail", default="auto",
        choices=["auto", "overview", "standard", "detailed"],
        help="底图内容密度（默认 auto），交通线路叠加始终完整",
    )
    p_transit.add_argument("--aa", action="store_true",
                           help="抗锯齿：按 2× 尺寸渲染后下采样")
    p_transit.add_argument("--memory-friendly", "--mf",
                           dest="memory_friendly", action="store_true",
                           help="内存友好模式：超大图自动分块渲染")
    p_transit.add_argument("--tile-size", type=int, default=0,
                           help="分块瓦片边长（0=自动）")
    p_transit.add_argument("--disk-backing", action="store_true",
                           help="磁盘缓存：底图大图合并落盘降内存")
    p_transit.add_argument(
        "--layers", default=None,
        help="底图图层（逗号分隔；交通线路 metro 自动剔除）。不填=全部默认图层",
    )
    p_transit.add_argument(
        "--transit-types", default=None,
        help="公共交通线路类型（逗号分隔，如 Metro,Train,Ship,Bus）。"
             "默认：注册表中的全部公共交通类型",
    )
    p_transit.add_argument(
        "--transit-mask", type=float, default=0.5,
        help="淡白遮罩强度 0~1（默认 0.5）",
    )
    p_transit.add_argument("--no-watermark", action="store_true",
                           help="关闭左下角水印")
    p_transit.add_argument("--watermark-text", default=None,
                           help="水印追加自定义文字")
    p_transit.add_argument("--rotate", type=int, default=0,
                           choices=[0, 90, 180, 270],
                           help="输出顺时针旋转角度（0/90/180/270）")
    p_transit.add_argument("--traffic", default=None,
                           help="（可选）外部交通快照 JSON 路径")
    p_transit.add_argument(
        "--station-fix", default=None, metavar="JSON",
        help="站点 Stop 锚定修正侧车路径；不指定时自动读取存档旁的 .stations.json",
    )
    p_transit.add_argument("--region", default=None,
                           help="渲染区域 x0,z0,x1,z1（可选）")
    p_transit.add_argument("-o", "--output", default="transit.png",
                           help="输出文件路径")
    p_transit.add_argument("--font-scale", type=float, default=1.0,
                           help="全局字号倍率")
    p_transit.set_defaults(func=cmd_transit)

    p_metro = sub.add_parser(
        "metro",
        help="纯地铁示意图：八方向线路、站点、站名、图例与城市标题",
    )
    p_metro.add_argument("file", help=".mmap 或 .mmap.gz 文件路径")
    p_metro.add_argument("--size", type=int, default=0,
                         help="输出长边像素（0=按站点数量自动，默认 0）")
    p_metro.add_argument("--aa", action="store_true",
                         help="抗锯齿：按 2× 尺寸渲染后下采样")
    p_metro.add_argument("--metro-types", default=None,
                         help="线路类型（逗号分隔；默认 Metro,Train,Monorail）")
    p_metro.add_argument("--rotate", type=int, default=0,
                         choices=[0, 90, 180, 270],
                         help="输出顺时针旋转角度（0/90/180/270）")
    p_metro.add_argument("-o", "--output", default="metro.png",
                         help="输出文件路径")
    p_metro.add_argument("--font-scale", type=float, default=1.0,
                         help="全局字号倍率")
    p_metro.add_argument(
        "--memory-friendly", action="store_true",
        help="大图分块渲染并降低峰值内存，适合 8K 以上输出",
    )
    p_metro.add_argument(
        "--disk-backing", action="store_true",
        help="使用磁盘画布合并大图，进一步降低内存占用",
    )
    p_metro.add_argument(
        "--tile-size", type=int, default=0,
        help="分块瓦片边长像素（0=自动；推荐 1024/2048/4096）",
    )
    p_metro.add_argument(
        "--no-facilities", action="store_true",
        help="不绘制机场/铁路/码头接驳设施图标（默认绘制）",
    )
    p_metro.add_argument(
        "--station-fix", default=None, metavar="JSON",
        help="站点 Stop 锚定修正侧车路径；不指定时自动读取存档旁的 .stations.json",
    )
    p_metro.add_argument(
        "--facilities-scale", type=float, default=1.0,
        help="接驳设施图标大小倍率（0.5~2.0，默认 1.0）",
    )
    p_metro.set_defaults(func=cmd_metro)

    p_random = sub.add_parser("random", help="随机特写：随机渲染一块城市建成区")
    p_random.add_argument("file", help=".mmap 或 .mmap.gz 文件路径")
    p_random.add_argument("--style", default="amap", help="主题名（默认 amap）")
    p_random.add_argument("--size", type=int, default=2048, help="输出边长像素（默认 2048）")
    p_random.add_argument("--aa", action="store_true", help="抗锯齿：按 2× 尺寸渲染后下采样")
    p_random.add_argument("--seed", type=int, default=None, help="随机种子（固定可复现）")
    p_random.add_argument(
        "--disk-backing", action="store_true",
        help="磁盘缓存：超大图合并画布落盘（降内存，适合 16K/32K 大图）",
    )
    p_random.add_argument(
        "--memory-friendly", "--mf",
        dest="memory_friendly", action="store_true",
        help="内存友好模式：大尺寸自动分块，峰值内存更小。",
    )
    p_random.add_argument(
        "--tile-size",
        type=int,
        default=0,
        help="分块瓦片边长像素（0=自动），同 render --tile-size。",
    )
    p_random.add_argument(
        "--layers",
        default=None,
        help="只渲染指定图层（逗号分隔），不填=全部。同 render --layers。",
    )
    p_random.add_argument(
        "--traffic",
        default=None,
        help="（可选，覆盖优先）CSLModernMap 外部交通快照 JSON 路径。v4.0+ 的 .mmap 已内嵌交通快照，\n"
             "无需再指定本参数；显式指定时覆盖内嵌数据。",
    )
    p_random.add_argument(
        "--no-poi-names",
        action="store_true",
        help="关闭 POI 图标右侧的随机地名文字",
    )
    p_random.add_argument(
        "--no-watermark",
        action="store_true",
        help="关闭左下角水印",
    )
    p_random.add_argument(
        "--watermark-text",
        default=None,
        help="水印追加自定义文字（如城市名备注）",
    )
    p_random.add_argument(
        "--rotate",
        type=int,
        default=0,
        choices=[0, 90, 180, 270],
        help="输出顺时针旋转角度（0/90/180/270，默认 0）；文字与图标在旋转后的坐标系中重新渲染",
    )
    p_random.add_argument("-o", "--output", default="random_spot.png", help="输出文件路径")
    p_random.add_argument("--font-scale", type=float, default=1.0,
                   help="全局字号倍率（如 1.2；1.0=不缩放，作用于渲染文字/标签）")
    p_random.set_defaults(func=cmd_random)

    p_int = sub.add_parser("interchange", help="立交特写：识别立交桥并逐处特写拼图")
    p_int.add_argument("file", help=".mmap 或 .mmap.gz 文件路径")
    p_int.add_argument("--style", default="amap", help="主题名（默认 amap）")
    p_int.add_argument("--tile", type=int, default=1024, help="单格立交图边长像素（拼图每格，默认 1024）")
    p_int.add_argument("--top", type=int, default=16, help="最多拼入的立交数（默认 16）")
    p_int.add_argument("--aa", action="store_true", help="抗锯齿：按 2× 尺寸渲染后下采样")
    p_int.add_argument(
        "--disk-backing", action="store_true",
        help="磁盘缓存：超大图合并画布落盘（降内存，适合 16K/32K 大图）",
    )
    p_int.add_argument(
        "--memory-friendly", "--mf",
        dest="memory_friendly", action="store_true",
        help="内存友好模式：单格尺寸大时自动分块。",
    )
    p_int.add_argument(
        "--tile-size",
        type=int,
        default=0,
        help="单格立交内部的分块瓦片（0=自动），仅当 --tile 极大时需要。",
    )
    p_int.add_argument(
        "--layers",
        default=None,
        help="只渲染指定图层（逗号分隔），不填=全部。同 render --layers。",
    )
    p_int.add_argument(
        "--traffic",
        default=None,
        help="（可选，覆盖优先）CSLModernMap 外部交通快照 JSON 路径。v4.0+ 的 .mmap 已内嵌交通快照，\n"
             "无需再指定本参数；显式指定时覆盖内嵌数据。",
    )
    p_int.add_argument(
        "--no-poi-names",
        action="store_true",
        help="关闭 POI 图标右侧的随机地名文字",
    )
    p_int.add_argument(
        "--no-watermark",
        action="store_true",
        help="关闭左下角水印",
    )
    p_int.add_argument(
        "--watermark-text",
        default=None,
        help="水印追加自定义文字（如城市名备注）",
    )
    p_int.add_argument(
        "--rotate",
        type=int,
        default=0,
        choices=[0, 90, 180, 270],
        help="输出顺时针旋转角度（0/90/180/270，默认 0）；文字与图标在旋转后的坐标系中重新渲染",
    )
    p_int.add_argument("-o", "--output", default="interchanges.png", help="输出文件路径")
    p_int.add_argument("--font-scale", type=float, default=1.0,
                   help="全局字号倍率（如 1.2；1.0=不缩放，作用于渲染文字/标签）")
    p_int.set_defaults(func=cmd_interchange)

    p_spot = sub.add_parser(
        "spot",
        help="特写拼图：随机/堵车取景多块区域，拼成网格图（数量、种子、标注、交通对比可调）",
    )
    p_spot.add_argument("file", help=".mmap 或 .mmap.gz 文件路径")
    p_spot.add_argument("--style", default="amap", help="主题名（默认 amap）")
    p_spot.add_argument(
        "--mode", default="random",
        choices=["random", "congestion", "top"],
        help="取景主题：random=按建筑密度随机，congestion=按拥堵加权随机，"
             "top=最堵路段Top榜单（默认 random；后两者需交通快照）",
    )
    p_spot.add_argument("--count", type=int, default=12,
                        help="取景数量（默认 12；1 为单张大图）")
    p_spot.add_argument("--tile", type=int, default=1024,
                        help="单格特写边长像素（默认 1024）")
    p_spot.add_argument("--seed", type=int, default=None,
                        help="随机种子（固定可复现）")
    p_spot.add_argument("--aa", action="store_true",
                        help="抗锯齿：按 2× 尺寸渲染后下采样")
    p_spot.add_argument("--labels", dest="labels", action="store_true", default=True,
                        help="每格画标注（默认开：随机标编号，堵车标路名+拥堵指数）")
    p_spot.add_argument("--no-labels", dest="labels", action="store_false",
                        help="不画每格标注")
    p_spot.add_argument("--compare", action="store_true",
                        help="交通对比：同区有/无交通并排（仅 --count 1，需交通快照）")
    p_spot.add_argument(
        "--disk-backing", action="store_true",
        help="磁盘缓存：超大图合并画布落盘（降内存，适合 16K/32K 大图）",
    )
    p_spot.add_argument(
        "--memory-friendly", "--mf",
        dest="memory_friendly", action="store_true",
        help="内存友好模式：单格尺寸大时自动分块。",
    )
    p_spot.add_argument(
        "--tile-size",
        type=int,
        default=0,
        help="单格特写内部的分块瓦片（0=自动），仅当 --tile 极大时需要。",
    )
    p_spot.add_argument(
        "--layers",
        default=None,
        help="只渲染指定图层（逗号分隔），不填=全部。同 render --layers。",
    )
    p_spot.add_argument(
        "--traffic",
        default=None,
        help="（可选，覆盖优先）CSLModernMap 外部交通快照 JSON 路径。v4.0+ 的 .mmap 已内嵌交通快照，\n"
             "无需再指定本参数；显式指定时覆盖内嵌数据。",
    )
    p_spot.add_argument(
        "--no-poi-names",
        action="store_true",
        help="关闭 POI 图标右侧的随机地名文字",
    )
    p_spot.add_argument(
        "--no-watermark",
        action="store_true",
        help="关闭左下角水印",
    )
    p_spot.add_argument(
        "--watermark-text",
        default=None,
        help="水印追加自定义文字（如城市名备注）",
    )
    p_spot.add_argument(
        "--rotate",
        type=int,
        default=0,
        choices=[0, 90, 180, 270],
        help="输出顺时针旋转角度（0/90/180/270，默认 0）；文字与图标在旋转后的坐标系中重新渲染",
    )
    p_spot.add_argument("-o", "--output", default="spots.png", help="输出文件路径")
    p_spot.add_argument("--font-scale", type=float, default=1.0,
                   help="全局字号倍率（如 1.2；1.0=不缩放，作用于渲染文字/标签）")
    p_spot.set_defaults(func=cmd_spot)

    p_list = sub.add_parser("list-styles", help="列出可用主题")
    p_list.set_defaults(func=cmd_list_styles)

    p_export = sub.add_parser(
        "export-web", help="导出 MapLibre 单文件网页地图 HTML（资源全部内嵌，双击即看）"
    )
    p_export.add_argument("file", help=".mmap 或 .mmap.gz 存档文件路径")
    p_export.add_argument("--style", default="amap", help="主题名（默认 amap，可用 list-styles 查看）")
    p_export.add_argument(
        "--no-poi", action="store_true",
        help="不导出 POI 图标层（默认导出）",
    )
    p_export.add_argument("-o", "--output", default="map.html", help="输出 HTML 文件路径（默认 map.html）")
    p_export.add_argument("--revision", type=int, default=None, help=argparse.SUPPRESS)
    p_export.add_argument(
        "--rotation", type=int, choices=(0, 90, 180, 270), default=0,
        help="输出旋转角度：顺时针旋转地图内容（默认 0，只改方向不改数据）",
    )
    p_export.add_argument("--font-scale", type=float, default=1.0,
                   help="全局字号倍率（如 1.2；1.0=不缩放，作用于渲染文字/标签）")
    p_export.set_defaults(func=cmd_export_web)

    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        parser.print_help()
        return 0

    args = parser.parse_args(argv)
    # 给不接受这些参数的子命令（gui/parse/list-styles）补齐默认值
    defaults = {
        "layers": None,
        "tile_size": 0,
        "memory_friendly": False,
        "show_phone_frame": False,
        "show_traffic_legend": False,
        "location": None,
        "scale_meters": 500,
        "title": None,
    }
    for attr, default in defaults.items():
        if not hasattr(args, attr):
            setattr(args, attr, default)
    set_lang(detect_lang(cli_lang=args.lang))
    return args.func(args)


def _startup_error_candidates() -> list[Path]:
    """启动错误日志的候选落点：先程序目录，再用户本地目录。"""
    from csmodernmap._paths import writable_dir

    candidates = [writable_dir() / "startup-error.log"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "CSLModernMap" / "logs" / "startup-error.log")
    candidates.append(Path.home() / ".cslmodernmap" / "logs" / "startup-error.log")
    return candidates


def _write_startup_error(text: str) -> Path | None:
    for path in _startup_error_candidates():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(text)
            return path
        except OSError:
            continue
    return None


def _has_console() -> bool:
    if sys.platform != "win32":
        return sys.stderr is not None
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return False


def _notify_startup_error(message: str) -> None:
    """没有控制台时用原生消息框把失败暴露出来，而不是静默退出。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None, message, "CSLModernMap 启动失败", 0x10
        )
    except Exception:
        pass


def _startup_error_hook(exc_type, exc_value, exc_tb) -> None:
    """记录启动异常，并在无控制台时显示日志位置。"""
    import traceback

    from csmodernmap._paths import is_bundled

    detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    header = (
        f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动失败 =====\n"
        f"版本 {_app_version()} / Python {sys.version.split()[0]}"
        f" / 打包运行 {is_bundled()}\n"
        f"程序 {sys.executable}\n"
        f"命令行 {' '.join(sys.argv)}\n\n"
    )
    try:
        sys.stderr.write(header + detail)
    except Exception:
        pass
    log_path = _write_startup_error(header + detail)
    if _has_console():
        return
    _notify_startup_error(
        "CSLModernMap 渲染器启动失败。\n\n"
        f"{exc_type.__name__}: {exc_value}\n\n"
        f"完整信息：\n{log_path if log_path else '（日志写入失败）'}"
    )


if __name__ == "__main__":
    # Windows 中基于 spawn 的进程池和独立 Nuitka 构建需要冻结支持，子进程不能重入界面入口。
    import multiprocessing

    multiprocessing.freeze_support()
    # 无控制台的发版产物里，未处理异常以前只会让进程安静地以退出码 1 结束。
    sys.excepthook = _startup_error_hook
    sys.exit(main())
