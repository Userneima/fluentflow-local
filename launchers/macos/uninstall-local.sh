#!/bin/bash
# FluentFlow Local 的 macOS 卸载脚本。
#
# 分三层，因为「卸载」对三种东西意味着完全不同的后果：
#   程序（默认删）：代码目录（从发布包装的才删，git 检出只删虚拟环境、前端依赖
#                   和构建产物，代码留着）、桌面启动器、日志、给 agent 的 skill，
#                   以及注册在 Claude Code、Codex 里的 fluentflow-local 工具（只在
#                   它指向本目录时注销）。删了重装就回来，没有任何损失。
#   模型（--models）：几 GB 的转录模型。它们住在 Hugging Face 的公共缓存里，
#                   那个目录别的工具也在用，所以这里只删本产品自己下载的那几个
#                   目录，而且目录清单是问代码要的，不是按名字猜的。
#   数据（--data）：任务历史、转录稿、笔记、媒体文件。删掉不可恢复，所以要单独
#                   开口，并且再确认一次。
#
# 安装脚本自己下载到数据目录 bin/、tools/ 里的 FFmpeg、Node.js、Python 属于程序，
# 默认一起删。机器上原本就有的 FFmpeg、Node.js、Homebrew 和 Python 不动：它们是
# 系统工具，别的东西很可能也在用。
#
# 用法：
#   bash launchers/macos/uninstall-local.sh                # 只卸程序
#   bash launchers/macos/uninstall-local.sh --models       # 连转录模型一起
#   bash launchers/macos/uninstall-local.sh --data         # 连任务数据一起
#   bash launchers/macos/uninstall-local.sh --all          # 三样都删
#   bash launchers/macos/uninstall-local.sh --all --dry-run  # 只看要删什么
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# 端口和各个位置都按环境变量、再 .env 的顺序读，和服务一致。
# shellcheck source=fluentflow-env.sh
source "${SCRIPT_DIR}/fluentflow-env.sh"
PORT="$(ff_setting "$REPO" FLUENTFLOW_LOCAL_PORT)"
[[ "$PORT" =~ ^[0-9]+$ ]] || PORT=8000

REMOVE_MODELS=0
REMOVE_DATA=0
DRY_RUN=0
ASSUME_YES=0

for arg in "$@"; do
	case "$arg" in
	--models) REMOVE_MODELS=1 ;;
	--data) REMOVE_DATA=1 ;;
	--all)
		REMOVE_MODELS=1
		REMOVE_DATA=1
		;;
	--dry-run) DRY_RUN=1 ;;
	-y | --yes) ASSUME_YES=1 ;;
	-h | --help)
		awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
		exit 0
		;;
	*)
		echo "未知参数：$arg（可用：--models --data --all --dry-run --yes）" >&2
		exit 2
		;;
	esac
done

step() { echo ""; echo "== $1 =="; }
fail() {
	echo "" >&2
	echo "✗ $1" >&2
	exit 1
}

[[ -f "${REPO}/backend/local_main.py" ]] ||
	fail "在 ${REPO} 下找不到 backend/local_main.py，这不像 FluentFlow Local 代码目录。"

# 卸载要删的东西里有正在被使用的文件，所以先把服务停下来。
step "检查服务是否在运行"
health="$(curl -s --max-time 2 "http://127.0.0.1:${PORT}/health" 2>/dev/null || true)"
if [[ "$health" == *'"execution":"local"'* ]]; then
	# `|| true`：grep 数不到任何在跑的任务时返回 1，而 set -e 会让「一切正常」
	# 这个最常见的情况直接终止脚本。
	running="$(curl -s --max-time 3 "http://127.0.0.1:${PORT}/jobs?limit=100" 2>/dev/null |
		grep -c '"status":"\(queued\|processing\|running\|pending\)"' || true)"
	echo "FluentFlow Local 正在端口 ${PORT} 上运行。"
	if [[ "${running:-0}" -gt 0 ]]; then
		echo "⚠ 后台还有 ${running} 个任务在进行中，停止会让它们中断。"
	fi
	if [[ "$DRY_RUN" -eq 1 ]]; then
		echo "（--dry-run：不会真的停止它）"
	else
		if [[ "$ASSUME_YES" -ne 1 ]]; then
			read -r -p "停止它并继续卸载？[y/N] " reply || reply="n"
			case "${reply:-N}" in
			[Yy]*) ;;
			*) fail "已取消，什么都没有删除。" ;;
			esac
		fi
		pids="$(lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null || true)"
		if [[ -n "$pids" ]]; then
			# shellcheck disable=SC2086
			kill $pids 2>/dev/null || true
			for _ in $(seq 1 40); do
				curl -s --max-time 1 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 || break
				sleep 0.25
			done
		fi
		curl -s --max-time 1 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 &&
			fail "服务没有停下来，请手动结束占用端口 ${PORT} 的进程后重试。"
		echo "已停止。"
	fi
else
	echo "服务没有在运行。"
fi

# 数据目录的位置可以被 FLUENTFLOW_DATA_DIR 改掉，所以问代码，不要假设。venv 这
# 时候还在（它是本脚本待删的东西之一），问完再删。
data_root_from_code() {
	local py="${REPO}/.venv/bin/python"
	[[ -x "$py" ]] || py="${REPO}/venv/bin/python"
	[[ -x "$py" ]] || return 1
	local out
	out="$(cd "$REPO" && "$py" -c 'from backend.core.runtime_paths import load_project_env, app_data_root; load_project_env(); print(app_data_root())' 2>/dev/null)" || return 1
	out="$(printf '%s\n' "$out" | tail -n 1)"
	[[ "$out" == /* ]] || return 1
	printf '%s\n' "$out"
}

DATA_ROOT="$(data_root_from_code || true)"
if [[ -z "$DATA_ROOT" ]]; then
	DATA_ROOT="$(ff_data_dir "$REPO")"
	echo ""
	echo "（问不到代码里的数据目录，按 .env 和默认位置处理：${DATA_ROOT}）"
fi
IS_GIT_CHECKOUT=0
[[ -e "${REPO}/.git" ]] && IS_GIT_CHECKOUT=1
# 数据、链接下载或模型设在代码目录里面时，代码目录不整个删，只删程序自己的部分（同 git 检出）。
KEEPS_USER_FILES=0
for where in "$DATA_ROOT" "$(ff_setting "$REPO" FLUENTFLOW_LINK_OUTPUT_DIR)" "$(ff_setting "$REPO" HF_HOME)"; do
	[[ -n "$where" && "$where" == "${REPO}/"* ]] && KEEPS_USER_FILES=1
done

# 模型目录同理：交给 scripts/stt_model.py 列，它知道产品下过哪些 repo。
model_dirs=()
if [[ "$REMOVE_MODELS" -eq 1 ]]; then
	py="${REPO}/.venv/bin/python"
	[[ -x "$py" ]] || py="${REPO}/venv/bin/python"
	if [[ -x "$py" ]]; then
		while IFS= read -r line; do
			[[ "$line" == /* && -d "$line" ]] && model_dirs+=("$line")
		done < <(ff_import_env "$REPO" && "$py" "${REPO}/scripts/stt_model.py" cache-paths 2>/dev/null || true)
	fi
	if [[ ${#model_dirs[@]} -eq 0 ]]; then
		echo ""
		echo "（列不出模型目录：虚拟环境可能已经不可用。模型通常在"
		echo "  ~/.cache/huggingface/hub/ 下以 models--mlx-community--whisper-"
		echo "  和 models--Systran--faster-whisper- 开头的目录里，可自行确认后删除。）"
	fi
fi

# 收集条目：三个平行数组，路径 / 说明 / 是不是用户数据。
targets=()
labels=()
sensitive=()

add_target() {
	[[ -e "$1" ]] || return 0
	targets+=("$1")
	labels+=("$2")
	sensitive+=("${3:-0}")
}

if [[ "$IS_GIT_CHECKOUT" -eq 1 || "$KEEPS_USER_FILES" -eq 1 ]]; then
	add_target "${REPO}/.venv" "Python 虚拟环境"
	add_target "${REPO}/venv" "Python 虚拟环境（旧位置）"
	add_target "${REPO}/node_modules" "前端依赖"
	add_target "${REPO}/frontend/dist-local" "前端构建产物"
else
	# 从发布包装的：目录里只有程序（和 .env，重装时会重新生成访问令牌）。
	add_target "$REPO" "程序（代码、Python 虚拟环境、前端）"
fi
add_target "${HOME}/Desktop/FluentFlow Local.app" "桌面启动器"
add_target "${HOME}/Desktop/FluentFlow Local.command" "桌面启动器（旧版）"
add_target "${HOME}/Library/Logs/FluentFlow" "日志"
add_target "${DATA_ROOT}/bin" "安装时下载的 FFmpeg 和 uv"
add_target "${DATA_ROOT}/tools" "安装时下载的 Node.js 和 Python"
# install.sh 装的 skill 是链到本目录的链接；用户自己的同名目录不动。
for skill in "${HOME}/.claude/skills/fluentflow" "${HOME}/.codex/skills/fluentflow"; do
	if [[ -L "$skill" && "$(readlink "$skill")" == "${REPO}/"* ]]; then
		add_target "$skill" "给 agent 的 skill（链接）"
	fi
done

# 装时注册的 MCP 工具，只在它启动的是本目录的服务时注销：指向别处的是另一份安装的。
mcp_removals=()
if command -v claude >/dev/null 2>&1; then
	if described="$(claude mcp get fluentflow-local 2>/dev/null)"; then
		if [[ "$described" == *"${REPO}/"* ]]; then
			mcp_removals+=("Claude Code")
		else
			echo ""
			echo "Claude Code 里的 fluentflow-local 指向别的位置，不动它。"
		fi
	fi
fi
CODEX_CONFIG="${HOME}/.codex/config.toml"
if [[ -f "$CODEX_CONFIG" ]]; then
	section="$(awk '/^\[mcp_servers\.fluentflow-local\][[:space:]]*$/ { on = 1; next } /^\[/ { on = 0 } on' "$CODEX_CONFIG")"
	if grep -q '^\[mcp_servers\.fluentflow-local\]' "$CODEX_CONFIG"; then
		if [[ "$section" == *"${REPO}/"* ]]; then
			mcp_removals+=("Codex")
		else
			echo ""
			echo "Codex 里的 fluentflow-local 指向别的位置，不动它。"
		fi
	fi
fi

for dir in "${model_dirs[@]:-}"; do
	[[ -n "$dir" ]] && add_target "$dir" "转录模型"
done

if [[ "$REMOVE_DATA" -eq 1 ]]; then
	add_target "$DATA_ROOT" "任务历史、转录稿、笔记、媒体文件" 1
fi

if [[ ${#targets[@]} -eq 0 && ${#mcp_removals[@]} -eq 0 ]]; then
	echo ""
	echo "✓ 没有找到需要删除的东西。"
	exit 0
fi

step "将要删除"
has_sensitive=0
for i in "${!targets[@]}"; do
	size="$(du -sh "${targets[$i]}" 2>/dev/null | cut -f1 || echo "?")"
	printf '  %-8s %s\n' "$size" "${targets[$i]}"
	printf '           %s\n' "${labels[$i]}"
	[[ "${sensitive[$i]}" -eq 1 ]] && has_sensitive=1
done
for tool in "${mcp_removals[@]:-}"; do
	[[ -n "$tool" ]] && echo "  注销 ${tool} 里的 fluentflow-local 工具"
done

if [[ "$DRY_RUN" -eq 1 ]]; then
	echo ""
	echo "（--dry-run：以上都没有真的删除。）"
	exit 0
fi

echo ""
if [[ "$has_sensitive" -eq 1 ]]; then
	echo "⚠ 其中包含你的任务数据，删掉之后无法恢复。"
	if [[ "$ASSUME_YES" -ne 1 ]]; then
		read -r -p "确认请输入 DELETE（其他任何输入都会取消）： " reply || reply=""
		[[ "$reply" == "DELETE" ]] || fail "已取消，什么都没有删除。"
	fi
elif [[ "$ASSUME_YES" -ne 1 ]]; then
	read -r -p "确认删除以上内容？[y/N] " reply || reply="n"
	case "${reply:-N}" in
	[Yy]*) ;;
	*) fail "已取消，什么都没有删除。" ;;
	esac
fi

step "删除"
for tool in "${mcp_removals[@]:-}"; do
	case "$tool" in
	"Claude Code")
		if claude mcp remove --scope user fluentflow-local >/dev/null 2>&1; then
			echo "已注销：Claude Code 里的 fluentflow-local"
		else
			echo "Claude Code 注销失败，请手动运行：claude mcp remove --scope user fluentflow-local"
		fi
		;;
	Codex)
		if command -v codex >/dev/null 2>&1 && codex mcp remove fluentflow-local >/dev/null 2>&1; then
			echo "已注销：Codex 里的 fluentflow-local"
		else
			echo "Codex 没能自动注销，请删掉 ${CODEX_CONFIG} 里 [mcp_servers.fluentflow-local] 那一节。"
		fi
		;;
	esac
done
# 代码目录可能就是当前目录；bash 已经打开了本脚本，删掉文件也能读完。
cd "$HOME"
for i in "${!targets[@]}"; do
	rm -rf "${targets[$i]}" || fail "删不掉 ${targets[$i]}，请检查权限后重试。"
	echo "已删除：${targets[$i]}"
done

step "还剩下"
if [[ "$REMOVE_DATA" -ne 1 && -e "$DATA_ROOT" ]]; then
	size="$(du -sh "$DATA_ROOT" 2>/dev/null | cut -f1 || echo "?")"
	echo "  你的任务数据（${size}）：${DATA_ROOT}"
	if [[ "$IS_GIT_CHECKOUT" -eq 1 ]]; then
		echo "    要一并删除：bash ${BASH_SOURCE[0]} --data"
	else
		echo "    确定不要了，直接删掉这个文件夹。"
	fi
fi
if [[ "$REMOVE_MODELS" -ne 1 ]]; then
	echo "  转录模型（通常几 GB）：在 Hugging Face 缓存里"
	if [[ "$IS_GIT_CHECKOUT" -eq 1 ]]; then
		echo "    要一并删除：bash ${BASH_SOURCE[0]} --models"
	else
		echo "    在 ~/.cache/huggingface/hub/ 下以 models--mlx-community--whisper- 和"
		echo "    models--Systran--faster-whisper- 开头的目录里，可自行确认后删除。"
	fi
fi
if [[ "$IS_GIT_CHECKOUT" -eq 1 ]]; then
	echo "  代码目录：${REPO}"
	echo "    这是 git 检出，里面可能有你的改动，留给你自己删。"
fi
echo ""
echo "  机器上原本就有的 FFmpeg、Node.js、Homebrew 和 Python 没有动，"
echo "  这台机器上别的程序可能也在用。"
echo ""
echo "✓ 卸载完成。"
