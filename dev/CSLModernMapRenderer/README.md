# CSLModernMap 渲染器

渲染器由 Windows WinUI 界面和 Python 引擎组成。

| 路径 | 职责 |
|---|---|
| `winui/` | WinUI 界面、引擎进程协议与 Windows 组装 |
| `csmodernmap/` | CMM／历史格式读取、共享业务与地图渲染 |
| `resources/`、`styles/` | 翻译、字体、图标与主题 |
| `viewer/` | 共用 MapLibre 网页预览 |
| `main.py` | 地图 CLI、`engine` 协议入口与引擎自检 |
| `build_nuitka.py` | 使用标准 Nuitka 打包 `CSLModernMapEngine.exe` |

界面启动使用 WinUI 可执行文件。构建与资源维护工具位于 `tools/renderer/`。

共享道路几何算法使用 PySide6 的 QtCore／QtGui。

从仓库根执行：

```powershell
python -m pip install -r dev/CSLModernMapRenderer/requirements.txt
python dev/CSLModernMapRenderer/main.py list-styles
python dev/CSLModernMapRenderer/main.py --lang ja list-styles
python dev/CSLModernMapRenderer/main.py render "D:/地图/城市.cmm.gz" --size 4096 --aa -o map.png
python dev/CSLModernMapRenderer/main.py export-web "D:/地图/城市.cmm.gz" -o map.html
python dev/CSLModernMapRenderer/main.py self-check
```

完整编译与源码界面启动见 [构建说明](../../docs/构建说明.md)。
