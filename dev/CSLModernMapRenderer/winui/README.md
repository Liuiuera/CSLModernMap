# CSLModernMap Windows 查看器

本目录维护正式渲染器的 Windows WinUI 界面，Python 引擎位于上级 `csmodernmap/`。

从仓库根目录用 `dotnet build dev/CSLModernMapRenderer/winui/CSLModernMapWinUI.csproj -c Release` 构建。网页预览需先完成 npm 构建。功能区采用 RibbonSpace.WinUI（MIT）。

构建完成后直接启动输出目录中的 `CSLModernMapRenderer.exe`。源码模式需要保留仓库结构及已安装的 Python 依赖。

输出目录通过 `assemble.py --publish` 组装 WinUI 和已有 Nuitka 引擎。结果位于 `dist/winui/CSLModernMapRenderer/`，顶层为查看器，`engine/` 内为 Python 引擎。运行时保留完整目录。

协议使用 UTF-8 JSON 行，`hello` 要求 `protocolVersion=1`，请求为 `{id,method,params}`，响应为 `{id,result}` 或 `{id,error}`，异步消息为 `{event,payload}`。预览资源由引擎的本地 HTTP 服务提供。启动日志位于 `%LOCALAPPDATA%/CSLModernMap/logs/winui-startup.log`。

构建方法见[构建说明](../../../docs/构建说明.md)。
