(*
  FluentFlow Local — 桌面启动器（AppleScript applet 源文件）

  由 install-local-to-desktop.sh 用 osacompile 编译成「FluentFlow Local.app」装到
  桌面。做成 .app 而不是 .command，是为了在桌面显示应用图标和名称，和这台机器上
  VerbaLab / CrystalSay / Glimmer Web 的做法一致（它们都是 osacompile 的 applet）。

  这个 applet 不重新实现任何启动逻辑。真正的启动器仍然是同目录的
  FluentFlowLocal.command —— 就绪检查、旧实例识别与重启、进行中任务保护、日志
  位置、中文失败原因全在那里。这里只做三件它做不到的事：包成可双击的应用、
  已在运行时给出打开/重启/取消、启动失败时把日志尾部读出来放进对话框。

  ── 为什么第二个按钮是"重启"而不是"停止" ──

  原来是「打开 / 停止服务 / 取消」，三个选项里没有一个能"换成最新代码"。后端加了
  路由而服务没重启时，页面上按钮在、接口不在，浏览器只报 Method Not Allowed——
  实测撞到过，而且当场没人猜得出原因。停止之后还得再双击一次才起得来，中间那次
  双击又会因为 applet 单实例而看起来没反应。
  所以「停止服务」换成「重启服务」：停掉、确认端口真的空了、再用当前代码起一个并
  打开页面。要单纯停掉服务，用终端：
    pkill -f 'uvicorn backend.local_main:app'

  入口是本地版 backend.local_main，绝不是 hosted 的 backend.main。这由
  FluentFlowLocal.command 保证；同目录的 FluentFlow.app 是旧的 hosted 启动器，
  两者互不相干。

  ── 为什么不走 Terminal ──

  hosted 那个启动器的注释说「GUI 进程直接 nohup 读 venv 会触发 TCC，无法读
  Documents 下的 pyvenv.cfg」，所以它绕道 Terminal。第一版照抄了这个前提，结果
  冷启动整个失败：applet 向 Terminal 发的 AppleEvent 超时（-1712），服务没起来。

  然后才去实测那个前提：一个 applet 直接 do shell script 执行
  `~/Documents/GitHub/fluentflow/venv/bin/python -c ...`，正常返回 3.9.6。
  **前提不成立**，Terminal 这一步是多余的，而且它引入了一个不稳定的自动化授权
  依赖 —— 同一句 `do script` 时而成功时而超时。

  教训写在这里而不是提交信息里，是因为下一个人改这个文件时会先读它：
  别把另一个启动器里的注释当成这台机器的既定事实，测一下比抄一遍便宜。
*)

property bundleDisplayName : "FluentFlow Local"
-- 安装时由 install-local-to-desktop.sh 替换成真实路径
property repoPath : "@@REPO@@"
-- 与 FluentFlowLocal.command 的默认端口一致。用户用 FLUENTFLOW_LOCAL_PORT 改过
-- 端口时，下面的快速通道探测不到，会落到正常启动路径由 .command 自己处理 ——
-- 退化是安全的，不会误判成"没起来"。
property appPort : "8000"
property readySeconds : 90

on q(p)
	return quoted form of p
end q

on sh(cmd)
	-- -lic 加载登录环境，ffmpeg 等 Homebrew 装的工具才在 PATH 里
	return do shell script "/bin/zsh -lic " & quoted form of cmd
end sh

on pageServes(theURL)
	-- 只认真正发出了正文的响应。健康检查返回 200 不等于页面出得来：一个跑了
	-- 十一天的进程曾经健康检查照常通过，访问首页却只发响应头、一个字节正文都
	-- 没有，浏览器打开就是全白。这也是重复双击不会起第二个实例的地方。
	try
		set code to sh("curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 1 --max-time 4 " & q(theURL))
		if code starts with "2" or code is "304" then
			return sh("curl -sS --max-time 4 " & q(theURL) & " | head -c 1 | wc -c | tr -d ' '") is not "0"
		end if
	end try
	return false
end pageServes

on localEditionServes(theURL)
	-- 页面出得来，还得是本地版。8000 端口上能出页面的不一定是 FluentFlow Local——
	-- hosted 版默认也是 8000。FluentFlowLocal.command 一直是查 "execution":"local"
	-- 才认，这里必须同口径：认错了就会把别人的进程 kill 掉。
	if not pageServes(theURL) then return false
	try
		set healthText to sh("curl -sS --max-time 3 " & q(theURL & "health"))
		return healthText contains "\"execution\":\"local\""
	end try
	return false
end localEditionServes

on activeJobCount(theURL)
	-- 停止一个空闲服务是可逆的；重启正在转录/排队的服务会中断任务。所以这里宁可
	-- 多问一句，也不静默中断。
	--
	-- 返回 -1 表示"读不到，不知道"，由调用方多问一句。
	--
	-- 数数这件事交给 count_active_jobs.py，不在这里用 grep 扫 JSON。原来那行
	-- `grep -Eo '"status":"pending"'` 会把任务进度快照里的**步骤**状态也算进来：
	-- 一条失败任务带着 4 个未执行的步骤，于是空闲的机器上永远显示"4 个任务正在
	-- 进行中"——实测撞到过，用户点几次都是 4。而且那次请求不带 client id，只看到
	-- 未归属的任务（1 条），页面上那 7 条根本不在视野里：真有任务在跑时它看不见。
	-- 两个方向都错，而关键字扫描本来就只是线索、不是判据。
	set counter to repoPath & "/launchers/macos/count_active_jobs.py"
	set py to repoPath & "/venv/bin/python"
	try
		do shell script "test -x " & q(py)
	on error
		set py to "/usr/bin/python3"
	end try
	try
		return (do shell script q(py) & " " & q(counter) & " " & q(theURL)) as integer
	on error
		return -1
	end try
end activeJobCount

on stopService(theURL)
	-- Returns true only when the port is confirmed free, because the restart path
	-- below decides whether to start a new service from that answer. Starting one
	-- on top of a process that has not let go of the port produces the failure
	-- nobody can read: a launcher that says nothing and a page that never loads.
	set pidList to ""
	try
		-- tr 不能省：lsof -t 一行一个 PID，多个 PID 拼进 "kill " 之后换行原样保留，
		-- shell 会读成两条命令——`kill 111` 执行了，`222` 变成找不到的命令，第二个
		-- 进程根本没被停。同一个进程的 IPv4/IPv6 也会重复出现，顺手去重。
		set pidList to sh("lsof -nP -iTCP:" & appPort & " -sTCP:LISTEN -t 2>/dev/null | /usr/bin/sort -u | /usr/bin/tr '\\n' ' ' | /usr/bin/sed 's/ *$//'")
	end try
	if pidList is "" then
		display dialog "没有找到正在监听 " & appPort & " 端口的 FluentFlow 进程。它可能已经停止。" buttons {"好"} default button 1 with title bundleDisplayName with icon caution
		return true
	end if

	try
		-- pageServes 已先确认这一端口是 FluentFlow Local；这里只发 TERM（而非 KILL），
		-- 让 uvicorn 正常退出。最多等 10 秒，避免 applet 无限卡住。
		sh("kill " & pidList & "; for i in {1..40}; do curl -sS --max-time 1 " & q(theURL & "health") & " >/dev/null 2>&1 || exit 0; sleep 0.25; done; exit 1")
		display notification "本地服务已停止" with title bundleDisplayName
		return true
	on error
		display dialog "10 秒内没有确认服务停止。它可能仍在退出；请稍后再打开 FluentFlow。" buttons {"好"} default button 1 with title bundleDisplayName with icon caution
		return false
	end try
end stopService

on reopen
	-- applet 默认单实例：上一次调用还没退出时，再次双击只会把它切到前台，on run
	-- 不会重跑，表现成"点了没反应"。实测撞到过：点完取消或停止后紧接着再双击，
	-- 日志只多一行表头，什么都没发生。把 reopen 接回 run，第二次双击才有效。
	run
end reopen

on run
	set appURL to "http://127.0.0.1:" & appPort & "/"
	set launcher to repoPath & "/launchers/macos/FluentFlowLocal.command"
	set logDir to (POSIX path of (path to home folder)) & "Library/Logs/FluentFlow"
	set serviceLog to logDir & "/local.log"
	-- 单独一份启动日志：.command 自己用 tee 写 serviceLog，若把 nohup 的输出也
	-- 指到同一个文件，uvicorn 每行都会重复一遍。分开两份，各自干净。
	set launchLog to logDir & "/local-launcher.log"

	try
		do shell script "test -f " & q(repoPath & "/backend/local_main.py")
	on error
		display dialog "找不到 FluentFlow 代码目录：" & return & return & repoPath & return & return & "如果代码目录移动过，请到新目录运行：" & return & "launchers/macos/install-local-to-desktop.sh" buttons {"好"} default button 1 with title bundleDisplayName with icon stop
		return
	end try

	try
		do shell script "test -x " & q(launcher)
	on error
		display dialog "找不到启动脚本，或它没有执行权限：" & return & return & launcher buttons {"好"} default button 1 with title bundleDisplayName with icon stop
		return
	end try

	-- 只有确认是本地版在服务，才给"打开/重启"这个菜单。8000 上是别的程序时不进
	-- 这个分支，落到下面的启动路径，由 .command 用它自己的话说清楚端口被谁占了。
	if localEditionServes(appURL) then
		-- 两个动作，差别说清楚：打开只是跳到页面，重启是把后台换成当前代码。
		-- 后端改了路由而服务没重启时，页面上按钮在、接口不在，报的是
		-- "Method Not Allowed"——没有这个入口的时候，那个错没人猜得出原因。
		try
			set existingChoice to button returned of (display dialog "FluentFlow Local 已在运行。" & return & return & "「打开」直接跳到页面，不动后台。" & return & "「重启服务」会停掉当前后台再用最新代码起一个（页面会自动打开）。" buttons {"打开 FluentFlow", "重启服务", "取消"} default button "打开 FluentFlow" cancel button "取消" with title bundleDisplayName)
		on error number -128
			return
		end try
		if existingChoice is "打开 FluentFlow" then
			try
				sh("open " & q(appURL))
			end try
			return
		end if
		if existingChoice is not "重启服务" then return

		set runningJobs to activeJobCount(appURL)
		set warnText to ""
		if runningJobs > 0 then
			set warnText to "当前有 " & runningJobs & " 个任务正在进行中。重启会中断它们，之后需要重新提交。"
		else if runningJobs < 0 then
			-- 读不到任务列表，不代表没有任务。宁可多问一句，也不静默中断。
			set warnText to "读不到任务列表，没法确认现在有没有任务在跑。重启会中断正在进行的任务。"
		end if
		if warnText is not "" then
			try
				set stopChoice to button returned of (display dialog warnText buttons {"继续运行", "仍要重启"} default button "继续运行" cancel button "继续运行" with title bundleDisplayName with icon caution)
			on error number -128
				return
			end try
			if stopChoice is not "仍要重启" then return
		end if
		-- 没停干净就不要起新的：端口还被占着，新服务只会失败得很难看。
		if stopService(appURL) is false then return
		-- 不 return，落到下面的启动路径，起一个新的并打开页面。
	end if

	display notification "正在启动本地服务…" with title bundleDisplayName

	-- nohup + </dev/null + disown：applet 退出后服务要继续活着。
	-- stdin 给 /dev/null 是有意的：.command 失败时会 read 等回车，没有终端时
	-- 读到 EOF 直接退出，不会永远挂着；它打印的中文原因进 launchLog，下面读出来。
	set body to "set -e; mkdir -p " & q(logDir) & "; printf '\\n---- %s app launch ----\\n' \"$(date '+%Y-%m-%d %H:%M:%S')\" >> " & q(launchLog) & "; cd " & q(repoPath) & "; nohup " & q(launcher) & " >> " & q(launchLog) & " 2>&1 </dev/null & disown 2>/dev/null || true"
	try
		sh(body)
	on error errMsg
		display dialog "启动脚本没能执行：" & return & return & errMsg & return & return & "启动日志：" & launchLog buttons {"好"} default button 1 with title bundleDisplayName with icon stop
		return
	end try

	-- 浏览器由 .command 自己打开，这里不重复开，否则多一个标签页
	set ready to false
	repeat with i from 1 to readySeconds
		if pageServes(appURL) then
			set ready to true
			exit repeat
		end if
		delay 1
	end repeat

	if ready then
		display notification "已启动：" & appURL with title bundleDisplayName
	else
		-- 把 .command 打印的原因直接摆给用户，而不是只丢一个日志路径
		set tailText to ""
		try
			set tailText to sh("tail -n 12 " & q(launchLog) & " 2>/dev/null | tail -c 900")
		end try
		if tailText is "" then set tailText to "（启动日志是空的）"
		display dialog "在 " & readySeconds & " 秒内没等到服务就绪（" & appURL & "）。" & return & return & "启动日志最后几行：" & return & tailText & return & return & "完整日志：" & return & launchLog & return & serviceLog buttons {"好"} default button 1 with title bundleDisplayName with icon caution
	end if
end run
