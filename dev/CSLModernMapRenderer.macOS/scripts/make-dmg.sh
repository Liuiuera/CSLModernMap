#!/bin/bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_DIR="$(cd "$SOURCE_DIR/../.." && pwd)"
WORKSPACE_DIR="${CSLMM_WORKSPACE_DIR:-$REPO_DIR}"
APP_DIR="${CSLMM_NATIVE_APP_DIR:-$WORKSPACE_DIR/outputs/native-integration/CSLModernMap Renderer.app}"
OUTPUT_DIR="$(dirname "$APP_DIR")"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP_DIR/Contents/Info.plist")"
DMG_PATH="$OUTPUT_DIR/CSLModernMap-$VERSION-macOS13-x86_64-test.dmg"
codesign --verify --deep --strict "$APP_DIR"
mkdir -p "$WORKSPACE_DIR/work/native-integration"
TEMP_DIR="$(mktemp -d "$WORKSPACE_DIR/work/native-integration/dmg-stage.XXXXXX")"
trap 'rm -rf "$TEMP_DIR"' EXIT
STAGING_DIR="$TEMP_DIR/contents"
mkdir "$STAGING_DIR"
ditto "$APP_DIR" "$STAGING_DIR/CSLModernMap Renderer.app"
ln -s /Applications "$STAGING_DIR/Applications"
if [[ -f "$OUTPUT_DIR/reports/测试版使用说明.txt" ]]; then
    cp "$OUTPUT_DIR/reports/测试版使用说明.txt" "$STAGING_DIR/"
fi
if ! hdiutil create -volname "CSLModernMap" -srcfolder "$STAGING_DIR" -format UDZO -ov "$DMG_PATH"; then
    # Build the same read-only distribution without attaching a writable disk device.
    hdiutil makehybrid -hfs -hfs-volume-name "CSLModernMap" -o "$TEMP_DIR/source.dmg" "$STAGING_DIR"
    hdiutil convert "$TEMP_DIR/source.dmg" -format UDZO -ov -o "$DMG_PATH"
fi
codesign --force --sign - "$DMG_PATH"
hdiutil verify "$DMG_PATH"
(cd "$OUTPUT_DIR" && shasum -a 256 "$(basename "$DMG_PATH")") > "$DMG_PATH.sha256"
printf '%s\n' "$DMG_PATH"
