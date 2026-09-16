#!/bin/bash
# FluentFlow Local 的 macOS 卸载脚本。
#
# 分三层，因为「卸载」对三种东西意味着完全不同的后果：
#   程序（默认删）：虚拟环境、前端依赖和构建产物、桌面启动器、日志。删了重装
#                   就回来，没有任何损失。
#   模型（--models）：几 GB 的转录模型。它们住在 Hugging Face 的公共缓存里，
#                   那个目录别的工具也在用，所以这里只删本产品自己下载的那几个
#                   目录，而且目录清单是问代码要的，不是按名字猜的。
#   数据（--data）：任务历史、转录稿、笔记、媒体文件。删掉不可恢复，所以要单独
#                   开口，并且再确认一次。
#
# 不动 FFmpeg、Node.js、Homebrew 和 Python：它们是系统工具，这台机器上别的东西
# 很可能也在用。
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
PORT="${FLUENTFLOW_LOCAL_PORT:-8000}"

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
	out="$("$py" -c 'from backend.core.runtime_paths import app_data_root; print(app_data_root())' 2>/dev/null)" || return 1
	out="$(printf '%s\n' "$out" | tail -n 1)"
	[[ "$out" == /* ]] || return 1
	printf '%s\n' "$out"
}

DATA_ROOT="$(data_root_from_code || true)"
if [[ -z "$DATA_ROOT" ]]; then
	DATA_ROOT="${FLUENTFLOW_DATA_DIR:-${HOME}/Library/Application Support/FluentFlow}"
	echo ""
	echo "（问不到代码里的数据目录，按默认位置处理：${DATA_ROOT}）"
fi

# 模型目录同理：交给 scripts/stt_model.py 列，它知道产品下过哪些 repo。
model_dirs=()
if [[ "$REMOVE_MODELS" -eq 1 ]]; then
	py="${REPO}/.venv/bin/python"
	[[ -x "$py" ]] || py="${REPO}/venv/bin/python"
	if [[ -x "$py" ]]; then
		while IFS= read -r line; do
			[[ "$line" == /* && -d "$line" ]] && model_dirs+=("$line")
		done < <("$py" "${REPO}/scripts/stt_model.py" cache-paths 2>/dev/null || true)
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

add_target "${REPO}/.venv" "Python 虚拟环境"
add_target "${REPO}/venv" "Python 虚拟环境（旧位置）"
add_target "${REPO}/node_modules" "前端依赖"
add_target "${REPO}/frontend/dist-local" "前端构建产物"
add_target "${HOME}/Desktop/FluentFlow Local.app" "桌面启动器"
add_target "${HOME}/Desktop/FluentFlow Local.command" "桌面启动器（旧版）"
add_target "${HOME}/Library/Logs/FluentFlow" "日志"

for dir in "${model_dirs[@]:-}"; do
	[[ -n "$dir" ]] && add_target "$dir" "转录模型"
done

if [[ "$REMOVE_DATA" -eq 1 ]]; then
	add_target "$DATA_ROOT" "任务历史、转录稿、笔记、媒体文件" 1
fi

if [[ ${#targets[@]} -eq 0 ]]; then
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
for i in "${!targets[@]}"; do
	rm -rf "${targets[$i]}" || fail "删不掉 ${targets[$i]}，请检查权限后重试。"
	echo "已删除：${targets[$i]}"
done

step "还剩下"
if [[ "$REMOVE_DATA" -ne 1 && -e "$DATA_ROOT" ]]; then
	size="$(du -sh "$DATA_ROOT" 2>/dev/null | cut -f1 || echo "?")"
	echo "  你的任务数据（${size}）：${DATA_ROOT}"
	echo "    要一并删除：bash ${BASH_SOURCE[0]} --data"
fi
if [[ "$REMOVE_MODELS" -ne 1 ]]; then
	echo "  转录模型（通常几 GB）：在 Hugging Face 缓存里"
	echo "    要一并删除：bash ${BASH_SOURCE[0]} --models"
fi
echo "  代码目录：${REPO}"
echo "    本脚本就在里面，所以留给你自己删：rm -rf ${REPO}"
echo ""
echo "  没有动 FFmpeg、Node.js、Homebrew 和 Python，它们是系统工具，"
echo "  这台机器上别的程序可能也在用。"
echo ""
echo "✓ 卸载完成。"
