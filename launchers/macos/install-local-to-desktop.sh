#!/bin/bash
# 在桌面生成「FluentFlow Local.command」快捷方式：内嵌当前代码目录路径，
# 双击即通过 launchers/macos/FluentFlowLocal.command 启动本地版。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LAUNCHER="${SCRIPT_DIR}/FluentFlowLocal.command"
DEST="${HOME}/Desktop/FluentFlow Local.command"

if [[ ! -f "$LAUNCHER" ]]; then
	echo "找不到 $LAUNCHER" >&2
	exit 1
fi

cat >"$DEST" <<EOF
#!/bin/bash
export FLUENTFLOW_REPO="${REPO}"
exec "${LAUNCHER}"
EOF
chmod +x "$DEST"
echo "已安装到: $DEST"
open -R "$DEST"
