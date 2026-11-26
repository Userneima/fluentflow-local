#!/bin/bash
# FluentFlow Local 一条命令安装（macOS）。给 agent 用，人也可以直接跑：
#
#   curl -fsSL https://raw.githubusercontent.com/Userneima/fluentflow-local/main/install.sh | bash
#
# 国内用 npmmirror 上随每个发布版发的同一份：
#   curl -fsSL https://registry.npmmirror.com/fluentflow-local/latest/files/install.sh | bash
#
# 依次做完：下载最新发布版到 ~/fluentflow-local、运行安装脚本、启动 FluentFlow、
# 把它的 MCP 工具注册到本机的 Claude Code 和 Codex、自检。全程不要密码，不弹系统窗口，
# 不调用 git。目录已经存在时沿用里面的代码，只把后面几步重做一遍。
#
# 可选环境变量：
#   FLUENTFLOW_INSTALL_DIR   装到哪里，默认 ~/fluentflow-local
#   FLUENTFLOW_INSTALL_REF   装哪个版本的标签（如 v0.7.0），默认最新发布版
#   FLUENTFLOW_INSTALL_SOURCE 已经在本机的发布版（解压好的文件夹或 .zip），从它安装或更新，不下载
# 其余参数原样交给 launchers/macos/setup-local.sh（如 --skip-model）。
set -euo pipefail

REPO_SLUG="Userneima/fluentflow-local"

# 读 .env、判断位置的几个函数，和 launchers/macos/fluentflow-env.sh 里的一字不差
# （tests/test_install_places.py 守着）：更新换代码之前，新旧两份的脚本都还靠不上
# （旧版本可能没有这些函数，新版本还没放好）。
# .env 里某个名字的值，不看环境变量，照服务（backend/core/runtime_paths.py 的 read_env_file）
# 的读法：去掉开头的 BOM；引号里的转义照它解；${NAME} 和 ${NAME:-默认值} 先看环境变量，
# 再看这一行之前的同名行（不是文件里最后那行）。照它读不准的，值前面加上 $?，ff_place
# 看到 $ 就当读不准：某一行的引号在本行没闭合（服务会把下一行也读进这个值，那一行
# 也就不再是一项设置），这一行和它之后的都算；文件不是 UTF-8，或值里有替换字符 U+FFFD。
# 第三个参数：只看这一行之前的（展开引用时用）。
ff_env_file_value() {
	local repo="$1" key="$2" before="${3:-}" line number=0 broken=0 found="" found_at=0 value
	[[ -f "${repo}/.env" ]] || return 0
	# 不是 UTF-8 的 .env 服务读不了，这里也就说不准任何一项。
	if command -v iconv >/dev/null 2>&1 && ! iconv -f UTF-8 -t UTF-8 "${repo}/.env" >/dev/null 2>&1; then
		printf '%s' '$?.env is not UTF-8'
		return 0
	fi
	while IFS= read -r line || [[ -n "$line" ]]; do
		number=$((number + 1))
		[[ -n "$before" && "$number" -ge "$before" ]] && break
		[[ "$number" -eq 1 ]] && line="${line#$'\xef\xbb\xbf'}"
		[[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$ ]] || continue
		value="${BASH_REMATCH[3]}"
		if [[ "${BASH_REMATCH[2]}" == "$key" ]]; then
			found="$value"
			found_at="$number"
		fi
		value="${value#"${value%%[![:space:]]*}"}"
		if [[ "$broken" -eq 0 && ( "$value" == \"* || "$value" == \'* ) ]] && ! ff_unquote "$value" >/dev/null; then
			broken="$number"
		fi
	done <"${repo}/.env"
	[[ "$found_at" -gt 0 ]] || return 0
	value="${found#"${found%%[![:space:]]*}"}"
	if [[ "$broken" -gt 0 && "$found_at" -ge "$broken" ]]; then
		printf '$?%s' "$value"
		return 0
	fi
	case "$value" in
	\"* | \'*) value="$(ff_unquote "$value")" ;;
	*) value="$(printf '%s' "$value" | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//')" ;;
	esac
	# 引用一段段拼起来，不用 ${value/…/…} 替换：bash 5.2 起替换文字里的 & 代表匹配到的
	# 那段，值里有 & 时（R&D）原来的循环永远停不下来。展开出来的文字不再展开，和服务一样。
	local rest="$value" ref ref_name ref_default ref_value
	value=""
	while [[ "$rest" =~ \$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\} ]]; do
		ref="${BASH_REMATCH[0]}" ref_name="${BASH_REMATCH[1]}" ref_default="${BASH_REMATCH[3]}"
		value="${value}${rest%%"$ref"*}"
		rest="${rest#*"$ref"}"
		ref_value="${!ref_name:-}"
		[[ -n "$ref_value" ]] || ref_value="$(ff_env_file_value "$repo" "$ref_name" "$found_at")"
		value="${value}${ref_value:-$ref_default}"
	done
	value="${value}${rest}"
	# U+FFFD：别的程序按错的编码读过再存回来的。
	[[ "$value" != *$'\xef\xbf\xbd'* ]] || value="\$?${value}"
	printf '%s' "$value"
}

# 开头带引号的值里引号中间那段，照服务的规则解转义（单引号里只有 \\ 和 \'，双引号里还有
# \n \t 这些）；引号在本行没闭合时返回 1。
ff_unquote() {
	local text="$1" quote="${1:0:1}" out="" i=1 char next
	while [[ "$i" -lt "${#text}" ]]; do
		char="${text:i:1}"
		if [[ "$char" == "\\" && $((i + 1)) -lt "${#text}" ]]; then
			next="${text:i+1:1}"
			if [[ "$quote" == "'" ]]; then
				case "$next" in "\\" | "'") ;; *) next="" ;; esac
			else
				case "$next" in
				n) next=$'\n' ;; r) next=$'\r' ;; t) next=$'\t' ;; a) next=$'\a' ;; b) next=$'\b' ;; f) next=$'\f' ;; v) next=$'\v' ;;
				"\\" | "'" | '"') ;;
				*) next="" ;;
				esac
			fi
			if [[ -n "$next" ]]; then
				out="${out}${next}"
				i=$((i + 2))
				continue
			fi
		fi
		if [[ "$char" == "$quote" ]]; then
			printf '%s' "$out"
			return 0
		fi
		out="${out}${char}"
		i=$((i + 1))
	done
	return 1
}

ff_setting() {
	local repo="$1" name="$2"
	if [[ -n "${!name:-}" ]]; then
		printf '%s' "${!name}"
	else
		ff_env_file_value "$repo" "$name"
	fi
}

# 去掉多余的 /、. 和 ..，只按字面算，不碰磁盘（服务的 Path 也不解开链接）。
ff_normalize() {
	local part parts out=()
	IFS=/ read -r -a parts <<<"$1"
	for part in ${parts[@]+"${parts[@]}"}; do
		case "$part" in
		"" | .) ;;
		..) [[ ${#out[@]} -gt 0 ]] && unset 'out[${#out[@]}-1]' && out=(${out[@]+"${out[@]}"}) ;;
		*) out+=("$part") ;;
		esac
	done
	local IFS=/
	printf '/%s' "${out[*]-}"
}

# 一个绝对路径最终落在哪个目录：一段段走，碰到已存在的就用 cd 进去（pwd -P 解开链接），
# 碰到 .. 就真的往上一级。服务拿到路径后是系统按实际目录打开的（/tmp/link/../data 里的 ..
# 从链接指向的目录算），只按字面化简会和服务放在两个地方。
ff_physical_path() {
	local rest="${1#/}" cur="" part
	while [[ -n "$rest" ]]; do
		part="${rest%%/*}"
		if [[ "$rest" == */* ]]; then rest="${rest#*/}"; else rest=""; fi
		case "$part" in
		"" | .) continue ;;
		..)
			if [[ -n "$cur" ]]; then
				if [[ -d "$cur" ]]; then cur="$(cd "$cur/.." 2>/dev/null && pwd -P)" || cur="${cur%/*}"; else cur="${cur%/*}"; fi
			fi
			continue
			;;
		esac
		if [[ -z "$cur" ]]; then cur="/${part}"; else cur="${cur}/${part}"; fi
		[[ -d "$cur" ]] && cur="$(cd "$cur" 2>/dev/null && pwd -P)" || true
	done
	printf '%s' "${cur:-/}"
}

# ff_place <代码目录> <设置的值>：服务把这个设置放在哪，打印绝对路径。开头的 ~ 是家目录；
# 相对路径从代码目录算起，服务在代码目录里运行（启动器先 cd 进去）。值里展开之后还剩 $
# 时（$HOME 不带花括号，服务照字面用）看不准，返回 1：调用方当作「可能在代码目录里」。
ff_place() {
	local folder="$1" value="$2"
	case "$value" in
	"~") value="$HOME" ;;
	"~/"*) value="${HOME}/${value#"~/"}" ;;
	esac
	[[ "$value" == /* ]] || value="${folder}/${value}"
	value="$(ff_physical_path "$value")"
	printf '%s' "$value"
	[[ "$value" != *'$'* ]]
}

# Codex 的配置和 skill 在哪：设了 CODEX_HOME 就在那里，否则在 ~/.codex。
ff_codex_home() {
	printf '%s' "${CODEX_HOME:-${HOME}/.codex}"
}

ff_lower() {
	printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

# 已存在的那段上级目录换成解开链接后的样子（/tmp 是 /private/tmp），其余照抄。
ff_physical() {
	local head="$1" tail="" real
	[[ "$head" == /* ]] || { printf '%s' "$1"; return 0; }
	while [[ "$head" != "/" && ! -d "$head" ]]; do
		tail="/${head##*/}${tail}"
		head="${head%/*}"
		[[ -n "$head" ]] || head="/"
	done
	real="$(cd "$head" 2>/dev/null && pwd -P)" || real="$head"
	printf '%s%s' "${real%/}" "$tail"
}

# ff_inside <路径> <目录>：路径在目录里或就是它。照字面比一次、解开链接再比一次，不分大小写
# （Mac 默认的磁盘格式不分）；多判成「在里面」只会让更新停下、卸载少删，不会多删。
ff_inside() {
	local path folder
	path="$(ff_lower "$1")" folder="$(ff_lower "$2")"
	[[ "$path" == "$folder" || "$path" == "$folder/"* ]] && return 0
	path="$(ff_lower "$(ff_physical "$1")")" folder="$(ff_lower "$(ff_physical "$2")")"
	[[ "$path" == "$folder" || "$path" == "$folder/"* ]]
}

# 更新时挪到旁边的那几份（.new .old .failed）里放一个标记，写明它是在哪个位置被放下的。
# 只有标记写的就是它现在的位置，才是更新留下的、可以删；用户把代码目录复制或改名成
# app.old 时，标记写的是别的位置（或者没有标记），不动。正在用的那份里没有标记。
ff_mark_update_copy() {
	printf 'Set aside by a FluentFlow Local update; the next update or the uninstaller removes it.\n%s\n' "$1" >"$1/.fluentflow-update"
}

ff_update_marker_says() {
	local said
	[[ -f "$1/.fluentflow-update" ]] || return 1
	said="$(tail -n 1 "$1/.fluentflow-update")"
	[[ -n "$said" && "$(ff_lower "$(ff_physical "$said")")" == "$(ff_lower "$(ff_physical "$1")")" ]]
}

# 停掉从某个代码目录运行的程序：命令行以这个目录里虚拟环境的 python 开头的，或者是一个
# python 直接跑这个目录里的脚本（Homebrew 的 Python 会换成框架里的 Python 再跑，命令行就不再
# 以虚拟环境的路径开头）。只在参数别处提到这个目录的程序不算，本脚本自己也不算。
ff_stop_folder_programs() {
	local folder pattern pid
	folder="$(printf '%s' "${1%/}" | sed -e 's/[].*^$+?(){}|\\[]/\\&/g')"
	[[ "${#folder}" -gt 1 ]] || return 1
	pattern="^(${folder}/(\.venv|venv)/bin/|[^ ]*/[Pp]ython[0-9.]* ${folder}/)"
	for pid in $(pgrep -f -- "$pattern" 2>/dev/null || true); do
		[[ "$pid" == "$$" || "$pid" == "$PPID" ]] || kill "$pid" 2>/dev/null || true
	done
	sleep 1
}

PORT="${FLUENTFLOW_LOCAL_PORT:-8000}"

# 正在运行的 FluentFlow 把它的代码目录写在 /health 里。装在自选文件夹、没设
# FLUENTFLOW_INSTALL_DIR 再运行一次安装命令时，靠它接着更新那一份；不然这里会在
# ~/fluentflow-local 另装一份，看上去像更新了，原来的任务和设置却不在页面里。
ff_code_dir_from_service() {
	local health dir
	health="$(curl -s --max-time 3 "http://127.0.0.1:${PORT}/health" 2>/dev/null || true)"
	[[ "$health" == *'"edition":"local"'* ]] || return 1
	dir="$(printf '%s' "$health" | sed -n 's/.*"code_dir":"\([^"]*\)".*/\1/p')"
	[[ -n "$dir" && -f "${dir}/backend/local_main.py" ]] || return 1
	printf '%s' "$dir"
}

# 装到哪：展开 ~，相对路径从当前目录算起，去掉结尾的 /。后面要挪进挪出这个目录，
# 也要拿它和 .env 里的位置比。
DIR="${FLUENTFLOW_INSTALL_DIR:-}"
if [[ -z "$DIR" && ! -f "${HOME}/fluentflow-local/backend/local_main.py" ]]; then
	if found="$(ff_code_dir_from_service)"; then
		echo "正在运行的 FluentFlow 装在 ${found}，接着更新那一份（要装到别处就设 FLUENTFLOW_INSTALL_DIR）。"
		DIR="$found"
	fi
fi
DIR="${DIR:-${HOME}/fluentflow-local}"
case "$DIR" in
"~") DIR="$HOME" ;;
"~/"*) DIR="${HOME}/${DIR#"~/"}" ;;
esac
[[ "$DIR" == /* ]] || DIR="${PWD}/${DIR}"
# 按实际目录算（链接和 .. 交给磁盘），和服务打开它的方式一致。
DIR="$(ff_physical_path "$DIR")"

step() { echo ""; echo "== $1 =="; }
fail() {
	echo "" >&2
	echo "✗ $1" >&2
	exit 1
}

[[ "$(uname -s)" == "Darwin" ]] || fail "这个脚本只用于 macOS。Windows 请运行 install.ps1。"

# 起这个目录里的服务：有桌面启动器就打开它，按要求没生成时直接在后台起。
start_service() {
	if [[ -d "${HOME}/Desktop/FluentFlow Local.app" ]]; then
		open "${HOME}/Desktop/FluentFlow Local.app"
		return 0
	fi
	local py="${DIR}/.venv/bin/python"
	[[ -x "${DIR}/venv/bin/python" ]] && py="${DIR}/venv/bin/python"
	mkdir -p "${HOME}/Library/Logs/FluentFlow"
	(cd "$DIR" && nohup "$py" -m uvicorn backend.local_main:app --host 127.0.0.1 --port "$PORT" \
		>>"${HOME}/Library/Logs/FluentFlow/local.log" 2>&1 &)
}

# 更新到一半失败时把旧版本换回来，再照常报错退出。
SWAPPED=0
fail_plain() {
	echo "" >&2
	echo "✗ $1" >&2
	exit 1
}
fail() {
	if [[ "${SWAPPED:-0}" -eq 1 ]]; then
		SWAPPED=0
		echo "更新失败，换回 ${CURRENT}…" >&2
		stop_folder_programs "$DIR"
		rm -rf "${DIR}.failed"
		if [[ -e "$DIR" ]]; then mv "$DIR" "${DIR}.failed" && { ff_mark_update_copy "${DIR}.failed" || true; }; fi
		mv "${DIR}.old" "$DIR"
		rm -f "${DIR}/.fluentflow-update"
		# 更新前在跑的服务换回后接着跑（没有桌面启动器的安装也一样）。
		if [[ "${STOPPED_OLD:-0}" -eq 1 || -d "${HOME}/Desktop/FluentFlow Local.app" ]]; then start_service; fi
		fail_plain "$1
更新到 ${LATEST} 没有成功，已换回 ${CURRENT}（失败的那份在 ${DIR}.failed）。"
	fi
	fail_plain "$1"
}

# 系统设置里的代理：curl 不看它。和 launchers/macos/fluentflow-env.sh 的
# ff_use_system_proxy 同一条规则（那份这时还不在这台机器上）。
if [[ -z "${HTTPS_PROXY:-}${HTTP_PROXY:-}${https_proxy:-}${http_proxy:-}" ]]; then
	_proxy="$(scutil --proxy 2>/dev/null || true)"
	_host="$(printf '%s\n' "$_proxy" | awk '/ HTTPSEnable : 1/ {on=1} on && / HTTPSProxy :/ {print $3}')"
	_port="$(printf '%s\n' "$_proxy" | awk '/ HTTPSEnable : 1/ {on=1} on && / HTTPSPort :/ {print $3}')"
	if [[ -n "$_host" && -n "$_port" ]]; then
		export HTTP_PROXY="http://${_host}:${_port}" HTTPS_PROXY="http://${_host}:${_port}" NO_PROXY="127.0.0.1,localhost,::1"
		echo "用系统设置里的代理：${HTTPS_PROXY}"
	fi
fi

# --- 1. 代码 ----------------------------------------------------------------
# 更新用到的旁边几个文件夹（.new .old .failed）：已经存在的，只有标记写着就是这个位置的
# （之前某次更新放下的，见 ff_mark_update_copy）才删；用户自己复制或改名出来的不动。
assert_own_leftover() {
	if [[ -e "$1" ]] && ! ff_update_marker_says "$1"; then
		fail_plain "$1 已存在，不是 FluentFlow 的更新留下的。它可能是上一次更新没做完时留下的拷贝（写到一半就停了，没来得及写标记）。确认不是你的文件夹后删掉它，再运行一次。什么都没改。"
	fi
	return 0
}

step "获取代码"
# 上一次更新在换文件夹的中间被关掉或断电：旧版本挪到了 ${DIR}.old，新代码还没放进来，
# ${DIR} 这时不存在。先把它放回去，再照常判断要不要更新；不然这次会当成没装过，另装一份，
# 旧版本一直躺在旁边，页面看到的也是新的空数据。
if [[ ! -e "$DIR" ]] && ff_update_marker_says "${DIR}.old"; then
	echo "上一次更新在换文件夹时被打断，把原来的版本从 ${DIR}.old 放回 ${DIR}…"
	mv "${DIR}.old" "$DIR"
	rm -f "${DIR}/.fluentflow-update"
fi
# 已经装过、又有新发布版时原地更新：新代码先放在旁边，带上原来的 .env，停掉服务后才换
# 进去；接着的安装步骤补齐新版本要的依赖、讲话人模型和转录模型；安装或重启失败就把旧版本
# 换回来（见 fail）。git 检出是开发者自己的，不动。
HAVE_CODE=0
IS_GIT=0
UPDATING=0
SWAPPED=0
CURRENT=""
LATEST=""
if [[ -f "${DIR}/backend/local_main.py" ]]; then
	HAVE_CODE=1
	CURRENT="$(tr -d '[:space:]' <"${DIR}/VERSION" 2>/dev/null || true)"
	[[ -e "${DIR}/.git" ]] && IS_GIT=1
fi
LOCAL_SOURCE="${FLUENTFLOW_INSTALL_SOURCE:-}"
if [[ "$IS_GIT" -eq 1 ]]; then
	echo "沿用 git 检出的代码：${DIR}（更新请 git pull，再运行一次本命令）"
elif [[ -n "$LOCAL_SOURCE" ]]; then
	if [[ "$LOCAL_SOURCE" == *.zip ]]; then
		SRC_TMP="$(mktemp -d)"
		unzip -q "$LOCAL_SOURCE" -d "$SRC_TMP" || fail "解不开 ${LOCAL_SOURCE}。"
		LOCAL_SOURCE="$SRC_TMP"
		[[ -f "${LOCAL_SOURCE}/backend/local_main.py" ]] || LOCAL_SOURCE="$(find "$SRC_TMP" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
	fi
	[[ -f "${LOCAL_SOURCE}/backend/local_main.py" ]] || fail "FLUENTFLOW_INSTALL_SOURCE 不是 FluentFlow Local 的发布版：${FLUENTFLOW_INSTALL_SOURCE}"
	[[ "$HAVE_CODE" -eq 0 && -e "$DIR" ]] && fail "${DIR} 已存在但不是 FluentFlow Local 的代码目录。请换一个位置（FLUENTFLOW_INSTALL_DIR）。"
	LATEST="$(tr -d '[:space:]' <"${LOCAL_SOURCE}/VERSION")"
	if [[ "$HAVE_CODE" -eq 1 && "$LATEST" != "$CURRENT" &&
		"$(printf '%s\n%s\n' "$CURRENT" "$LATEST" | sort -V | tail -n 1)" == "$LATEST" ]]; then
		UPDATING=1
		echo "把 FluentFlow Local 从 ${CURRENT} 更新到 ${LATEST}（来自 ${LOCAL_SOURCE}）。"
	elif [[ "$HAVE_CODE" -eq 1 ]]; then
		echo "${DIR} 里的 ${CURRENT} 不比 ${LOCAL_SOURCE} 里的 ${LATEST} 旧。"
		[[ -n "${SRC_TMP:-}" ]] && rm -rf "$SRC_TMP"
	fi
	if [[ "$HAVE_CODE" -eq 0 || "$UPDATING" -eq 1 ]]; then
		TARGET="$DIR"
		if [[ "$UPDATING" -eq 1 ]]; then
			TARGET="${DIR}.new"
			assert_own_leftover "$TARGET"
			rm -rf "$TARGET"
		fi
		mkdir -p "$(dirname "$TARGET")"
		# 写到一半停下（磁盘满、窗口被关）的拷贝没有标记，下次更新会停下说明它；这次先自己删掉。
		if ! cp -R "$LOCAL_SOURCE" "$TARGET"; then
			rm -rf "$TARGET"
			fail "拷贝到 ${TARGET} 没有写完。请检查磁盘空间后重新运行。什么都没改。"
		fi
		rm -rf "${TARGET}/.git" "${TARGET}/.venv" "${TARGET}/.env" "${TARGET}/.fluentflow-update"
		# 标记最后才写：整份拷贝在才写。
		[[ -f "${TARGET}/backend/local_main.py" ]] || { rm -rf "$TARGET"; fail "拷贝到 ${TARGET} 没有写完。请检查磁盘空间后重新运行。"; }
		if [[ "$UPDATING" -eq 1 ]]; then ff_mark_update_copy "$TARGET"; fi
		[[ -n "${SRC_TMP:-}" ]] && rm -rf "$SRC_TMP"
		echo "已放到 ${TARGET}"
	fi
else
	[[ "$HAVE_CODE" -eq 0 && -e "$DIR" ]] && fail "${DIR} 已存在但不是 FluentFlow Local 的代码目录。请换一个位置（FLUENTFLOW_INSTALL_DIR）。"
	# 发布版有两个来源：GitHub，和 npmmirror 上的 npm 包 fluentflow-local（见
	# scripts/stage_npm_package.py），后者在国内打得开。先探 GitHub 几秒，连不上或明显
	# 慢就先走 npmmirror，和其余下载同一条规则（launchers/macos/fluentflow-env.sh，这时
	# 还不在这台机器上）。
	TAG="${FLUENTFLOW_INSTALL_REF:-}"
	if [[ -z "$TAG" ]]; then
		# 读 releases/latest 的跳转地址，不走 GitHub API：API 对未登录请求有次数限制。
		TAG="$(curl -fsSI --connect-timeout 10 --max-time 15 "https://github.com/${REPO_SLUG}/releases/latest" 2>/dev/null |
			tr -d '\r' | sed -n 's#^[Ll]ocation: .*/tag/##p' | tail -1 || true)"
		[[ "$TAG" == v[0-9]* ]] || TAG=""
	fi
	GITHUB_ZIP=""
	[[ -n "$TAG" ]] && GITHUB_ZIP="https://github.com/${REPO_SLUG}/archive/refs/tags/${TAG}.zip"
	NPM_VERSION="${TAG#v}"
	NPM_META="$(curl -fsS --max-time 15 "https://registry.npmmirror.com/fluentflow-local/${NPM_VERSION:-latest}" 2>/dev/null || true)"
	# plutil 读 JSON：系统自带，不像 /usr/bin/python3 那样在没装开发者工具时弹窗。
	npm_field() { printf '%s' "$NPM_META" | plutil -extract "$1" raw -o - - 2>/dev/null; }
	LATEST="${TAG#v}"
	[[ -z "$LATEST" && -n "$NPM_META" ]] && LATEST="$(npm_field version)"
	if [[ "$HAVE_CODE" -eq 1 ]]; then
		if [[ -n "$LATEST" && -n "$CURRENT" && "$LATEST" != "$CURRENT" &&
			"$(printf '%s\n%s\n' "$CURRENT" "$LATEST" | sort -V | tail -n 1)" == "$LATEST" ]]; then
			UPDATING=1
			echo "把 FluentFlow Local 从 ${CURRENT} 更新到 ${LATEST}。"
		elif [[ -n "$LATEST" ]]; then
			echo "${DIR} 里的 ${CURRENT} 已经是最新发布版。"
		else
			echo "查不到最新版本，沿用 ${DIR} 里的 ${CURRENT}。"
		fi
	elif [[ -z "$GITHUB_ZIP" && -z "$NPM_META" ]]; then
		fail "连不上 github.com，也连不上 registry.npmmirror.com，请检查网络或代理。"
	fi
fi
if [[ "$IS_GIT" -eq 0 && -z "$LOCAL_SOURCE" && ( "$HAVE_CODE" -eq 0 || "$UPDATING" -eq 1 ) ]]; then
	TARGET="$DIR"
	if [[ "$UPDATING" -eq 1 ]]; then
		TARGET="${DIR}.new"
		assert_own_leftover "$TARGET"
		rm -rf "$TARGET"
	fi
	order="npm github"
	if [[ -n "$GITHUB_ZIP" ]]; then
		got="$(curl -sL -r 0-1048575 --max-time 5 -o /dev/null -w '%{size_download}' "$GITHUB_ZIP" 2>/dev/null || true)"
		[[ -z "$NPM_META" || "${got:-0}" -ge 1048576 ]] && order="github npm"
	fi
	WORK="$(mktemp -d)"
	trap 'rm -rf "$WORK"' EXIT
	unpacked=""
	for source in $order; do
		if [[ "$source" == github && -n "$GITHUB_ZIP" ]]; then
			echo "从 github.com 下载 ${TAG}…"
			if curl -fL --retry 2 --connect-timeout 20 -sS -o "${WORK}/src.zip" "$GITHUB_ZIP" &&
				unzip -q "${WORK}/src.zip" -d "${WORK}/src"; then
				unpacked="$(find "${WORK}/src" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
				# 这一版发版时构建好的界面（release.yml），安装脚本就不用装 Node.js、不用构建。
				# 旧版本没有这个附件，安装脚本照旧在本机构建。
				if curl -fL --retry 2 --connect-timeout 20 -sS -o "${WORK}/ui.zip" \
					"https://github.com/${REPO_SLUG}/releases/download/${TAG}/frontend-dist-local.zip" 2>/dev/null; then
					mkdir -p "${unpacked}/frontend/dist-local" && unzip -oq "${WORK}/ui.zip" -d "${unpacked}/frontend/dist-local"
				else
					echo "  （${TAG} 没有附带构建好的界面，安装时在本机构建）"
				fi
			fi
		elif [[ "$source" == npm && -n "$NPM_META" ]]; then
			echo "从 registry.npmmirror.com 下载 $(npm_field version)…"
			if curl -fL --retry 2 --connect-timeout 20 -sS -o "${WORK}/src.tgz" "$(npm_field dist.tarball)"; then
				digest="sha512-$(openssl dgst -sha512 -binary "${WORK}/src.tgz" | base64 | tr -d '\n')"
				if [[ "$digest" == "$(npm_field dist.integrity)" ]]; then
					mkdir -p "${WORK}/npm" && tar -xzf "${WORK}/src.tgz" -C "${WORK}/npm" && unpacked="${WORK}/npm/package/source"
				else
					echo "  下载的文件和校验值对不上。"
				fi
			fi
		fi
		[[ -n "$unpacked" && -f "${unpacked}/backend/local_main.py" ]] && break
		unpacked=""
		echo "  ${source} 没下成，换另一个来源。"
	done
	[[ -n "$unpacked" ]] || fail "代码从 github.com 和 registry.npmmirror.com 都没下成，请检查网络或代理。"
	mkdir -p "$(dirname "$DIR")"
	mv "$unpacked" "$TARGET"
	if [[ "$UPDATING" -eq 1 ]]; then ff_mark_update_copy "$TARGET"; fi
	echo "已放到 ${TARGET}"
fi

# 还在从某个目录运行的程序（服务、后台下载模型、yt-dlp 更新）。
# 不算这个脚本自己：它可能就是从那个目录里运行的。
stop_folder_programs() {
	ff_stop_folder_programs "$1" || true
}

if [[ "$UPDATING" -eq 1 ]]; then
	assert_own_leftover "${DIR}.old"
	assert_own_leftover "${DIR}.failed"
	# 数据、链接下载、模型若设在代码目录里面，会随旧代码一起被删：先挪出去再更新。
	# 数据目录的每一部分（任务库、原片、笔记、改过的转录稿、设置……）都能单独设位置。
	# 照服务的读法定位置（ff_setting、ff_place）：相对路径、${HOME} 这样的引用、~ 都算上；
	# 读不准的当作在里面。
	names="HF_HOME $(env | sed -n -E 's/^(FLUENTFLOW_[A-Z0-9_]*(DIR|PATH|CACHE))=.*/\1/p')"
	[[ -f "${DIR}/.env" ]] && names="$names $(sed -n -E 's/^[[:space:]]*(export[[:space:]]+)?(FLUENTFLOW_[A-Z0-9_]*(DIR|PATH|CACHE))[[:space:]]*=.*/\2/p' "${DIR}/.env")"
	for name in $(printf '%s\n' $names | sort -u); do
		# 装到哪的那个变量本身不算（它就是这份安装的位置）。
		[[ "$name" != FLUENTFLOW_INSTALL_DIR ]] || continue
		# 环境变量和 .env 里的同一项都看：之后由启动器起的服务不一定带着这个窗口的环境变量。
		for where in "${!name:-}" "$(ff_env_file_value "$DIR" "$name")"; do
			[[ -n "$where" ]] || continue
			if ! place="$(ff_place "$DIR" "$where")"; then
				rm -rf "${DIR}.new"
				fail_plain "${name}（${where}）照服务的读法看不准在哪（展开不了的 \$、引号没在本行闭合、不是 UTF-8）；更新会换掉代码目录，所以先停下。请把它改成完整路径，并确认在 ${DIR} 外面。什么都没改。"
			fi
			if ff_inside "$place" "$DIR"; then
				rm -rf "${DIR}.new"
				fail_plain "${name}（${where}，也就是 ${place}）在代码目录里面，更新会换掉这个目录。请先把它挪到 ${DIR} 外面。什么都没改。"
			fi
		done
	done
	# 端口和桌面启动器一样：环境变量优先，其次是 .env 里的同名行（新旧两份 .env 一样）。
	if [[ -z "${FLUENTFLOW_LOCAL_PORT:-}" && -f "${DIR}/.env" ]]; then
		env_port="$(grep -E '^[[:space:]]*(export[[:space:]]+)?FLUENTFLOW_LOCAL_PORT[[:space:]]*=' "${DIR}/.env" | tail -n 1 |
			sed -E "s/^[^=]*=[[:space:]]*['\"]?//; s/['\"]?[[:space:]]*(#.*)?$//" || true)"
		[[ "$env_port" =~ ^[0-9]+$ ]] && PORT="$env_port"
	fi
	OLD_PY="${DIR}/.venv/bin/python"
	[[ -x "${DIR}/venv/bin/python" ]] && OLD_PY="${DIR}/venv/bin/python"
	health="$(curl -s --max-time 2 "http://127.0.0.1:${PORT}/health" 2>/dev/null || true)"
	if [[ "$health" != *'"edition":"local"'* && -n "$(lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null || true)" &&
		"${FLUENTFLOW_UPDATE_FORCE:-}" != "1" ]]; then
		rm -rf "${DIR}.new"
		fail_plain "端口 ${PORT} 上的程序没有像 FluentFlow 那样应答（忙，或是别的程序）。稍后再运行一次；确认可以停掉时设 FLUENTFLOW_UPDATE_FORCE=1。什么都没改。"
	fi
	STOPPED_OLD=0
	if [[ "$health" == *'"edition":"local"'* ]]; then
		# 每个任务的 activity.busy 算（剪辑、写笔记时任务主状态已经是完成），和启动器的重启
		# 检查同一份脚本；-1 是问不到。
		busy="$("$OLD_PY" "${DIR}.new/launchers/macos/count_active_jobs.py" "http://127.0.0.1:${PORT}" 2>/dev/null || echo -1)"
		[[ "$busy" =~ ^-?[0-9]+$ ]] || busy=-1
		if [[ "$busy" -ne 0 && "${FLUENTFLOW_UPDATE_FORCE:-}" != "1" ]]; then
			rm -rf "${DIR}.new"
			if [[ "$busy" -lt 0 ]]; then
				fail "问不到正在运行的 FluentFlow 有没有任务在处理。确认没有之后设 FLUENTFLOW_UPDATE_FORCE=1 再运行一次。什么都没改。"
			fi
			fail "还有 ${busy} 个任务在处理，等它们做完再运行一次（或设 FLUENTFLOW_UPDATE_FORCE=1 先停掉，重启后接着做）。什么都没改。"
		fi
		echo "停止正在运行的 FluentFlow Local…"
		STOPPED_OLD=1
		pids="$(lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null || true)"
		# shellcheck disable=SC2086
		[[ -n "$pids" ]] && kill $pids 2>/dev/null || true
	fi
	stop_folder_programs "$DIR"
	# 可选的 pyannote 讲话人引擎不在标准依赖里；装过的，更新后照样装上。
	HAD_PYANNOTE=0
	compgen -G "$(dirname "$(dirname "$OLD_PY")")/lib/python3*/site-packages/pyannote" >/dev/null && HAD_PYANNOTE=1
	[[ -f "${DIR}/.env" ]] && cp -p "${DIR}/.env" "${DIR}.new/.env"
	rm -rf "${DIR}.old"
	cd "$(dirname "$DIR")"
	if ! mv "$DIR" "${DIR}.old"; then
		rm -rf "${DIR}.new"
		[[ "$STOPPED_OLD" -eq 1 ]] && start_service
		fail_plain "没能把 ${DIR} 挪开（有程序在用它）。关掉它再运行一次。什么都没改。"
	fi
	SWAPPED=1
	# set -e 停下的那些步骤也要先换回旧版本，包括下一步的 mv。
	trap 'fail "安装中途出错（install.sh 第 ${LINENO} 行）。"' ERR
	# 没写成标记时下次更新会停在它前面，不会删它：不值得为此失败。
	ff_mark_update_copy "${DIR}.old" || true
	mv "${DIR}.new" "$DIR"
	rm -f "${DIR}/.fluentflow-update"
fi

# --- 2. 安装 ----------------------------------------------------------------
bash "${DIR}/launchers/macos/setup-local.sh" "$@" || fail "安装步骤没有完成，请看上面的报错。"
if [[ "$UPDATING" -eq 1 && "${HAD_PYANNOTE:-0}" -eq 1 ]]; then
	echo "重新装上你装过的 pyannote 讲话人引擎…"
	"${DIR}/.venv/bin/python" -m pip install -q --disable-pip-version-check -r "${DIR}/requirements-speaker.txt" ||
		fail "重新装 pyannote 没有成功。"
fi

VENV_PY="${DIR}/.venv/bin/python"
[[ -x "${DIR}/venv/bin/python" ]] && VENV_PY="${DIR}/venv/bin/python"
MCP_SCRIPT="${DIR}/scripts/fluentflow_mcp_server.py"

# --- 3. 启动 ----------------------------------------------------------------
step "启动 FluentFlow Local"
healthy() { curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/health" 2>/dev/null | grep -q '"edition":"local"'; }
if healthy; then
	echo "已经在运行。"
else
	start_service
	for _ in $(seq 1 90); do
		healthy && break
		sleep 1
	done
	healthy || fail "服务没有在 90 秒内启动。日志：${HOME}/Library/Logs/FluentFlow/local.log"
	echo "已启动：http://127.0.0.1:${PORT}"
fi

# --- 4. MCP 工具 ------------------------------------------------------------
# MCP 服务自己从仓库的 .env 读访问令牌，所以注册时不带任何密钥。
# 已经注册过的：指向这一份就不动；指向另一份还在的安装也不动（那是有效的另一份）；
# 指向的文件已经不在了（那份删了、或换了位置）就注销再注册，留给 agent 的不能是失效条目。
# 注销要按它当初注册的作用域来，只按 user 删不掉项目级的。
step "注册 MCP 工具"
CODEX_HOME_DIR="$(ff_codex_home)"
CODEX_CONFIG="${CODEX_HOME_DIR}/config.toml"
CODEX_TOML="[mcp_servers.fluentflow-local]
command = \"${VENV_PY}\"
args = [\"${MCP_SCRIPT}\"]"
registered=0
# 默认端口时 MCP 服务自己就连得上；换过端口才需要告诉它。
MCP_ENV=()
[[ "$PORT" != "8000" ]] && MCP_ENV=(-e "FLUENTFLOW_API_BASE=http://127.0.0.1:${PORT}")
# claude mcp get 打印的作用域："Scope: User config …" 和最后一行
# "To remove this server, run: claude mcp remove fluentflow-local -s user"。
claude_scope_of() {
	printf '%s\n' "$1" | sed -n 's/.*claude mcp remove fluentflow-local -s \([A-Za-z]*\).*/\1/p' | tail -n 1
}
claude_target_of() {
	printf '%s\n' "$1" | sed -n 's/^[[:space:]]*Args:[[:space:]]*//p' | tail -n 1 | awk '{print $NF}'
}
if command -v claude >/dev/null 2>&1; then
	described="$(claude mcp get fluentflow-local 2>/dev/null || true)"
	if [[ -n "$described" && "$described" == *"${MCP_SCRIPT}"* ]]; then
		echo "Claude Code：已注册过 fluentflow-local（这一份），保持不动。"
	elif [[ -n "$described" ]]; then
		target="$(claude_target_of "$described")"
		if [[ -n "$target" && -e "$target" ]]; then
			echo "Claude Code：fluentflow-local 已注册到另一份安装（${target}），不动它。卸载那份后重新运行本命令，就会注册到这一份。"
		else
			claude mcp remove --scope "$(claude_scope_of "$described" || true)" fluentflow-local >/dev/null 2>&1 ||
				claude mcp remove --scope user fluentflow-local >/dev/null 2>&1 || true
			if claude mcp add --scope user fluentflow-local ${MCP_ENV[@]+"${MCP_ENV[@]}"} -- "$VENV_PY" "$MCP_SCRIPT" >/dev/null; then
				echo "Claude Code：原来那条指向的位置已经没了，已重新注册 fluentflow-local。"
			else
				echo "Claude Code：注册失败，可以手动运行：claude mcp add --scope user fluentflow-local -- \"${VENV_PY}\" \"${MCP_SCRIPT}\""
			fi
		fi
	elif claude mcp add --scope user fluentflow-local ${MCP_ENV[@]+"${MCP_ENV[@]}"} -- "$VENV_PY" "$MCP_SCRIPT" >/dev/null; then
		echo "Claude Code：已注册 fluentflow-local。"
	else
		echo "Claude Code：注册失败，可以手动运行：claude mcp add --scope user fluentflow-local -- \"${VENV_PY}\" \"${MCP_SCRIPT}\""
	fi
	registered=1
fi
if command -v codex >/dev/null 2>&1; then
	section=""
	[[ -f "$CODEX_CONFIG" ]] && section="$(awk '/^\[mcp_servers\.fluentflow-local\][[:space:]]*$/ { on = 1; next } /^\[/ { on = 0 } on' "$CODEX_CONFIG")"
	stale=0
	if [[ -n "$section" && "$section" != *"${MCP_SCRIPT}"* ]]; then
		target="$(printf '%s\n' "$section" | sed -n 's/^[[:space:]]*args[[:space:]]*=[[:space:]]*\[[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)"
		[[ -n "$target" && -e "$target" ]] || stale=1
	fi
	if [[ -n "$section" && "$stale" -eq 0 ]]; then
		echo "Codex：已注册过 fluentflow-local（这一份，或另一份还在的安装），保持不动。"
	elif [[ "$stale" -eq 1 ]]; then
		codex mcp remove fluentflow-local >/dev/null 2>&1 || true
		if codex mcp add fluentflow-local -- "$VENV_PY" "$MCP_SCRIPT" >/dev/null 2>&1; then
			echo "Codex：原来那条指向的位置已经没了，已重新注册 fluentflow-local。"
		else
			echo "Codex：没能自动注册，把下面几行加进 ${CODEX_CONFIG}："
			echo "$CODEX_TOML"
		fi
	elif codex mcp add fluentflow-local -- "$VENV_PY" "$MCP_SCRIPT" >/dev/null 2>&1; then
		echo "Codex：已注册 fluentflow-local。"
	else
		echo "Codex：没能自动注册，把下面几行加进 ${CODEX_CONFIG}："
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

# --- 5. 给 agent 的 skill -------------------------------------------------
# 写明拿到视频怎么处理、收到笔记反馈怎么验证后再改写法。链到代码目录里那份，
# 升级代码时 skill 跟着更新。已有同名的、不是链接的目录不动：那是用户自己的。
step "安装给 agent 的 skill"
SKILL_SRC="${DIR}/agent/skills/fluentflow"
for skills_dir in "${HOME}/.claude/skills" "${CODEX_HOME_DIR}/skills"; do
	tool_dir="$(dirname "$skills_dir")"
	[[ -d "$tool_dir" ]] || continue
	mkdir -p "$skills_dir"
	if [[ -e "${skills_dir}/fluentflow" && ! -L "${skills_dir}/fluentflow" ]]; then
		echo "${skills_dir}/fluentflow 已存在且不是链接，保持不动。"
		continue
	fi
	ln -sfn "$SKILL_SRC" "${skills_dir}/fluentflow"
	echo "已链到 ${skills_dir}/fluentflow"
done

# --- 6. 自检 ----------------------------------------------------------------
step "自检 MCP 服务"
"$VENV_PY" "${DIR}/scripts/check_mcp_server.py" >/dev/null || fail "MCP 服务自检没通过，运行 ${VENV_PY} ${DIR}/scripts/check_mcp_server.py 看详情。"
echo "MCP 服务正常。"

if [[ "$UPDATING" -eq 1 ]]; then
	SWAPPED=0
	trap - ERR
	rm -rf "${DIR}.old"
	echo "已更新到 ${LATEST}，旧版本已删除。"
elif ff_update_marker_says "${DIR}.old"; then
	# 上一次更新已经换好了新代码、之后被打断：新版跑通了，旁边那份旧版本删掉。
	rm -rf "${DIR}.old"
	echo "上一次更新留下的旧版本（${DIR}.old）已删除。"
fi

echo ""
echo "✓ FluentFlow Local 已装好，并已启动：http://127.0.0.1:${PORT}"
echo "  以后从桌面的「FluentFlow Local」打开。要笔记时，在新对话里把视频文件或链接交给你的 agent，"
echo "  它会用 FluentFlow 转写并亲自写笔记，不需要任何 API Key。"
