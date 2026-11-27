#!/bin/bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_DIR="$(cd "$SOURCE_DIR/../.." && pwd)"
WORKSPACE_DIR="${CSLMM_WORKSPACE_DIR:-$REPO_DIR}"
APP_DIR="${CSLMM_NATIVE_APP_DIR:-$WORKSPACE_DIR/outputs/native-integration/CSLModernMap Renderer.app}"
OUTPUT_DIR="$(dirname "$APP_DIR")"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP_DIR/Contents/Info.plist")"
DMG_PATH="$OUTPUT_DIR/CSLModernMap-$VERSION-macOS13-x86_64-test.dmg"
"${CSLMM_DMG_PYTHON:-python3}" "$SOURCE_DIR/scripts/make-dmg.py" \
    --app "$APP_DIR" --output "$DMG_PATH" --version "$VERSION"
