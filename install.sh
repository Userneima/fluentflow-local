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
DIR="${FLUENTFLOW_INSTALL_DIR:-${HOME}/fluentflow-local}"
PORT="${FLUENTFLOW_LOCAL_PORT:-8000}"

step() { echo ""; echo "== $1 =="; }
fail() {
	echo "" >&2
	echo "✗ $1" >&2
	exit 1
}

[[ "$(uname -s)" == "Darwin" ]] || fail "这个脚本只用于 macOS。Windows 请运行 install.ps1。"

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
		[[ -e "$DIR" ]] && mv "$DIR" "${DIR}.failed"
		mv "${DIR}.old" "$DIR"
		[[ -d "${HOME}/Desktop/FluentFlow Local.app" ]] && open "${HOME}/Desktop/FluentFlow Local.app"
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
# 更新用到的旁边几个文件夹（.new .old .failed）：已经存在的，只有带着本脚本留下的标记
# （之前某次更新建的）才删；用户自己叫这个名字的备份不动。
UPDATE_MARKER=".fluentflow-update"
assert_own_leftover() {
	[[ -e "$1" && ! -e "$1/${UPDATE_MARKER}" ]] &&
		fail_plain "$1 已存在，不是 FluentFlow 的更新留下的。请先把它移走或改名。什么都没改。"
	return 0
}

step "获取代码"
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
	fi
	if [[ "$HAVE_CODE" -eq 0 || "$UPDATING" -eq 1 ]]; then
		TARGET="$DIR"
		if [[ "$UPDATING" -eq 1 ]]; then
			TARGET="${DIR}.new"
			assert_own_leftover "$TARGET"
			rm -rf "$TARGET"
		fi
		mkdir -p "$(dirname "$TARGET")"
		cp -R "$LOCAL_SOURCE" "$TARGET"
		rm -rf "${TARGET}/.git" "${TARGET}/.venv" "${TARGET}/.env"
		echo "made by a FluentFlow Local install or update" >"${TARGET}/${UPDATE_MARKER}"
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
	echo "made by a FluentFlow Local install or update" >"${TARGET}/${UPDATE_MARKER}"
	echo "已放到 ${TARGET}"
fi

# 还在从某个目录运行的程序（服务、后台下载模型、yt-dlp 更新）。
# 不算这个脚本自己：它可能就是从那个目录里运行的。
stop_folder_programs() {
	local pid
	for pid in $(pgrep -f "$1/" 2>/dev/null || true); do
		[[ "$pid" == "$$" || "$pid" == "$PPID" ]] || kill "$pid" 2>/dev/null || true
	done
	sleep 1
}

if [[ "$UPDATING" -eq 1 ]]; then
	assert_own_leftover "${DIR}.old"
	assert_own_leftover "${DIR}.failed"
	# 数据、链接下载、模型若设在代码目录里面，会随旧代码一起被删：先挪出去再更新。
	for name in FLUENTFLOW_DATA_DIR FLUENTFLOW_LINK_OUTPUT_DIR HF_HOME; do
		where="${!name:-}"
		if [[ -z "$where" && -f "${DIR}/.env" ]]; then
			where="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${name}[[:space:]]*=" "${DIR}/.env" | tail -n 1 |
				sed -E "s/^[^=]*=[[:space:]]*['\"]?//; s/['\"]?[[:space:]]*(#.*)?$//" || true)"
		fi
		where="${where/#\~/$HOME}"
		if [[ "$where" == "${DIR}/"* ]]; then
			rm -rf "${DIR}.new"
			fail_plain "${name}（${where}）在代码目录里面，更新会换掉这个目录。请先把它挪到 ${DIR} 外面。什么都没改。"
		fi
	done
	# 端口和桌面启动器一样：环境变量优先，其次是 .env 里的同名行（新旧两份 .env 一样）。
	if [[ -z "${FLUENTFLOW_LOCAL_PORT:-}" && -f "${DIR}/.env" ]]; then
		env_port="$(grep -E '^[[:space:]]*(export[[:space:]]+)?FLUENTFLOW_LOCAL_PORT[[:space:]]*=' "${DIR}/.env" | tail -n 1 |
			sed -E "s/^[^=]*=[[:space:]]*['\"]?//; s/['\"]?[[:space:]]*(#.*)?$//" || true)"
		[[ "$env_port" =~ ^[0-9]+$ ]] && PORT="$env_port"
	fi
	health="$(curl -s --max-time 2 "http://127.0.0.1:${PORT}/health" 2>/dev/null || true)"
	if [[ "$health" == *'"edition":"local"'* ]]; then
		# 每个任务的 activity.busy 算（剪辑、写笔记时任务主状态已经是完成），和启动器的重启
		# 检查同一份脚本；-1 是问不到。
		busy="$("${DIR}/.venv/bin/python" "${DIR}.new/launchers/macos/count_active_jobs.py" "http://127.0.0.1:${PORT}" 2>/dev/null || echo -1)"
		[[ "$busy" =~ ^-?[0-9]+$ ]] || busy=-1
		if [[ "$busy" -ne 0 && "${FLUENTFLOW_UPDATE_FORCE:-}" != "1" ]]; then
			rm -rf "${DIR}.new"
			if [[ "$busy" -lt 0 ]]; then
				fail "问不到正在运行的 FluentFlow 有没有任务在处理。确认没有之后设 FLUENTFLOW_UPDATE_FORCE=1 再运行一次。什么都没改。"
			fi
			fail "还有 ${busy} 个任务在处理，等它们做完再运行一次（或设 FLUENTFLOW_UPDATE_FORCE=1 先停掉，重启后接着做）。什么都没改。"
		fi
		echo "停止正在运行的 FluentFlow Local…"
		pids="$(lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null || true)"
		# shellcheck disable=SC2086
		[[ -n "$pids" ]] && kill $pids 2>/dev/null || true
	fi
	stop_folder_programs "$DIR"
	# 可选的 pyannote 讲话人引擎不在标准依赖里；装过的，更新后照样装上。
	HAD_PYANNOTE=0
	compgen -G "${DIR}/.venv/lib/python3*/site-packages/pyannote" >/dev/null && HAD_PYANNOTE=1
	[[ -f "${DIR}/.env" ]] && cp -p "${DIR}/.env" "${DIR}.new/.env"
	rm -rf "${DIR}.old"
	cd "$(dirname "$DIR")"
	mv "$DIR" "${DIR}.old"
	SWAPPED=1
	# set -e 停下的那些步骤也要先换回旧版本，包括下一步的 mv。
	trap 'fail "安装中途出错（install.sh 第 ${LINENO} 行）。"' ERR
	mv "${DIR}.new" "$DIR"
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
	elif claude mcp add --scope user fluentflow-local ${MCP_ENV[@]+"${MCP_ENV[@]}"} -- "$VENV_PY" "$MCP_SCRIPT" >/dev/null; then
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

# --- 5. 给 agent 的 skill -------------------------------------------------
# 写明拿到视频怎么处理、收到笔记反馈怎么验证后再改写法。链到代码目录里那份，
# 升级代码时 skill 跟着更新。已有同名的、不是链接的目录不动：那是用户自己的。
step "安装给 agent 的 skill"
SKILL_SRC="${DIR}/agent/skills/fluentflow"
for skills_dir in "${HOME}/.claude/skills" "${HOME}/.codex/skills"; do
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
fi

echo ""
echo "✓ FluentFlow Local 已装好，并已启动：http://127.0.0.1:${PORT}"
echo "  以后从桌面的「FluentFlow Local」打开。要笔记时，在新对话里把视频文件或链接交给你的 agent，"
echo "  它会用 FluentFlow 转写并亲自写笔记，不需要任何 API Key。"
