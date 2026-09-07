#!/bin/bash
# FluentFlow Local 启动器（macOS）。
#
# 双击后在终端窗口里启动本地版后端并打开浏览器。这个窗口就是服务本身：
# 按 Ctrl+C 或关闭窗口即停止。与旧的云端启动器不同：
#   - 启动的是本地版入口 backend.local_main:app（无账号/云端面）；
#   - 已在运行时直接打开浏览器，不会杀掉占用端口的其他进程；
#   - 日志写到 ~/Library/Logs/FluentFlow/，不污染代码目录；
#   - 启动前先跑就绪检查，缺什么会用中文说清楚怎么装。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${FLUENTFLOW_REPO:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
PORT="${FLUENTFLOW_LOCAL_PORT:-8000}"
APP_URL="http://127.0.0.1:${PORT}/"
LOG_DIR="${HOME}/Library/Logs/FluentFlow"
LOG_FILE="${LOG_DIR}/local.log"

fail() {
	echo ""
	echo "✗ $1" >&2
	echo ""
	read -r -p "按回车键关闭…" _ || true
	exit 1
}

if [[ ! -f "${REPO}/backend/local_main.py" ]]; then
	fail "找不到 FluentFlow 代码目录（当前推测：${REPO}）。
如果这个启动器被复制到了别处，请设置环境变量 FLUENTFLOW_REPO 指向代码目录，
或使用 install-local-to-desktop.sh 生成桌面快捷方式。"
fi

# 单实例检测：健康检查确认是本地版就直接打开浏览器；端口被别的程序占用则提示。
health="$(curl -s --max-time 2 "http://127.0.0.1:${PORT}/health" 2>/dev/null || true)"
if [[ -n "$health" ]]; then
	if [[ "$health" == *'"execution":"local"'* ]]; then
		# /health 活着不等于页面出得来。一个跑了 11 天的进程曾经健康检查照常返回
		# 200，访问 / 却只发响应头、一个字节的正文都不发，浏览器打开就是全白。
		# 那次启动器认定「已在运行」，高高兴兴地把空白页打开了 —— 所以这里要探
		# 浏览器真正会加载的那个地址，而不是只探健康检查。
		if curl -sf --max-time 5 "$APP_URL" 2>/dev/null | head -c 1 | grep -q .; then
			echo "FluentFlow Local 已在运行，直接打开浏览器。"
			open "$APP_URL"
			exit 0
		fi
		echo ""
		echo "⚠ 端口 ${PORT} 上的 FluentFlow Local 还在应答健康检查，但已经打不开页面了。"
		echo "  这通常是跑了很久的旧进程。正在重启它…"
		echo ""
		stale_pids="$(lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null || true)"
		if [[ -z "$stale_pids" ]]; then
			fail "没能找到占用端口 ${PORT} 的进程，请手动退出它后重试。"
		fi
		# 双击启动器本身就是「我要用它」，所以坏掉的实例直接重启，不再多问一句。
		# 唯一要停下来问的情况是后台真的在转东西：界面坏了不代表任务坏了，一段三小时
		# 的视频转到一半被杀掉，得从头再来。查不到任务列表就照常重启 —— 那说明这个
		# 进程连自己的接口都答不上来了，留着也没用。
		# `|| true` 不是装饰：脚本开头是 set -euo pipefail，而 grep 找不到匹配时返回 1，
		# 于是「没有任务在跑」这个最常见的情况会让整条管道失败、脚本当场退出，重启那几行
		# 根本执行不到。第一版就是这么写的，测试时它一声不吭地什么也没做。
		running="$(curl -s --max-time 3 "http://127.0.0.1:${PORT}/jobs?limit=100" 2>/dev/null \
			| grep -c '"status":"\(queued\|processing\|running\|pending\)"' || true)"
		if [[ "${running:-0}" -gt 0 ]]; then
			echo "但后台还有 ${running} 个任务在进行中，重启会让它们从头再来。"
			read -r -p "确认重启请按回车，按 Ctrl+C 取消…" _ || exit 1
		fi
		# shellcheck disable=SC2086
		kill $stale_pids 2>/dev/null || true
		for _ in $(seq 1 40); do
			curl -s --max-time 1 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 || break
			sleep 0.25
		done
		if curl -s --max-time 1 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
			fail "旧进程没有退出（PID: ${stale_pids}）。请手动结束它后重新双击启动。"
		fi
		echo "旧进程已停止，继续启动新的。"
	else
		fail "端口 ${PORT} 被其他程序占用（不是 FluentFlow Local）。
请先退出那个程序，或设置 FLUENTFLOW_LOCAL_PORT 换一个端口后重试。"
	fi
fi

VENV_PY="${REPO}/venv/bin/python"
[[ -x "$VENV_PY" ]] || VENV_PY="${REPO}/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
	fail "找不到 Python 虚拟环境。请在终端执行：
cd ${REPO}
python3 -m venv venv
./venv/bin/pip install -r requirements-local.txt"
fi

echo "== FluentFlow Local 启动前检查 =="
if ! "$VENV_PY" "${REPO}/scripts/check_local_readiness.py"; then
	fail "环境未就绪，请按上面的提示处理后重新双击启动。"
fi

mkdir -p "$LOG_DIR"
echo "---- $(date '+%Y-%m-%d %H:%M:%S') starting backend.local_main:app on :${PORT} ----" >>"$LOG_FILE"

# 不在 shell 里 source .env：.env 是 dotenv 格式（可含中文注释、空格），
# backend.local_main 启动时会用 python-dotenv 自己正确加载它。
cd "$REPO"

# 服务起来后自动开浏览器（后台探测，不阻塞前台 uvicorn）。
(
	for _ in $(seq 1 80); do
		if curl -s --max-time 1 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
			open "$APP_URL"
			exit 0
		fi
		sleep 0.25
	done
	echo "（服务迟迟未监听端口 ${PORT}，请查看日志：${LOG_FILE}）" >&2
) &

echo ""
echo "FluentFlow Local 正在启动：${APP_URL}"
echo "日志文件：${LOG_FILE}"
# 「关闭窗口即停止」只在有终端窗口时成立。桌面上的 FluentFlow Local.app 是用
# nohup 拉起这个脚本的，没有窗口可关，照原样打印会给出一条做不到的指示。
if [[ -t 1 ]]; then
	echo "停止方式：在本窗口按 Ctrl+C，或直接关闭窗口。"
else
	echo "停止方式：本次是后台启动（没有终端窗口）。停止请执行："
	echo "  lsof -ti:${PORT} | xargs kill"
fi
echo ""
exec "$VENV_PY" -m uvicorn backend.local_main:app --host 127.0.0.1 --port "$PORT" 2>&1 | tee -a "$LOG_FILE"
