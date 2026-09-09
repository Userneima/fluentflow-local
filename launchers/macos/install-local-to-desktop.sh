#!/bin/bash
# 在桌面安装「FluentFlow Local.app」：一个可双击的 macOS 应用，显示图标和名称。
#
# 应用内部仍然调用同目录的 FluentFlowLocal.command，启动逻辑、日志位置和失败
# 提示都不变——这里只是把它包成应用，并在启动后把终端窗口收起来。做成 .app 而
# 不是 .command，是为了和这台机器上 VerbaLab / CrystalSay / Glimmer Web 一致。
#
# 之前这个脚本装的是桌面上的「FluentFlow Local.command」。旧文件不会被删除或
# 移动，两者可以共存；确认新应用可用后再自行处理旧的那个。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SOURCE="${SCRIPT_DIR}/FluentFlowLocal.applescript"
LAUNCHER="${SCRIPT_DIR}/FluentFlowLocal.command"
APP_NAME="FluentFlow Local"
DEST="${HOME}/Desktop/${APP_NAME}.app"

fail() { echo "✗ $1" >&2; exit 1; }

[[ -f "$SOURCE" ]] || fail "找不到 $SOURCE"
[[ -f "$LAUNCHER" ]] || fail "找不到 $LAUNCHER"
[[ -f "${REPO}/backend/local_main.py" ]] || fail "在 ${REPO} 下找不到 backend/local_main.py，这不像 FluentFlow 代码目录"
chmod +x "$LAUNCHER"

command -v osacompile >/dev/null 2>&1 || fail "找不到 osacompile（macOS 自带，通常在 /usr/bin）"

# 把代码目录路径写进源码再编译。applet 里不能靠相对路径推断：它被装到桌面，
# 和代码目录没有位置关系。
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
python3 - "$SOURCE" "$WORK/main.applescript" "$REPO" <<'PY'
import sys
src, dst, repo = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(src, encoding="utf-8").read()
if "@@REPO@@" not in text:
    raise SystemExit("源文件里没有 @@REPO@@ 占位符，安装脚本和源文件不同步了")
open(dst, "w", encoding="utf-8").write(text.replace("@@REPO@@", repo))
PY

# 重装时先移走旧的：osacompile 不会覆盖已存在的 .app
if [[ -e "$DEST" ]]; then
	rm -rf "$DEST"
fi
osacompile -o "$DEST" "$WORK/main.applescript" || fail "编译失败"

# 完整性自检：缺任何一件，双击就是一个打不开的图标
for part in "Contents/Info.plist" "Contents/MacOS/applet" "Contents/Resources/Scripts/main.scpt"; do
	[[ -e "${DEST}/${part}" ]] || fail "生成的应用缺少 ${part}"
done
# osacompile 不写 CFBundleIdentifier，而 PlistBuddy 的 Set 对不存在的键会失败，
# 所以先 Add 再 Set。改完 plist 会让 osacompile 刚打的签名对不上，必须重签，
# 否则 Gatekeeper 可能拒绝启动。
PLIST="${DEST}/Contents/Info.plist"
BUNDLE_ID="com.fluentflow.local.launcher"
/usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string ${BUNDLE_ID}" "$PLIST" >/dev/null 2>&1 ||
	/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier ${BUNDLE_ID}" "$PLIST" >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy -c "Set :CFBundleName ${APP_NAME}" "$PLIST" >/dev/null 2>&1 || true
codesign --force --deep --sign - "$DEST" >/dev/null 2>&1 ||
	echo "  （提示：重新签名失败，首次打开若被拦截，右键选「打开」即可）"
[[ "$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$PLIST" 2>/dev/null)" == "$BUNDLE_ID" ]] ||
	fail "CFBundleIdentifier 没有写入成功"

echo "✓ 已安装：${DEST}"
echo "  代码目录：${REPO}"
echo "  启动入口：backend.local_main（本地版）"
echo "  日志：${HOME}/Library/Logs/FluentFlow/local.log"
if [[ -e "${HOME}/Desktop/${APP_NAME}.command" ]]; then
	echo ""
	echo "  桌面上旧的「${APP_NAME}.command」保留未动，确认新应用可用后可自行删除。"
fi
open -R "$DEST"
