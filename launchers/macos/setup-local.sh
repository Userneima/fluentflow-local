#!/bin/bash
# FluentFlow Local 的 macOS 一次性安装脚本。
#
# 和 Windows 的 setup-local.ps1 对等：建项目虚拟环境、装 Python 依赖、装前端
# 依赖并构建、在后台下载转录模型、跑就绪检查，最后在桌面生成「FluentFlow Local.app」。
# 装完双击桌面图标即可使用，不需要再开终端。
#
# 中途不问问题。跑这个脚本就是要用这个产品，而它问得出口的每一样东西（FFmpeg、
# Node.js、转录模型）都是必需品，答案只有一个；把它们做成问句，只是把等待切成
# 几段，让用户没法走开。所以缺什么装什么，每步开始前说一句在装什么，失败了才停
# 下来说话。
#
# 全程不需要密码，也不会弹系统窗口，所以 agent 可以替人从头装到尾。缺的工具都装进
# 用户自己的数据目录，不经过 Homebrew，也不调用 /usr/bin 下那些没装命令行开发者
# 工具时会弹窗的占位程序（python3、git）。
#
# 它动的地方：仓库里的 .venv / node_modules / frontend/dist-local，桌面上的启动
# 器，以及数据目录下的 bin/（FFmpeg、uv）和 tools/（构建界面用的 Node.js、uv 装的
# Python）。
#
# pip、npm、Node.js、讲话人模型和转录模型都有国内来源：先探原站几秒，连不上或明显
# 慢就先走镜像（清华源、npmmirror、ModelScope、hf-mirror），失败了再换另一个；
# Node.js 按固定的校验值核对。一开始就设了 PIP_INDEX_URL 或 npm registry 的，按你
# 设的来。
#
# 用法：
#   bash launchers/macos/setup-local.sh
#   bash launchers/macos/setup-local.sh --skip-desktop   # 不生成桌面 App
#   bash launchers/macos/setup-local.sh --skip-model     # 不下转录模型（第一次转录时再下）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SKIP_DESKTOP=0
SKIP_MODEL=0
for arg in "$@"; do
	case "$arg" in
	--skip-desktop) SKIP_DESKTOP=1 ;;
	--skip-model) SKIP_MODEL=1 ;;
	-h | --help)
		# 打印开头的注释块：跳过 shebang，遇到第一行非注释就停。写死行号的话，
		# 以后在注释里多加一句，帮助信息末尾就会冒出一行 set -euo pipefail。
		awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
		exit 0
		;;
	*)
		echo "未知参数：$arg（可用：--skip-desktop --skip-model）" >&2
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

# 和 backend/core/runtime_paths.py 的 app_data_root 一致（先环境变量，再 .env）：
# 后端启动时会把这里的 bin/ 放到 PATH 最前面，所以装在这里的 FFmpeg 后端找得到。
# shellcheck source=fluentflow-env.sh
source "${SCRIPT_DIR}/fluentflow-env.sh"
ff_import_env "$REPO"
[[ -z "$FF_UNPLACED" ]] ||
	fail "${FF_UNPLACED} 照服务的读法看不准在哪（展开不了的 \$、引号没在本行闭合、.env 不是 UTF-8）；照这样装，FFmpeg 和模型会放到服务找不到的地方。请在 .env 或环境变量里把它改成完整路径后重新运行。什么都还没装。"
ff_use_system_proxy
DATA_DIR="$(ff_data_dir "$REPO")"
TOOLS_BIN="${DATA_DIR}/bin"
TOOLS_DIR="${DATA_DIR}/tools"
mkdir -p "$TOOLS_BIN" "$TOOLS_DIR"
export PATH="${TOOLS_BIN}:${PATH}"
# uv 的下载缓存放进数据目录（1 GB 多），卸载时能一起删；默认的 ~/.cache/uv 别的程序也在用，
# 卸载不能动（2026-11-25 沙箱实测卸载后剩 1.4 GB）。用户自己设了 UV_CACHE_DIR 的照用。
export UV_CACHE_DIR="${UV_CACHE_DIR:-${DATA_DIR}/cache/uv}"

case "$(uname -m)" in
arm64) ARCH_FFMPEG="arm64" ARCH_UV="aarch64" ARCH_NODE="arm64" ;;
*) ARCH_FFMPEG="amd64" ARCH_UV="x86_64" ARCH_NODE="x64" ;;
esac

# 用 curl 下载（它下的文件不带隔离标记，打开时不会被系统拦下）。
download() {
	# 有人看着时显示进度条；agent 读日志时不要满屏的进度字符。
	local progress="-sS"
	[[ -t 1 ]] && progress="--progress-bar"
	curl -fL --retry 2 --connect-timeout 20 "$progress" -o "$2" "$1"
}

# 没装命令行开发者工具时，/usr/bin/python3 和 /usr/bin/git 一运行就弹窗。
has_dev_tools() { xcode-select -p >/dev/null 2>&1; }

# --- 1. 系统依赖：FFmpeg ---------------------------------------------------
# 放在最前面。它是必需项，而下面的 pip 安装要下载一两 GB；把这个检查留到最后，
# 用户会先等十几分钟，再被告知少装一个东西。
step "检查 FFmpeg"
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
	echo "已安装：$(command -v ffmpeg)"
else
	# 静态编译、已签名的版本，解压即用。正式版优先，这个架构暂时没有正式版时用每日构建。
	echo "未找到 FFmpeg（转码和抽帧都要用它），正在下载到 ${TOOLS_BIN}…"
	for tool in ffmpeg ffprobe; do
		got=0
		for channel in release snapshot; do
			if download "https://ffmpeg.martin-riedl.de/redirect/latest/macos/${ARCH_FFMPEG}/${channel}/${tool}.zip" \
				"${TOOLS_DIR}/${tool}.zip"; then
				got=1
				break
			fi
		done
		[[ "$got" -eq 1 ]] || fail "${tool} 下载失败，请检查网络后重新运行本脚本。"
		unzip -oq "${TOOLS_DIR}/${tool}.zip" -d "$TOOLS_BIN" || fail "${tool} 解压失败。"
		rm -f "${TOOLS_DIR}/${tool}.zip"
		chmod +x "${TOOLS_BIN}/${tool}"
		"${TOOLS_BIN}/${tool}" -version >/dev/null 2>&1 || fail "下载的 ${tool} 运行不了。"
	done
	echo "已安装：${TOOLS_BIN}/ffmpeg"
fi

# --- 2. Python 虚拟环境 ----------------------------------------------------
# 必须 3.10 以上。macOS 自带的是 Xcode 命令行工具里的 Python 3.9，用它建出来的
# 环境会在装 pyobjc-framework-Vision 时尝试从源码编译并失败，而那个报错完全看不
# 出根因是版本太旧。所以这里主动挑一个够新的解释器。
step "准备 Python 虚拟环境"

python_ok() {
	[[ -x "$1" ]] || return 1
	# 没装开发者工具时，系统的 python3 运行一下就弹窗，碰都不能碰。
	if [[ "$1" == /usr/bin/python3* ]] && ! has_dev_tools; then
		return 1
	fi
	"$1" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else 1)' >/dev/null 2>&1
}
# 上限 3.14：依赖（onnxruntime、ctranslate2、faster-whisper）只给 3.10 到 3.14 出了
# 预编译包，更新的 Python（比如刚出的 3.15）会在 pip 深处报「onnxruntime 找不到匹配
# 的版本」，看不出是版本的问题。这几个包给下一个 Python 出包后再放宽；
# launchers/windows/setup-local.ps1 有同样的上限。

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
	if "$VENV_PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
		# 比 3.14 新：多半是上一次用新 Python 装失败留下的，里面只有依赖包，重建。
		echo "已有的虚拟环境是 $("$VENV_PY" -V 2>&1)，依赖装不上，删掉重建…"
		rm -rf "$VENV"
	else
		fail "已存在的虚拟环境是 $("$VENV_PY" -V 2>&1)，低于 3.10。
请删除它再重新运行本脚本：rm -rf ${VENV}"
	fi
fi

if [[ ! -x "$VENV_PY" ]]; then
	BASE_PY="$(best_python || true)"
	if [[ -z "$BASE_PY" ]]; then
		# 没有合格的 Python 就用 uv 装一个到用户目录：不要密码，不碰系统。
		echo "没有找到 3.10 到 3.14 之间的 Python，用 uv 装一个到 ${TOOLS_DIR}…"
		if [[ ! -x "${TOOLS_BIN}/uv" ]]; then
			download "https://github.com/astral-sh/uv/releases/latest/download/uv-${ARCH_UV}-apple-darwin.tar.gz" \
				"${TOOLS_DIR}/uv.tar.gz" || fail "uv 下载失败，请检查网络后重新运行本脚本。"
			tar -xzf "${TOOLS_DIR}/uv.tar.gz" -C "$TOOLS_DIR" || fail "uv 解压失败。"
			cp "${TOOLS_DIR}/uv-${ARCH_UV}-apple-darwin/uv" "${TOOLS_BIN}/uv"
			rm -rf "${TOOLS_DIR}/uv.tar.gz" "${TOOLS_DIR}/uv-${ARCH_UV}-apple-darwin"
		fi
		# 两个目录都指进数据目录：不设 UV_PYTHON_BIN_DIR 的话，uv 会往 ~/.local/bin 放快捷方式。
		export UV_PYTHON_INSTALL_DIR="${TOOLS_DIR}/python"
		export UV_PYTHON_BIN_DIR="${TOOLS_DIR}/python/bin"
		"${TOOLS_BIN}/uv" python install 3.12 || fail "用 uv 安装 Python 失败，请检查网络后重新运行本脚本。"
		BASE_PY="$("${TOOLS_BIN}/uv" python find 3.12)" || fail "uv 装好了 Python，但找不到它。"
	fi
	echo "使用 ${BASE_PY}（$("$BASE_PY" -V 2>&1)）创建 ${VENV}"
	"$BASE_PY" -m venv "$VENV" || fail "创建虚拟环境失败。"
fi
echo "虚拟环境：${VENV}（$("$VENV_PY" -V 2>&1)）"

# --- 3. Python 依赖 --------------------------------------------------------
step "安装 Python 依赖（体积较大，首次约需十几分钟）"
# 先探 PyPI：连不上或明显慢就先用清华源；失败一次就换另一个再试。用户自己设了
# PIP_INDEX_URL 的按他的来。
PYPI_INDEX="https://pypi.org/simple"
TUNA_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
PIP_INDEX=""
if [[ -z "${PIP_INDEX_URL:-}" ]]; then
	# 一个 2 MB 的包，两边路径相同。
	wheel="packages/8a/6a/19e9fe04fca059ccf770861c7d5721ab4c2aebc539889e97c7977528a53b/pip-24.0-py3-none-any.whl"
	PIP_INDEX="$PYPI_INDEX"
	if [[ "$(ff_order "https://files.pythonhosted.org/${wheel}" "https://pypi.tuna.tsinghua.edu.cn/${wheel}" | head -n 1)" == *tuna* ]]; then
		PIP_INDEX="$TUNA_INDEX"
		echo "PyPI 连不上或太慢，Python 依赖从国内镜像（清华源）下载。"
	fi
fi
# 依赖用 uv 装（固定版本，自己用 pip 从同一个源装上）：并行下载、几秒解出版本，比 pip
# 快得多（王愉超 2026-11-21）。uv 不读 PIP_INDEX_URL，所以源总是显式传。uv 装不上时照旧用 pip。
UV_VERSION="0.13.7"
USE_UV=0
pip_tool() {
	local index="$1" arg
	shift
	if [[ "$USE_UV" -eq 1 ]]; then
		local args=()
		for arg in "$@"; do [[ "$arg" == "--disable-pip-version-check" ]] || args+=("$arg"); done
		"$VENV_PY" -m uv pip install "${args[@]}" --python "$VENV_PY" ${index:+--index-url "$index"}
	else
		"$VENV_PY" -m pip install "$@" ${index:+--index-url "$index"}
	fi
}
pip_install() {
	local index="${PIP_INDEX:-${PIP_INDEX_URL:-}}"
	pip_tool "$index" "$@" && return 0
	[[ -n "$PIP_INDEX" ]] || return 1
	local other="$TUNA_INDEX"
	[[ "$PIP_INDEX" == "$TUNA_INDEX" ]] && other="$PYPI_INDEX"
	echo "下载失败，换 $(ff_host "$other") 重试…"
	pip_tool "$other" "$@"
}
# 先升 pip：老 pip 拿不到 pyobjc 的预编译包，会转去编译源码然后失败（uv 装不上时还要靠它）。
pip_install --quiet --upgrade pip || fail "升级 pip 失败。"
if pip_install --quiet "uv==${UV_VERSION}" && "$VENV_PY" -m uv --version >/dev/null 2>&1; then
	USE_UV=1
else
	echo "uv 没装上，改用 pip 安装依赖（慢一些）。"
fi
# 有本平台的锁定文件时按它装：那是一份实际测过的版本组合，requirements-local.txt
# 只写下限，隔几周装出来的就是另一套版本。目前只冻结了 macOS arm64 的；别的平台
# 落回 requirements-local.txt。
REQUIREMENTS="${REPO}/requirements-local.txt"
case "$(uname -s)" in
Darwin) LOCK_PLATFORM="macos" ;;
*) LOCK_PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')" ;;
esac
LOCK_FILE="${REPO}/requirements-local.lock.${LOCK_PLATFORM}-$(uname -m).txt"
if [[ -f "$LOCK_FILE" ]]; then
	echo "按锁定文件安装：$(basename "$LOCK_FILE")"
	REQUIREMENTS="$LOCK_FILE"
fi
pip_install -r "$REQUIREMENTS" ||
	fail "安装 Python 依赖失败，请看上面的报错。"

# yt-dlp 不进锁定文件，单独装最新版：抖音、B 站、YouTube 改版后旧版本会整个平台
# 下载失败。装不上不算安装失败——只影响视频链接，上传本地文件照常可用，而且之后
# 每次启动都会再试（scripts/update_yt_dlp.py）。
echo "安装/更新 yt-dlp（视频链接下载器）"
if ! pip_install -U --disable-pip-version-check yt-dlp; then
	echo "⚠ yt-dlp 没有装上或没能更新到最新版（多半是网络问题）。"
	echo "  上传本地文件不受影响；视频链接功能要等它装好。之后每次启动会自动再试，"
	echo "  也可以手动运行：${VENV_PY} scripts/update_yt_dlp.py --force"
fi

# Apple Silicon 上的加速转录引擎由 requirements-local.txt 里的平台标记自动决定，
# 不需要像 Windows 那样单独装一份 GPU 运行库。这里只是把实际走的那条路说出来。
if "$VENV_PY" -c 'import importlib.util,sys; sys.exit(0 if importlib.util.find_spec("mlx_whisper") else 1)' 2>/dev/null; then
	echo "转录引擎：mlx-whisper（Apple Silicon 加速）"
else
	echo "转录引擎：faster-whisper（CPU）"
fi

# --- 4. 前端 ---------------------------------------------------------------
# 用发布包装的，带着这一版发版时构建好的界面（release.yml，
# frontend/dist-local/.release-version 和 VERSION 一致），跳过 Node.js、npm 和构建。
# git 检出的、或这之前的版本，照旧在本机构建。
if [[ -f "${REPO}/frontend/dist-local/local.html" && -f "${REPO}/frontend/dist-local/.release-version" &&
	"$(tr -d '[:space:]' <"${REPO}/frontend/dist-local/.release-version")" == "$(tr -d '[:space:]' <"${REPO}/VERSION")" ]]; then
	step "界面"
	echo "用这一版发布时构建好的界面，不用装 Node.js，也不用构建。"
else
	step "安装前端依赖并构建"
	# 只有构建界面要用 Node.js。机器上没有就下载官方的免安装包放进数据目录，只给这一步用。
	NODE_VERSION="v24.20.0"
	# 取自这个版本官方的 SHASUMS256.txt；npmmirror 上的是同一个文件。
	case "$ARCH_NODE" in
	arm64) NODE_SHA256="40e5607e5ecb3db9192723776da2d75d966260fc74a7a9e731c1bd67dda96bc8" ;;
	*) NODE_SHA256="9e5b2644cf107befb6aefca676b96d3296bc10138096f022ed378d6233ed81f4" ;;
	esac
	if ! command -v npm >/dev/null 2>&1; then
		NODE_HOME="${TOOLS_DIR}/node-${NODE_VERSION}-darwin-${ARCH_NODE}"
		if [[ ! -x "${NODE_HOME}/bin/npm" ]]; then
			echo "未找到 Node.js（构建界面要用它），正在下载免安装版到 ${TOOLS_DIR}…"
			archive="node-${NODE_VERSION}-darwin-${ARCH_NODE}.tar.gz"
			ff_verified_download "${TOOLS_DIR}/${archive}" "$NODE_SHA256" \
				"https://nodejs.org/dist/${NODE_VERSION}/${archive}" \
				"https://registry.npmmirror.com/-/binary/node/${NODE_VERSION}/${archive}" ||
				fail "Node.js 下载失败，请检查网络后重新运行本脚本。"
			tar -xzf "${TOOLS_DIR}/${archive}" -C "$TOOLS_DIR" || fail "Node.js 解压失败。"
			rm -f "${TOOLS_DIR}/${archive}"
		fi
		export PATH="${NODE_HOME}/bin:${PATH}"
	fi

	# 先探 npmjs：连不上或明显慢就先用 npmmirror；失败一次就换另一个再试。用户自己配了
	# registry 的按他的来。
	NPMJS_REGISTRY="https://registry.npmjs.org"
	NPMMIRROR_REGISTRY="https://registry.npmmirror.com"
	NPM_REGISTRY=""
	if [[ -z "$(npm config get registry 2>/dev/null | grep -v registry.npmjs.org)" ]]; then
		NPM_REGISTRY="$NPMJS_REGISTRY"
		# 一个 5 MB 的包，两边路径相同。
		tarball="typescript/-/typescript-5.4.5.tgz"
		if [[ "$(ff_order "${NPMJS_REGISTRY}/${tarball}" "${NPMMIRROR_REGISTRY}/${tarball}" | head -n 1)" == "$NPMMIRROR_REGISTRY"* ]]; then
			NPM_REGISTRY="$NPMMIRROR_REGISTRY"
			echo "npmjs 连不上或太慢，前端依赖从国内镜像（npmmirror）下载。"
		fi
	fi
	npm_retry() {
		if [[ -z "$NPM_REGISTRY" ]]; then
			npm "$@"
			return
		fi
		npm "$@" --registry="$NPM_REGISTRY" && return 0
		local other="$NPMMIRROR_REGISTRY"
		[[ "$NPM_REGISTRY" == "$NPMMIRROR_REGISTRY" ]] && other="$NPMJS_REGISTRY"
		echo "下载失败，换 $(ff_host "$other") 重试…"
		npm "$@" --registry="$other"
	}

	cd "$REPO"
	if [[ -f package-lock.json ]]; then
		npm_retry ci || fail "npm ci 失败。"
	else
		npm_retry install || fail "npm install 失败。"
	fi

	# 构建脚本的名字在两套前端里不一样，按 package.json 实际声明的来，避免静默构建
	# 错目标。
	BUILD_SCRIPT="build:frontend"
	if "$VENV_PY" - "$REPO/package.json" <<-'PY' 2>/dev/null; then
	import json
	import sys

	scripts = json.load(open(sys.argv[1], encoding="utf-8")).get("scripts", {})
	sys.exit(0 if "build:frontend:local" in scripts else 1)
	PY
		BUILD_SCRIPT="build:frontend:local"
	fi
	echo "构建：npm run ${BUILD_SCRIPT}"
	npm run "$BUILD_SCRIPT" || fail "前端构建失败。"
fi

# --- 4b. 讲话人区分模型 ---------------------------------------------------
# 两个小模型（约 47MB），装好后设置里的「区分不同讲话人」直接能用。GitHub 和国内
# 镜像由脚本自己挑；下载失败不影响转录和笔记，所以只提示、不中断安装。
step "准备讲话人区分模型"
if ! "$VENV_PY" "${REPO}/scripts/fetch_diarization_models.py"; then
	echo "提示：讲话人区分模型没有下载成功，其余功能不受影响。网络恢复后运行这一句补上："
	echo "  \"${VENV_PY}\" \"${REPO}/scripts/fetch_diarization_models.py\""
fi

# --- 5. 转录模型 -----------------------------------------------------------
# 模型（1.5 到 3 GB）在后台下载，安装不用等它，装完就能打开页面（王愉超 2026-11-21：
# 国内真实网络下这是最慢的一步）。下完之前提交的录像在任务里显示「正在下载转录模型」，
# 下完接着转（local_stt._download_model_if_missing）；下载源由脚本自己在 huggingface.co
# 和 hf-mirror.com 之间挑。输出写进数据目录的 logs。
step "准备转录模型"
if [[ "$SKIP_MODEL" -eq 1 ]]; then
	echo "按要求跳过。第一次转录时会自动下载。"
else
	"$VENV_PY" "${REPO}/scripts/stt_model.py" fetch --background "${DATA_DIR}/logs/model-download.log" ||
		echo "转录模型没能开始后台下载，第一次转录时会自动下载。"
	echo "转录模型（1.5 到 3 GB）不用等它：装完就能打开页面，下完之前提交的录像会等它下完再转。"
fi

# --- 6. Agent 接口的访问令牌 -----------------------------------------------
# 用户的 agent 通过 MCP 工具调用本应用，那个接口要一个令牌才开。这里生成一串随机
# 字符写进 .env；后端和 MCP 服务都从这个文件读，两边自然一致，没人需要手抄。
step "开启 Agent 接口"
ENV_FILE="${REPO}/.env"
if grep -Eq '^[[:space:]]*FLUENTFLOW_ACCESS_TOKEN=[^[:space:]]+' "$ENV_FILE" 2>/dev/null; then
	echo "已有访问令牌（${ENV_FILE}）。"
else
	# od 只读 24 个字节就停，不会像 tr | head 那样被管道提前掐断、在 pipefail 下让脚本退出。
	TOKEN="$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')"
	[[ -s "$ENV_FILE" && -n "$(tail -c 1 "$ENV_FILE")" ]] && echo "" >>"$ENV_FILE"
	echo "FLUENTFLOW_ACCESS_TOKEN=${TOKEN}" >>"$ENV_FILE"
	echo "已生成访问令牌，写入 ${ENV_FILE}。"
fi

# --- 7. 就绪检查 -----------------------------------------------------------
step "启动前检查"
"$VENV_PY" "${REPO}/scripts/check_local_readiness.py" ||
	fail "环境未就绪，请按上面的提示处理后重新运行本脚本。"

# --- 8. 桌面启动器 ---------------------------------------------------------
if [[ "$SKIP_DESKTOP" -eq 1 ]]; then
	echo ""
	echo "✓ 安装完成（按要求跳过了桌面启动器）。"
	port="$(ff_setting "$REPO" FLUENTFLOW_LOCAL_PORT)"
	echo "  手动启动：${VENV_PY} -m uvicorn backend.local_main:app --host 127.0.0.1 --port ${port:-8000}"
	exit 0
fi

step "生成桌面启动器"
bash "${SCRIPT_DIR}/install-local-to-desktop.sh" || fail "生成桌面启动器失败。"

echo ""
echo "✓ 安装完成。双击桌面上的「FluentFlow Local」即可启动。"
echo "  AI 与飞书凭据在应用的设置页里填写。"
