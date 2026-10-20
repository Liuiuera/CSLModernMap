# CSLModernMap

[中文](#中文) · [English](#english)

## 中文

### 简介

CSLModernMap 渲染器用于查看和绘制《城市：天际线》和《城市：天际线 II》导出的城市地图。

《城市：天际线 II》的游戏内导出模组源码见 [CSLModernMap-CodeMod](https://github.com/Liuiuera/CSLModernMap-CodeMod)。模组负责导出城市数据，本仓库的查看器与渲染器负责读取数据、显示地图和导出结果。

Windows 查看器使用 WinUI，支持 `.cmm`、`.cmm.gz` 及历史 `.mmap`、`.mmap.gz` 文件。可以导出 PNG 图片、HTML 网页地图、RMG 和 RMP 线路项目；内嵌的 Rail Map Painter（RMP）用于编辑线路图。

本项目包含由 OpenAI Codex 辅助生成的代码。

### 开始使用

1. 安装并启用对应游戏的 CSLModernMap Mod，载入城市。
2. 在 Mod 的设置页面选择导出城市数据，或选择“导出并打开”。
3. 在查看器中打开导出的文件，调整主题、图层和显示范围，再选择所需的导出格式。

使用说明见[入门指南](docs/wiki/Home.md)。

自行编译请参阅[构建说明](docs/构建说明.md)。Windows 查看器支持 x64、Windows 10 19041 及以上版本。

### 项目构造与构建

| 目录 | 内容 |
| --- | --- |
| `dev/CSLModernMapRenderer/winui/` | Windows 查看器 |
| `dev/CSLModernMapRenderer/csmodernmap/` | Python 数据读取、地图渲染与导出引擎 |
| `dev/CSLModernMapRenderer/viewer/` | MapLibre 网页预览 |
| `dev/third_party/rmp/` | 固定版本的 RMP 源码及本地修改 |
| `assets/` | 地图图标与来源声明 |
| `tools/` | 构建、打包、翻译与资源生成工具 |

构建使用 Python 3.13、Node.js 24 和 .NET SDK 10。WinUI 功能区使用 RibbonSpace。依赖许可见[许可说明](LICENSING.md)。

### 版权与许可

CSLModernMap 自有代码版权归 Liuiuera（少糖烧仙草）及相应贡献者所有。本仓库中的自有查看器、引擎和工具采用 GPL-3.0-only。第三方代码、字体、图标和衍生部分保留各自的版权与许可。

许可范围见 [LICENSING.md](LICENSING.md)，GPL 正文见 [LICENSE](LICENSE)，第三方说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。发行时需保留各组件的完整许可，并核对最终包内的实际依赖。

《城市：天际线》及相关名称、标识归各自权利人所有。本项目是社区项目，与游戏开发商、发行商及交通运营机构无隶属或背书关系。

### 鸣谢

感谢 CSLMapView 的城市数据导出工作，以及 [Rail Map Painter](https://github.com/railmapgen/rmp) 的线路图编辑器。感谢 MapLibre、PySide6、Shapely、Map Icons、Tabler Icons、Fluent UI System Icons、Noto Sans 及其他依赖项目的作者，也感谢提供翻译、问题反馈和城市数据验证的贡献者。许可与来源详见第三方说明。

## English

### Introduction

The CSLModernMap renderer displays and renders city maps exported from Cities: Skylines and Cities: Skylines II.

The source for the Cities: Skylines II in-game export mod is maintained in [CSLModernMap-CodeMod](https://github.com/Liuiuera/CSLModernMap-CodeMod). The mod exports city data; the viewer and renderer in this repository read that data, display maps and export the results.

The Windows viewer uses WinUI and reads `.cmm`, `.cmm.gz`, and legacy `.mmap` and `.mmap.gz` files. Outputs include PNG images, HTML web maps, and RMG and RMP transit projects. An embedded Rail Map Painter (RMP) editor lets you edit transit diagrams.

This project contains code generated with assistance from OpenAI Codex.

### Getting started

1. Install and enable the CSLModernMap mod for your game, then load a city.
2. Open the mod's settings and export the city data, or choose the export-and-open action.
3. Open the exported file in the viewer, adjust the theme, layers and map extent, then choose an output format.

See the [user guide](docs/wiki/Getting-Started.md) for everyday use.

See [Build instructions](docs/构建说明.md) (Chinese) to compile the application. The Windows viewer supports x64 Windows 10 build 19041 or later.

### Structure and building

| Directory | Contents |
| --- | --- |
| `dev/CSLModernMapRenderer/winui/` | Windows viewer |
| `dev/CSLModernMapRenderer/csmodernmap/` | Python data reader, map renderer and export engine |
| `dev/CSLModernMapRenderer/viewer/` | MapLibre web preview |
| `dev/third_party/rmp/` | Pinned RMP source and local modifications |
| `assets/` | Map icons and provenance notices |
| `tools/` | Build, packaging, translation and resource generation tools |

Building uses Python 3.13, Node.js 24 and .NET SDK 10. The WinUI ribbon uses RibbonSpace. See [Licensing](LICENSING.md) for dependency terms.

### Copyright and licensing

CSLModernMap-owned code is copyright Liuiuera (少糖烧仙草) and its respective contributors. The project-owned viewer, engine and tools in this repository use GPL-3.0-only. Third-party code, fonts, icons and derived portions retain their respective copyrights and licenses.

See [LICENSING.md](LICENSING.md) for license scope, [LICENSE](LICENSE) for the GPL text, and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for third-party notices. Distributions must retain each component's full license and be checked against their actual bundled dependencies.

Cities: Skylines and related names and marks belong to their respective owners. This community project is not affiliated with or endorsed by the game developers, publishers or transit operators.

### Acknowledgements

Thanks to CSLMapView for its city export work and [Rail Map Painter](https://github.com/railmapgen/rmp) for its transit diagram editor. Thanks also to the authors of MapLibre, PySide6, Shapely, Map Icons, Tabler Icons, Fluent UI System Icons, Noto Sans and other dependencies, and to contributors who provide translations, issue reports and city data validation. See the third-party notices for licenses and provenance.
