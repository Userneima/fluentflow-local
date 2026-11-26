#!/bin/bash
# 给 macOS 的安装脚本和卸载脚本共用。用法：source "${SCRIPT_DIR}/fluentflow-env.sh"
#
# 这里定两件事，让各个脚本说法一致：
#
# 设置从哪读：先看环境变量，再看仓库 .env 里最后一行同名的，和服务
# （backend/core/runtime_paths.py 的 load_project_env）、Windows 脚本
# （launchers/windows/fluentflow-env.ps1）同一套规则。数据目录
# （FLUENTFLOW_DATA_DIR）和模型位置（HF_HOME）可能只写在 .env 里；只看环境变量的
# 脚本会把 FFmpeg 下到一个目录，服务却读另一个。不 source 整个 .env：它是 dotenv
# 格式，可含中文注释和空格。
#
# 从哪个地址下载：每样东西有原站和一个内容相同的国内镜像。先探原站几秒，连不上或
# 明显慢，就把快的那个排前面（和 backend/core/download_source.py 同一条规则）；第
# 一个失败了仍然试另一个。

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
	value="$(ff_normalize "$value")"
	printf '%s' "$value"
	[[ "$value" != *'$'* ]]
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

ff_data_dir() {
	local value
	value="$(ff_setting "$1" FLUENTFLOW_DATA_DIR)"
	printf '%s' "${value:-${HOME}/Library/Application Support/FluentFlow}"
}

# 把 .env 里会影响下载和存放位置的几项放进环境，给这个脚本启动的程序用。
ff_import_env() {
	local name value
	for name in FLUENTFLOW_DATA_DIR HF_HOME PIP_CACHE_DIR UV_CACHE_DIR npm_config_cache; do
		value="$(ff_setting "$1" "$name")"
		[[ -n "$value" ]] && export "${name}=${value}"
	done
	return 0
}

FF_PROBE_BYTES=1048576
FF_PROBE_SECONDS=5

# 5 秒内拿到多少字节（最多 1 MB）；比 1 MB 小的文件按时读完算 1 MB。
ff_measure() {
	local out code got
	# if/else：在 set -e 下，失败的命令替换写在赋值里会直接结束整个脚本。
	if out="$(curl -sL -r "0-$((FF_PROBE_BYTES - 1))" --max-time "$FF_PROBE_SECONDS" -o /dev/null \
		-w '%{http_code} %{size_download}' "$1" 2>/dev/null)"; then
		code=0
	else
		code=$?
	fi
	got="${out##* }"
	[[ "$got" =~ ^[0-9]+$ ]] || got=0
	if [[ "$code" -eq 0 && "${out%% *}" =~ ^2 && "$got" -gt 0 ]]; then
		echo "$FF_PROBE_BYTES"
	elif [[ "${out%% *}" =~ ^2 ]]; then
		echo "$got"
	else
		echo 0
	fi
}

# 按先试哪个的顺序逐行打印地址（第一个是原站）。
ff_order() {
	local first got url
	first="$(ff_measure "$1")"
	if [[ "$#" -lt 2 || "$first" -ge "$FF_PROBE_BYTES" ]]; then
		printf '%s\n' "$@"
		return 0
	fi
	{
		printf '%s\t0\t%s\n' "$first" "$1"
		local i=1
		shift
		for url in "$@"; do
			got="$(ff_measure "$url")"
			printf '%s\t%s\t%s\n' "$got" "$i" "$url"
			i=$((i + 1))
		done
	} | sort -t $'\t' -k1,1nr -k2,2n | cut -f3
}

ff_host() {
	local rest="${1#*://}"
	printf '%s' "${rest%%/*}"
}

# ff_verified_download <输出文件> <sha256> <地址…>：按探测顺序下载，校验对上的才留下。
ff_verified_download() {
	local out="$1" sha="$2" url got
	shift 2
	while IFS= read -r url; do
		echo "  从 $(ff_host "$url") 下载…"
		if curl -fL --retry 2 --connect-timeout 20 -sS -o "${out}.part" "$url"; then
			got="$(shasum -a 256 "${out}.part" | cut -d' ' -f1)"
			if [[ "$got" == "$sha" ]]; then
				mv -f "${out}.part" "$out"
				return 0
			fi
			echo "  $(ff_host "$url") 下载的文件校验不对（${got}）。"
		fi
		rm -f "${out}.part"
	done < <(ff_order "$@")
	return 1
}

# 系统设置里的代理（scutil --proxy），没设 HTTP(S)_PROXY 时给这个脚本启动的程序用。
# curl、pip、uv、npm 都不看系统设置；他朋友开着本机代理，每条命令都要手动加（2026-11）。
# 本机地址不走代理。
ff_use_system_proxy() {
	[[ -n "${HTTPS_PROXY:-}${HTTP_PROXY:-}${https_proxy:-}${http_proxy:-}" ]] && return 0
	# 赋空值：只声明不赋值的 local，bash 5（Homebrew 装的常排在 PATH 前面）在 set -u 下一读就报「unbound variable」，没开代理时安装会停在这里；系统自带的 bash 3.2 不报。
	local info host="" port=""
	info="$(scutil --proxy 2>/dev/null)" || return 0
	if [[ "$(printf '%s\n' "$info" | awk '/ HTTPSEnable :/ {print $3}')" == 1 ]]; then
		host="$(printf '%s\n' "$info" | awk '/ HTTPSProxy :/ {print $3}')"
		port="$(printf '%s\n' "$info" | awk '/ HTTPSPort :/ {print $3}')"
	elif [[ "$(printf '%s\n' "$info" | awk '/ HTTPEnable :/ {print $3}')" == 1 ]]; then
		host="$(printf '%s\n' "$info" | awk '/ HTTPProxy :/ {print $3}')"
		port="$(printf '%s\n' "$info" | awk '/ HTTPPort :/ {print $3}')"
	fi
	[[ -n "$host" && -n "$port" ]] || return 0
	export HTTP_PROXY="http://${host}:${port}" HTTPS_PROXY="http://${host}:${port}" NO_PROXY="127.0.0.1,localhost,::1"
	echo "用系统设置里的代理：${HTTPS_PROXY}"
}
