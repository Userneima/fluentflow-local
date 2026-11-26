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

# .env 里某个名字的值，不看环境变量。引用前面行的值（${BASE}/data）时，照服务那边
# 读前面那一行；FF_ENV_DEPTH 防止互相引用转圈。
ff_env_file_value() {
	local repo="$1" name="$2" line value
	[[ -f "${repo}/.env" ]] || return 0
	line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${name}[[:space:]]*=" "${repo}/.env" 2>/dev/null | tail -n 1 || true)"
	[[ -n "$line" ]] || return 0
	value="${line#*=}"
	value="${value#"${value%%[![:space:]]*}"}"
	case "$value" in
	\"*)
		value="${value#\"}"
		value="${value%%\"*}"
		;;
	\'*)
		value="${value#\'}"
		value="${value%%\'*}"
		;;
	*)
		value="$(printf '%s' "$value" | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//')"
		;;
	esac
	# ${NAME} 和 ${NAME:-默认值}，照服务那边（python-dotenv 的规则）从环境变量展开。
	local ref name default found
	while [[ "$value" =~ \$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\} ]]; do
		ref="${BASH_REMATCH[0]}" name="${BASH_REMATCH[1]}" default="${BASH_REMATCH[3]}"
		found="${!name:-}"
		if [[ -z "$found" && "${FF_ENV_DEPTH:-0}" -lt 5 ]]; then
			found="$(FF_ENV_DEPTH=$(( ${FF_ENV_DEPTH:-0} + 1 )) ff_env_file_value "$repo" "$name")"
		fi
		found="${found:-$default}"
		value="${value/"$ref"/$found}"
	done
	printf '%s' "$value"
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
