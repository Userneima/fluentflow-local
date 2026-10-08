# 已知问题（2026-10-08 复审 0.6.0）

来源：2026-10-08 对 0.6.0 的四路复审：代码复审（v0.5.0..v0.6.0）、隔离环境真实任务（本地视频、抖音、B 站、重复提交、重试）、设置页与界面审查、启动器与文档审查。修好一条就从这里删掉一条，并在 `docs/changelog.md` 记一笔。「实测」指真实跑过；其余为读代码得出。

## 会给出错误结果或违背设置的

- MCP `export_result` 不读用户选的导出路线和文件夹，固定走飞书应用路线：只登录了 lark-cli 的用户从 AI 工具导出必定失败；结果写进 `exports` 而不是编辑器读的 `lark_response`；Agent 导出在事件循环上同步跑 `lark-cli auth status`，最长卡住服务 15 秒。
- Claude 不可用时退到文本笔记，丢掉了用户的提示词、笔记模式、说话人标注，也没生成可下载的笔记文件（`local_intake_flow._write_text_note_instead`）。
- 自动剪辑被放弃后手动剪成功的任务，之后再剪会被误拒为「转写时已经去过气口」（`debreath_job._store` 合并留下 `ran_before_transcription`）。
- 设置页在没填 Anthropic Key 时藏起「给笔记自动配图」和千问 Key，但这项设置仍在流水线里生效、花千问的钱（设置页看 `writesItsOwnNote`，后端看 `auto_note_will_run`）。
- 只填了 Anthropic Key 时，「字幕生成笔记」静默拿不到笔记，首页不提醒（字幕流程只走文本模型）。

## 状态和文案会误导人的

- 链接任务的 `summary_status` 被拿来存下载进度文案，转写时还写着「正在保存视频信息」（`local_video_sources.py` 写入处，实测）。
- 任务步骤「AI 笔记生成」的说明是「已生成自动选择。」；重试出来的链接任务没下载就显示「素材已保存」；详情 timeline 全标为推断；材料分类混英文且把 3 分钟面试判成长视频；三个产物标签没本地化（实测）。
- `note_written_from` 在用原文件转写的任务上也写成 `debreath_media_note`（实测）。
- 退回文本模型写的笔记，界面一个字都不提（后端记了 `note_fallback_reason`，前端不读）。
- 编辑器笔记空状态还写「仅转录模式」「AI 摘要仍在生成中」，和其他页面叫法不一致；设置页「笔记」区第一句和写笔记方那句可能互相矛盾。
- 首页「最近任务」把已取消标成「失败」，任务列表标「已取消」。
- 自动导出飞书的结果在编辑页轮询停下后才写入，要重开任务才看得到「已导出 · 打开」或失败原因。
- 「区分讲话人」开关和令牌行在安装脚本装的版本上永远用不了（没装 pyannote），只显示灰色，不说原因；Agent/MCP 按路径提交的任务仍默认请求它再跳过，链接任务又不请求，两边不一致。
- 中文界面里漏英文和行话：`FEISHU APP ID`、`PYANNOTE AUTH TOKEN`、「STT 模型」「转录路线」「重新执行 STT」；千问 Key 有三种叫法。
- 「抖音备用解析」默认开、链接会发给第三方，开关藏在浏览器登录那一项里面。
- 设置页的「导出记录」只有手动导出，自动导出的文档不在里面。

## 稳定性与资源

- 链接任务没有合格剪后版本时，下载的视频在缓存目录和任务目录各留一份，永不清理。
- 重剪先删掉旧剪后文件再渲染，渲染失败时任务记录指向不存在的文件（`silence_cuts.render_keeps`）。
- 服务被强杀后，链接下载的隐藏临时目录 `.*.download/` 一直留着，下载进程也不会跟着退出；启动时不清理。
- 局域网模式接受 `.local` 来源，可被同网段的 mDNS 重绑定利用（`local_http_boundary.py`）；文档说其他设备「只能查看」，但不带令牌就能读全部转录稿和笔记，没有提醒只在可信网络开启。
- 每个任务都重新加载转写模型（实测 1.6 到 2.5 秒）。
- 链接任务事件里记录的播放地址 `/video-sources/files/…` 返回 404（实测，前端不用它）。

## 安装与启动（未在 Windows 实机验证）

- Windows 启动器很可能一启动就闪退：`$ErrorActionPreference = "Stop"` 下，uvicorn 写到 stderr 的第一行会被 PowerShell 5.1 当成致命错误；`.cmd` 没有 `pause`，看不到原因。
- Windows 卸载脚本读错了 `/health` 字段，认不出正在运行的服务，会在服务运行中删虚拟环境。
- 启动过程中再点一次图标会起第二个启动流程，两个 pip 可能同时升级 yt-dlp；yt-dlp 升级按总时长强杀 pip，可能装坏一半；升级的那几秒里开始的链接下载会被报成「yt-dlp 没装」。
- 国内用户安装时设的 `PIP_INDEX_URL` 不会保存，之后每天的 yt-dlp 自动更新都去连 pypi.org，静默失败。
- 端口读法在启动器、卸载脚本、Windows 之间不一致；前端没构建时提示的 `npm run build:frontend:local` 不存在；Windows 脚本全是英文提示。

## 文档与 AI 工具说明

- 维护文档、更新记录、MCP 说明仍按「默认用本机 Claude 订阅」写，实际默认是 Anthropic API Key；`FLUENTFLOW_LOCAL_AUTO_NOTE` 的说明和效果不符。
- 隐私说明：图文笔记还会发送转录稿；「关于」页说只接受本机访问，和局域网模式矛盾；飞书导出写成「用你自己的飞书应用」，现在默认是本人身份。
- 「Agent 接入」页给的验证命令 `npm run mcp:check:e2e` 不存在，配置里的路径是占位符；`check_mcp_server.py` 的工具清单漏了三个工具。
- MCP `submit_transcript` 同步写笔记，超过 120 秒就超时、调用方可能重复提交；`prompt_preset`/`note_mode` 在 Claude 写笔记时不生效，说明里没写；`write_note_from_cut_media`、`debreath_task`、`retry_task` 的说明和实际行为有出入。
- README 教用 curl 关抖音备用解析，设置页已有开关；环境变量表漏了 `FLUENTFLOW_NOTE_DEADLINE_SECONDS`、`FLUENTFLOW_SYSTEM_PROMPT`。
