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

# .env 里某个名字的值，不看环境变量。
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
	local info host port
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
