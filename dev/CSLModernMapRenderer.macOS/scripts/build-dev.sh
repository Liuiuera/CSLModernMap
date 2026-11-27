#!/bin/bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_DIR="$(cd "$SOURCE_DIR/../.." && pwd)"
WORKSPACE_DIR="${CSLMM_WORKSPACE_DIR:-$REPO_DIR}"
build_dir=native-integration
swift_inputs=("$SOURCE_DIR"/Sources/*.swift)
with_checks=false
case "${1:-}" in
    --with-checks)
        build_dir=native-checks
        swift_inputs+=(-D CMM_INTEGRATION_CHECKS "$SOURCE_DIR"/Tests/Integration/*.swift)
        with_checks=true
        ;;
    "") ;;
    *) echo "Usage: $0 [--with-checks]" >&2; exit 2 ;;
esac
APP_DIR="${CSLMM_NATIVE_APP_DIR:-$WORKSPACE_DIR/outputs/$build_dir/CSLModernMap Renderer.app}"
if [[ ! -f "$REPO_DIR/dev/CSLModernMapRenderer/viewer/dist/index.html" ||
      ! -f "$REPO_DIR/dev/third_party/rmp/dist-embed/embed.html" ]]; then
    echo "Build the shared viewer and RMP assets first (CSLMM_MACOS_BUILD=1)." >&2
    exit 1
fi
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources/engine" "$WORKSPACE_DIR/work/native-integration/swift-cache"
# Sync the embedded RMP viewer.
if [ -f "$REPO_DIR/dev/third_party/rmp/dist-embed/embed.html" ]; then
    python3 - "$REPO_DIR" <<'SYNC_RMP'
from pathlib import Path
import shutil
import sys
repo = Path(sys.argv[1])
source = repo / 'dev/third_party/rmp/dist-embed'
target = repo / 'dev/CSLModernMapRenderer/viewer/dist/rmp'
if target.exists():
    shutil.rmtree(target)
shutil.copytree(source, target)
SYNC_RMP
fi
xcrun swiftc -whole-module-optimization "${swift_inputs[@]}" -o "$APP_DIR/Contents/MacOS/CSLModernMap" -target x86_64-apple-macosx13.0 -module-cache-path "$WORKSPACE_DIR/work/native-integration/swift-cache" -framework AppKit -framework WebKit -framework CryptoKit
cp -R "$SOURCE_DIR/Resources/Themes" "$SOURCE_DIR/Resources/TransitLabels" "$APP_DIR/Contents/Resources/"
mkdir -p "$APP_DIR/Contents/Resources/RmpFacilities"
cp "$REPO_DIR/dev/third_party/rmp/public/images/facilities/"*.svg "$APP_DIR/Contents/Resources/RmpFacilities/"
cp "$SOURCE_DIR/Resources/AppIcon.icns" "$APP_DIR/Contents/Resources/"
cp "$SOURCE_DIR/Resources/NativeStrings.json" "$APP_DIR/Contents/Resources/"
cp "$SOURCE_DIR/Resources/WinUIStrings.json" "$APP_DIR/Contents/Resources/"
cp "$SOURCE_DIR/Resources/NativeUILocalization.json" "$APP_DIR/Contents/Resources/"
cp "$SOURCE_DIR/Resources/NativeSchemaStrings.json" "$APP_DIR/Contents/Resources/"
cp "$SOURCE_DIR/Info.plist" "$APP_DIR/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CMMIntegrationChecks bool $with_checks" "$APP_DIR/Contents/Info.plist"
engine_python="${CSLMM_ENGINE_PYTHON:-$REPO_DIR/.venv/bin/python}"
cat > "$APP_DIR/Contents/Resources/engine/run-engine" <<LAUNCH
#!/bin/bash
exec "$engine_python" "$REPO_DIR/dev/CSLModernMapRenderer/main.py" engine
LAUNCH
chmod +x "$APP_DIR/Contents/Resources/engine/run-engine"
codesign --force --sign - "$APP_DIR"
printf '%s\n' "$APP_DIR"
