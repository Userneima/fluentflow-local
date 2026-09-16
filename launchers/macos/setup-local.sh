#!/bin/bash
# FluentFlow Local 的 macOS 一次性安装脚本。
#
# 和 Windows 的 setup-local.ps1 对等：建项目虚拟环境、装 Python 依赖、装前端
# 依赖并构建、跑就绪检查，最后在桌面生成「FluentFlow Local.app」。装完双击桌面
# 图标即可使用，不需要再开终端。
#
# 它只动两个地方：仓库里的 .venv / node_modules / frontend/dist-local，以及桌面
# 上的启动器。不装机器级的东西——FFmpeg 缺失时只给出 brew 命令并询问，不会背着
# 用户改系统。
#
# 用法：
#   bash launchers/macos/setup-local.sh
#   bash launchers/macos/setup-local.sh --skip-desktop   # 不生成桌面 App
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SKIP_DESKTOP=0
for arg in "$@"; do
	case "$arg" in
	--skip-desktop) SKIP_DESKTOP=1 ;;
	-h | --help)
		# 打印开头的注释块：跳过 shebang，遇到第一行非注释就停。写死行号的话，
		# 以后在注释里多加一句，帮助信息末尾就会冒出一行 set -euo pipefail。
		awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
		exit 0
		;;
	*)
		echo "未知参数：$arg（可用：--skip-desktop）" >&2
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

# --- 1. 系统依赖：FFmpeg ---------------------------------------------------
# 放在最前面。它是必需项，而下面的 pip 安装要下载一两 GB；把这个检查留到最后，
# 用户会先等十几分钟，再被告知少装一个东西。
step "检查 FFmpeg"
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
	echo "已安装：$(command -v ffmpeg)"
elif command -v brew >/dev/null 2>&1; then
	echo "未找到 FFmpeg（转码和抽帧都要用它）。"
	if [[ -t 0 ]]; then
		read -r -p "现在用 Homebrew 安装吗？[Y/n] " reply || reply="n"
		case "${reply:-Y}" in
		[Nn]*) fail "请先执行 brew install ffmpeg，然后重新运行本脚本。" ;;
		esac
		brew install ffmpeg || fail "brew install ffmpeg 失败，请手动安装后重试。"
	else
		fail "请先执行 brew install ffmpeg，然后重新运行本脚本。"
	fi
else
	fail "未找到 FFmpeg，也没有 Homebrew。
请先安装 Homebrew（https://brew.sh）再执行 brew install ffmpeg，
或用其他方式安装 FFmpeg 并确保 ffmpeg、ffprobe 在 PATH 上。"
fi

# --- 2. Python 虚拟环境 ----------------------------------------------------
# 必须 3.10 以上。macOS 自带的是 Xcode 命令行工具里的 Python 3.9，用它建出来的
# 环境会在装 pyobjc-framework-Vision 时尝试从源码编译并失败，而那个报错完全看不
# 出根因是版本太旧。所以这里主动挑一个够新的解释器。
step "准备 Python 虚拟环境"

python_ok() {
	[[ -x "$1" ]] || return 1
	"$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

python_minor_key() {
	# 把 "3.14" 变成可比较的整数，用来在多个解释器里挑最新的那个。
	local version major minor
	version="$("$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
	[[ -n "$version" ]] || return 1
	major="${version%%.*}"
	minor="${version##*.}"
	echo $((major * 1000 + minor))
}

# 扫 PATH 上所有 python3 / python3.N，外加 Homebrew 的两个常见前缀。
# 这里不能写死一串 python3.10 … python3.13：这台机器上系统自带的 python3 是
# 3.9、Homebrew 装的是 3.14，写死到 13 的列表两头都够不着，全新安装会在一台
# 明明装了合格 Python 的机器上报「找不到 3.10 以上的 Python」。每出一个新版本
# 就要回来改一次的列表，迟早忘。
collect_pythons() {
	local dir entry
	local old_ifs="$IFS"
	IFS=:
	for dir in $PATH; do
		IFS="$old_ifs"
		[[ -d "$dir" ]] || continue
		for entry in "$dir"/python3 "$dir"/python3.[0-9] "$dir"/python3.[0-9][0-9]; do
			[[ -x "$entry" ]] && echo "$entry"
		done
		IFS=:
	done
	IFS="$old_ifs"
	for entry in /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.[0-9] /opt/homebrew/bin/python3.[0-9][0-9] \
		/usr/local/bin/python3 /usr/local/bin/python3.[0-9] /usr/local/bin/python3.[0-9][0-9]; do
		[[ -x "$entry" ]] && echo "$entry"
	done
}

# 优先用 PATH 上的 python3（用户手动装东西时用的就是它，第三方包的预编译轮子
# 也最齐）；它不够新时，退而取所有合格解释器里版本最高的那个。
best_python() {
	local py key best="" best_key=0
	if python_ok "$(command -v python3 2>/dev/null || echo /nonexistent)"; then
		command -v python3
		return 0
	fi
	while IFS= read -r py; do
		python_ok "$py" || continue
		key="$(python_minor_key "$py")" || continue
		if ((key > best_key)); then
			best="$py"
			best_key="$key"
		fi
	done < <(collect_pythons | awk '!seen[$0]++')
	[[ -n "$best" ]] || return 1
	echo "$best"
}

# 启动器 FluentFlowLocal.command 先找 venv/ 再找 .venv/。如果机器上已经有一个
# venv/，这里还去建 .venv/，双击启动时用的是旧的那个，而用户会以为自己刚装好。
VENV="${REPO}/.venv"
if [[ -d "${REPO}/venv" ]]; then
	VENV="${REPO}/venv"
	echo "沿用已存在的 venv/（启动器优先使用它）。"
fi
VENV_PY="${VENV}/bin/python"

if [[ -x "$VENV_PY" ]] && ! python_ok "$VENV_PY"; then
	fail "已存在的虚拟环境是 $("$VENV_PY" -V 2>&1)，低于 3.10。
请删除它再重新运行本脚本：rm -rf ${VENV}"
fi

if [[ ! -x "$VENV_PY" ]]; then
	BASE_PY="$(best_python || true)"
	[[ -n "$BASE_PY" ]] || fail "没有找到 3.10 或更高版本的 Python。
macOS 自带的是 3.9，装不上本项目的依赖。请执行 brew install python 后重试。"
	echo "使用 ${BASE_PY}（$("$BASE_PY" -V 2>&1)）创建 ${VENV}"
	"$BASE_PY" -m venv "$VENV" || fail "创建虚拟环境失败。"
fi
echo "虚拟环境：${VENV}（$("$VENV_PY" -V 2>&1)）"

# --- 3. Python 依赖 --------------------------------------------------------
step "安装 Python 依赖（体积较大，首次约需十几分钟）"
# 先升 pip：老 pip 拿不到 pyobjc 的预编译包，会转去编译源码然后失败。
"$VENV_PY" -m pip install --quiet --upgrade pip || fail "升级 pip 失败。"
"$VENV_PY" -m pip install -r "${REPO}/requirements-local.txt" ||
	fail "安装 Python 依赖失败，请看上面的报错。"

# Apple Silicon 上的加速转录引擎由 requirements-local.txt 里的平台标记自动决定，
# 不需要像 Windows 那样单独装一份 GPU 运行库。这里只是把实际走的那条路说出来。
if "$VENV_PY" -c 'import importlib.util,sys; sys.exit(0 if importlib.util.find_spec("mlx_whisper") else 1)' 2>/dev/null; then
	echo "转录引擎：mlx-whisper（Apple Silicon 加速）"
else
	echo "转录引擎：faster-whisper（CPU）"
fi

# --- 4. 前端 ---------------------------------------------------------------
step "安装前端依赖并构建"
# 和上面的 FFmpeg 同样对待。之前这里是直接报错让用户自己去装 Node，而 FFmpeg
# 缺失时脚本会主动问一句——同一个脚本里两种待遇，先遇到哪个全看运气。
if ! command -v npm >/dev/null 2>&1; then
	if command -v brew >/dev/null 2>&1 && [[ -t 0 ]]; then
		echo "未找到 Node.js（构建前端界面要用它）。"
		read -r -p "现在用 Homebrew 安装吗？[Y/n] " reply || reply="n"
		case "${reply:-Y}" in
		[Nn]*) fail "请先安装 Node.js（brew install node），然后重新运行本脚本。" ;;
		esac
		brew install node || fail "brew install node 失败，请手动安装后重试。"
	else
		fail "未找到 npm。请先安装 Node.js（brew install node，或到 https://nodejs.org 下载），
然后重新运行本脚本。"
	fi
fi

cd "$REPO"
if [[ -f package-lock.json ]]; then
	npm ci || fail "npm ci 失败。"
else
	npm install || fail "npm install 失败。"
fi

# 构建脚本的名字在两套前端里不一样，按 package.json 实际声明的来，避免静默构建
# 错目标。
BUILD_SCRIPT="build:frontend"
if "$VENV_PY" - "$REPO/package.json" <<'PY' 2>/dev/null; then
import json
import sys

scripts = json.load(open(sys.argv[1], encoding="utf-8")).get("scripts", {})
sys.exit(0 if "build:frontend:local" in scripts else 1)
PY
	BUILD_SCRIPT="build:frontend:local"
fi
echo "构建：npm run ${BUILD_SCRIPT}"
npm run "$BUILD_SCRIPT" || fail "前端构建失败。"

# --- 5. 转录模型 -----------------------------------------------------------
# 不下这一步也能装完，但「安装完成」四个字会变成谎话：用户双击图标、丢进第一个
# 视频，才开始等几 GB 的模型，而那个等待没有任何进度可看。下载失败不让整个安装
# 失败——模型随时可以补，环境不必重装。
step "准备转录模型"
"$VENV_PY" "${REPO}/scripts/stt_model.py" fetch --ask ||
	echo "（模型没有下成。第一次转录时会自动重试，也可以稍后手动运行
  ${VENV_PY} ${REPO}/scripts/stt_model.py fetch）"

# --- 6. 就绪检查 -----------------------------------------------------------
step "启动前检查"
"$VENV_PY" "${REPO}/scripts/check_local_readiness.py" ||
	fail "环境未就绪，请按上面的提示处理后重新运行本脚本。"

# --- 7. 桌面启动器 ---------------------------------------------------------
if [[ "$SKIP_DESKTOP" -eq 1 ]]; then
	echo ""
	echo "✓ 安装完成（按要求跳过了桌面启动器）。"
	echo "  手动启动：${VENV_PY} -m uvicorn backend.local_main:app --host 127.0.0.1 --port 8000"
	exit 0
fi

step "生成桌面启动器"
bash "${SCRIPT_DIR}/install-local-to-desktop.sh" || fail "生成桌面启动器失败。"

echo ""
echo "✓ 安装完成。双击桌面上的「FluentFlow Local」即可启动。"
echo "  AI 与飞书凭据在应用的设置页里填写。"
