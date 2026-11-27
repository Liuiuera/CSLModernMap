# CSLModernMap for macOS

AppKit 和 WKWebView 前端与 Windows 查看器共用 Python 引擎、地图预览及 RMP 编辑器。当前构建目标为 Intel x86_64、macOS 13 及以上。

## 开发构建

在仓库根目录安装 Python 3.13、Node.js 和 Xcode 命令行工具，然后运行以下命令。

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r dev/CSLModernMapRenderer.macOS/requirements-macos13.lock
(cd dev/CSLModernMapRenderer/viewer && npm ci && npm run typecheck && CSLMM_MACOS_BUILD=1 npm run build)
(cd dev/third_party/rmp && npm ci && CSLMM_MACOS_BUILD=1 npm run build:embed)
CSLMM_ENGINE_PYTHON="$PWD/.venv/bin/python" bash dev/CSLModernMapRenderer.macOS/scripts/build-dev.sh
```

脚本输出应用路径。开发应用通过指定的解释器运行当前仓库中的共用引擎。可用 `CSLMM_NATIVE_APP_DIR` 指定输出位置，`CSLMM_WORKSPACE_DIR` 指定本地构建缓存和默认输出的外部工作区；默认使用仓库根目录。应用接受 `--open <城市文件>`，也支持 CS1 模组传入的 `gui <城市文件>`。模组按 `/Applications/CSLModernMap Renderer.app` 查找程序。

## 本地化

简体中文为原文，通用文案优先使用 WinUI 资源。macOS 专属翻译维护于 `Resources/NativeLocalization.tsv`，等义文案对应关系维护于 `Resources/NativeLocalizationAliases.tsv`。更新资源后运行：

```sh
python3 dev/CSLModernMapRenderer.macOS/scripts/generate-winui-strings.py
python3 dev/CSLModernMapRenderer.macOS/scripts/generate-native-localization.py
python3 dev/CSLModernMapRenderer.macOS/scripts/generate-native-localization.py --check
```

不要直接编辑生成的 JSON 文件。

## 集成检查和打包

`macOS CI` 只编译 Intel 客户端，并执行基础引擎和原生逻辑检查，不影响 Windows 的构建任务。完整打包仍按下面的步骤进行。

`build-dev.sh --with-checks` 生成可执行 `--smoke <城市文件> <结果目录>` 的测试应用。测试时用 `CSLMM_NATIVE_DATA_DIR` 指定独立配置目录。

3D 原生专项检查需要先构建带只读相机调试接口的 Viewer：

```sh
(cd dev/CSLModernMapRenderer/viewer && CSLMM_MACOS_BUILD=1 VITE_CSLMM_NATIVE_CHECKS=1 npm run build)
bash dev/CSLModernMapRenderer.macOS/scripts/build-dev.sh --with-checks
```

测试 App 使用 `--smoke <城市文件> <结果目录> --camera-probe`，通过实际 WKWebView 控件切换 2D/3D，并写出 `camera.json`。正式打包前须用不含 `VITE_CSLMM_NATIVE_CHECKS` 的常规命令重新构建 Viewer。

使用同架构、可重定位的 Python 3.13 运行时打包。以下命令在仓库根目录执行，`runtime` 为该运行时的路径。发行依赖必须按 macOS 13 选择 Intel wheel，而不使用较新构建机的默认 wheel；aggdraw 由源码按 macOS 13 编译。DMG 布局工具仅安装到打包环境。

```sh
export CSLMM_NATIVE_APP_DIR="$PWD/artifacts/macos/CSLModernMap Renderer.app"
packages="$PWD/artifacts/macos/release-deps"
.venv/bin/python -m pip install -r dev/CSLModernMapRenderer.macOS/requirements-dmg.lock
.venv/bin/python dev/CSLModernMapRenderer.macOS/scripts/install-release-deps.py --target "$packages"
.venv/bin/python dev/CSLModernMapRenderer.macOS/scripts/package-app.py --app-dir "$CSLMM_NATIVE_APP_DIR" --runtime "$runtime" --site-packages "$packages"
python3 dev/CSLModernMapRenderer.macOS/scripts/audit-bundle.py "$CSLMM_NATIVE_APP_DIR" --output "$PWD/artifacts/macos/audit.json"
CSLMM_DMG_PYTHON="$PWD/.venv/bin/python" bash dev/CSLModernMapRenderer.macOS/scripts/make-dmg.sh
```

打包只保留引擎所需的 QtCore、QtGui、QtDBus、平台插件和 Shiboken，不改动开发环境。DMG 沿用旧版 Finder 安装示意图，采用 ULMO 压缩，验证签名、挂载布局以及 100,000,000 字节上限，生成 SHA256。

DMG 内为 ad-hoc 签名应用。沿用已有 Bundle ID，正式版配置位于 `~/Library/Application Support/CSLModernMap/`，语言偏好沿用其 `preferences/` 子目录。已有城市旁的配置优先用于该城市；其他城市使用共享配置。主题格式迁移保留原文件备份，不从原生测试版目录覆盖正式版配置。
