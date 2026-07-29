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
		echo "FluentFlow Local 已在运行，直接打开浏览器。"
		open "$APP_URL"
		exit 0
	fi
	fail "端口 ${PORT} 被其他程序占用（不是 FluentFlow Local）。
请先退出那个程序，或设置 FLUENTFLOW_LOCAL_PORT 换一个端口后重试。"
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
echo "停止方式：在本窗口按 Ctrl+C，或直接关闭窗口。"
echo ""
exec "$VENV_PY" -m uvicorn backend.local_main:app --host 127.0.0.1 --port "$PORT" 2>&1 | tee -a "$LOG_FILE"
