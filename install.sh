#!/bin/bash
# FluentFlow Local 一条命令安装（macOS）。给 agent 用，人也可以直接跑：
#
#   curl -fsSL https://raw.githubusercontent.com/Userneima/fluentflow-local/main/install.sh | bash
#
# 依次做完：下载最新发布版到 ~/fluentflow-local、运行安装脚本、启动 FluentFlow、
# 把它的 MCP 工具注册到本机的 Claude Code 和 Codex、自检。全程不要密码，不弹系统窗口，
# 不调用 git。目录已经存在时沿用里面的代码，只把后面几步重做一遍。
#
# 可选环境变量：
#   FLUENTFLOW_INSTALL_DIR   装到哪里，默认 ~/fluentflow-local
#   FLUENTFLOW_INSTALL_REF   装哪个版本的标签（如 v0.7.0），默认最新发布版
# 其余参数原样交给 launchers/macos/setup-local.sh（如 --skip-model）。
set -euo pipefail

REPO_SLUG="Userneima/fluentflow-local"
DIR="${FLUENTFLOW_INSTALL_DIR:-${HOME}/fluentflow-local}"
PORT="${FLUENTFLOW_LOCAL_PORT:-8000}"

step() { echo ""; echo "== $1 =="; }
fail() {
	echo "" >&2
	echo "✗ $1" >&2
	exit 1
}

[[ "$(uname -s)" == "Darwin" ]] || fail "这个脚本只用于 macOS。Windows 请运行 install.ps1。"

# --- 1. 代码 ----------------------------------------------------------------
step "获取代码"
if [[ -f "${DIR}/backend/local_main.py" ]]; then
	echo "沿用已有的代码：${DIR}（要升级到新版本，按其中 README 的「更新到新版本」操作）"
else
	[[ -e "$DIR" ]] && fail "${DIR} 已存在但不是 FluentFlow Local 的代码目录。请换一个位置（FLUENTFLOW_INSTALL_DIR）。"
	TAG="${FLUENTFLOW_INSTALL_REF:-}"
	if [[ -z "$TAG" ]]; then
		# 读 releases/latest 的跳转地址，不走 GitHub API：API 对未登录请求有次数限制。
		TAG="$(curl -fsSI --connect-timeout 20 "https://github.com/${REPO_SLUG}/releases/latest" |
			tr -d '\r' | sed -n 's#^[Ll]ocation: .*/tag/##p' | tail -1)"
		[[ -n "$TAG" ]] || fail "查不到最新版本，请检查能否打开 github.com。"
	fi
	echo "下载 ${TAG}…"
	WORK="$(mktemp -d)"
	trap 'rm -rf "$WORK"' EXIT
	curl -fL --retry 2 --connect-timeout 20 -sS -o "${WORK}/src.zip" \
		"https://github.com/${REPO_SLUG}/archive/refs/tags/${TAG}.zip" || fail "代码下载失败，请检查能否打开 github.com。"
	unzip -q "${WORK}/src.zip" -d "${WORK}/src" || fail "代码解压失败。"
	mkdir -p "$(dirname "$DIR")"
	mv "${WORK}"/src/*/ "$DIR"
	echo "已放到 ${DIR}"
fi

# --- 2. 安装 ----------------------------------------------------------------
bash "${DIR}/launchers/macos/setup-local.sh" "$@"

VENV_PY="${DIR}/.venv/bin/python"
[[ -x "${DIR}/venv/bin/python" ]] && VENV_PY="${DIR}/venv/bin/python"
MCP_SCRIPT="${DIR}/scripts/fluentflow_mcp_server.py"

# --- 3. 启动 ----------------------------------------------------------------
step "启动 FluentFlow Local"
healthy() { curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q '"edition":"local"'; }
if healthy; then
	echo "已经在运行。"
else
	APP="${HOME}/Desktop/FluentFlow Local.app"
	if [[ -d "$APP" ]]; then
		open "$APP"
	else
		# 按要求没生成桌面图标时，直接在后台起服务。
		mkdir -p "${HOME}/Library/Logs/FluentFlow"
		(cd "$DIR" && nohup "$VENV_PY" -m uvicorn backend.local_main:app --host 127.0.0.1 --port "$PORT" \
			>>"${HOME}/Library/Logs/FluentFlow/local.log" 2>&1 &)
	fi
	for _ in $(seq 1 90); do
		healthy && break
		sleep 1
	done
	healthy || fail "服务没有在 90 秒内启动。日志：${HOME}/Library/Logs/FluentFlow/local.log"
	echo "已启动：http://127.0.0.1:${PORT}"
fi

# --- 4. MCP 工具 ------------------------------------------------------------
# MCP 服务自己从仓库的 .env 读访问令牌，所以注册时不带任何密钥。
step "注册 MCP 工具"
CODEX_TOML="[mcp_servers.fluentflow-local]
command = \"${VENV_PY}\"
args = [\"${MCP_SCRIPT}\"]"
registered=0
# 默认端口时 MCP 服务自己就连得上；换过端口才需要告诉它。
MCP_ENV=()
[[ "$PORT" != "8000" ]] && MCP_ENV=(-e "FLUENTFLOW_API_BASE=http://127.0.0.1:${PORT}")
if command -v claude >/dev/null 2>&1; then
	if claude mcp get fluentflow-local >/dev/null 2>&1; then
		echo "Claude Code：已注册过 fluentflow-local，保持不动。"
	elif claude mcp add --scope user ${MCP_ENV[@]+"${MCP_ENV[@]}"} fluentflow-local -- "$VENV_PY" "$MCP_SCRIPT" >/dev/null; then
		echo "Claude Code：已注册 fluentflow-local。"
	else
		echo "Claude Code：注册失败，可以手动运行：claude mcp add --scope user fluentflow-local -- \"${VENV_PY}\" \"${MCP_SCRIPT}\""
	fi
	registered=1
fi
if command -v codex >/dev/null 2>&1; then
	if grep -q '^\[mcp_servers\.fluentflow-local\]' "${HOME}/.codex/config.toml" 2>/dev/null; then
		echo "Codex：已注册过 fluentflow-local，保持不动。"
	elif codex mcp add fluentflow-local -- "$VENV_PY" "$MCP_SCRIPT" >/dev/null 2>&1; then
		echo "Codex：已注册 fluentflow-local。"
	else
		echo "Codex：没能自动注册，把下面几行加进 ~/.codex/config.toml："
		echo "$CODEX_TOML"
	fi
	registered=1
fi
if [[ "$registered" -eq 0 ]]; then
	echo "没找到 claude 或 codex 命令。在你的 AI 工具里把下面这个命令注册成 MCP 服务："
	echo "  \"${VENV_PY}\" \"${MCP_SCRIPT}\""
	echo "Codex 的写法："
	echo "$CODEX_TOML"
fi

# --- 5. 自检 ----------------------------------------------------------------
step "自检 MCP 服务"
"$VENV_PY" "${DIR}/scripts/check_mcp_server.py" >/dev/null || fail "MCP 服务自检没通过，运行 ${VENV_PY} ${DIR}/scripts/check_mcp_server.py 看详情。"
echo "MCP 服务正常。"

echo ""
echo "✓ FluentFlow Local 已装好，并已启动：http://127.0.0.1:${PORT}"
echo "  以后从桌面的「FluentFlow Local」打开。要笔记时，在新对话里把视频文件或链接交给你的 agent，"
echo "  它会用 FluentFlow 转写并亲自写笔记，不需要任何 API Key。"
