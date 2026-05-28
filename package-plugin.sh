#!/usr/bin/env bash
# Đóng gói keyword-labeler.plugin từ cowork-plugin/
# Chạy: bash package-plugin.sh
set -euo pipefail

cd "$(dirname "$0")"

# Đồng bộ SKILL.md mới nhất vào cowork-plugin
cp skills/keyword/SKILL.md cowork-plugin/skills/keyword/SKILL.md

OUTPUT="keyword-labeler.plugin"
rm -f "$OUTPUT"

TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT

cp cowork-plugin/.mcp.json          "$TMP/.mcp.json"
cp -r cowork-plugin/.claude-plugin  "$TMP/.claude-plugin"
cp -r cowork-plugin/skills          "$TMP/skills"

cd "$TMP"
zip -r "$OLDPWD/$OUTPUT" . --exclude "*.DS_Store" --exclude "__pycache__/*"

echo "✅ Đã tạo: $OLDPWD/$OUTPUT"
