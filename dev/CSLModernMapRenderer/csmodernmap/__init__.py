"""CSL 地图渲染器核心包。

基于《城市天际线》存档导出的 CSLMap XML 数据，
解析为 MapData 模型并渲染为 PNG 图片。

模块划分（每个子功能独立）：
- data.py      数据模型定义
- parser.py    XML 解析器
- terrain.py   地形处理（高度图、晕渲）
- style.py     样式配置加载
- layers/      图层渲染器（每个图层一个模块）
- renderer.py  渲染管线
- export.py    导出
"""

# 导入附属模块时保持包版本信息可用。
__version__ = "6.7.2f"

from .detail import DetailMode

__all__ = ["DetailMode"]
