# FluentFlow Local Changelog

## Unreleased

- Generated local edition.
- Windows 安装脚本现在会将 NVIDIA GPU 转录运行库安装在项目虚拟环境，避免 CTranslate2 因缺少 `cublas64_12.dll` 在首次转录时失败。
- 新增可复现的前端/Backend 静态检查依赖与 GitHub Actions CI。
- 补全本地版发布清单，使前端路由契约测试可在干净环境运行。
- 结果编辑器现在虚拟化超长转录列表，避免一次性渲染数千个可编辑段落而卡顿。
- 视频复查改为按需授权的本地分段播放，不再将完整源视频读入浏览器内存。
- 修复含小数点的本地文件名被截断，并允许在编辑器重新选择原视频后保存到该任务，供后续视频复查使用。
- 修复重启应用后已保留源文件的记录仍提示"选择音频"：编辑器现在直接挂载本地服务的分段流地址，不再要求每次重新选择原音频。
- 修复从"处理记录"列表打开任务后，笔记被截断成 240 字并自动保存覆盖原文的数据丢失问题：任务列表与浏览器缓存只返回预览字段并标记 `result_partial`，编辑器在完整记录加载完成前停用笔记/转录编辑、自动保存、重生笔记与导出。
- 重生笔记改为流式返回进度：新增 `/regenerate-summary/stream`，按"提取要点 3/7""撰写章节""按覆盖度返修"等步骤实时推送，按钮显示当前步骤与进度条，不再只有一个转圈图标。
- 修复重生笔记在切换窗口/关闭标签页后被静默中断、笔记丢失且不留任何日志：生成过程改为经 `JobEventHub` 以后台任务运行（与 `/process` 一致），SSE 响应只是它的观察者。客户端断开不再取消生成，前端会带 `since=N` 重新订阅 `/jobs/{id}/events` 续上同一次运行，不会重复触发第二次生成。
- 修复重生笔记在生成完成后被误判冲突丢弃：冲突检查过去比较整个 result，任何无关写入（自动保存刷新 `summary_edited_at`、留存期扫描、产物刷新）都会让已生成好的笔记作废；现在只比较笔记正文与转录文本，真实编辑仍然优先。
- Windows 启动器现在会重启后端：过去检测到实例在跑就只打开浏览器直接退出，强制关闭窗口残留的进程只能去任务管理器杀。现在双击快捷方式即重启（只结束确认属于 FluentFlow 的 python 进程树），有任务在跑时拒绝重启以免中断转录，`-Force` 可强制，`-Reuse` 保留旧的"仅打开页面"行为。
- 历史留存默认关闭，修复"处理一个新任务就丢掉旧笔记"的数据丢失：`FLUENTFLOW_ARTIFACT_RETENTION_DAYS` 名为"产物留存"，实际到期删除的是整条任务记录，笔记正文就存在这条记录里，而且清理挂在任务完成的流水线上。默认值改为 `0`（永不删除记录）；回收磁盘仍由 `FLUENTFLOW_SOURCE_RETENTION_DAYS`（默认 7 天）负责，它只删体积大的源媒体，保留转录和笔记。
- Windows 数据目录默认不再放在系统盘：一次转录会完整保存源文件，`%APPDATA%` 所在的 C 盘正是最不能被写满的盘。现在优先选择可用空间最大的**固定**非系统盘（排除可移动盘和网络盘，要求至少 5 GB 可用），没有合适磁盘时回退到 `%APPDATA%`。已有数据仍留在 `%APPDATA%` 继续读取，不会静默切换到空目录，需要搬迁时运行 `scripts/migrate_data_dir.py`（先校验复制结果，再把旧目录改名而非删除）。`FLUENTFLOW_DATA_DIR` 优先级不变。
- 数据目录的位置收敛为一处决定：`resolve_workspace()` 返回位置**和判定依据**（`env` / `recorded` / `chosen` / `default`），启发式只在首次运行跑一次就由 `ensure_workspace_recorded()` 固化，之后启动都是读记录文件。启动的 `data-dir` 检查会打印位置、依据，以及被 `FLUENTFLOW_*_DIR`/`_PATH` 单独指到别处的项——工作区被拆散不再毫无提示（数据库在一块盘、配置在另一块盘时，界面表现和"记录全丢"完全一样）。
- 笔记支持由外部 Agent 写入：新增 `PUT /agent/v1/tasks/{task_id}/note` 与 MCP 工具 `save_note`，配合已有的 `get_task_package`（已包含全文转录），Claude Desktop 这类 MCP 客户端可以读走转录、用自己的模型写笔记再存回来，走订阅而不消耗本地 AI 凭证。写入可带 `expected_summary_markdown` 前置条件——Agent 写作耗时数分钟，期间用户在编辑器改过笔记时返回 409 而不是静默覆盖；空笔记被拒绝，不会把已有笔记清空。
- 修复 MCP / Agent 客户端看不到本机任何任务：任务按 client id 分域，浏览器端写在 `local-single-user`，而 `scripts/local_agent_client.py` 自带的默认值是 `local-client`——于是在一台有记录的机器上 `list_tasks` 返回 0 条，经它创建的任务也落在应用看不到的地方。默认值改为同一身份，常量收到 `local_request_scope.LOCAL_SINGLE_USER_CLIENT_ID`，并用 `tests/test_client_id_contract.py` 把 Python 侧、前端两处副本和迁移脚本的目标钉在一起（跨语言没有共享模块，只能互钉）。
- MCP server 自己把 stdin/stdout 固定为 UTF-8：帧是 UTF-8 JSON 而内容以中文为主，不设的话 stdio 跟随控制台代码页，在非 UTF-8 的中文 Windows 上第一个中文字符就会让服务端崩掉——而这件事启动它的 MCP 客户端从外部修不了。
- 任务列表行的标题改为优先 `raw_title`（与浏览器端一致）：标题截断修复之前建立的记录带着坏掉的 `display_title`（`4.5期…` 变成 `4`），按标题挑活的 Agent 需要一个能读的标题。
- MCP 客户端可以自己找到待办任务：新增 `GET /agent/v1/tasks` 与 MCP 工具 `list_tasks`（`note=any|missing|present`），于是「把还没有笔记的任务都处理一遍」变成一句话，而不必由用户逐个报 task_id。行里带 `note.source`，第二次跑批可以跳过自己写过的、也不覆盖用户手改的；读的是列表投影，不会为了列出一百条而加载一百份转录稿。
  判定「还没有笔记」只看笔记正文长度：`summary_status` 同时存在于 result 和 job 列里，而笔记写入只更新 result，列一直停留在 `skipped`——按状态判定会把每条写完的任务都报成待办（端到端测试抓到的）。
- 笔记写入路径收敛为一处：新增 `backend/core/note_write.py` 的 `apply_summary_edit()`，编辑器路由与 Agent 路由共用同一套字段。过去每条写入路径各自拼字段，它们不一致的那几个（空笔记后的 `summary_status`、`summary_skipped`、编辑标记）直接决定编辑器是否显示"已保存"、导出能否找到笔记、以及正在跑的重生是否有权覆盖。同时记录 `summary_source` / `summary_source_label` 并在任务包的 `note` 段暴露 `edited` / `source`——否则 Agent 重读任务时分不清笔记是自己写的、用户手改的，还是流水线产出的，会覆盖掉刚做完的手工工作。
- 笔记生成新增 Claude（Anthropic）服务商，可替代 DeepSeek：设置页多一个选项 + API Key，默认 `claude-opus-5`，`ANTHROPIC_MODEL` / `ANTHROPIC_MAX_TOKENS`（默认 64000）可覆盖。需要 API Key，Claude Pro/Max 订阅无法作为 API 凭证。
  三个既有服务商都是 OpenAI 兼容格式、只差 base URL，Anthropic 不是，所以客户端改为携带自己的服务商标记（`AiClient`），由 `_chat` / `_vision_chat` 按标记分派——靠类型判断客户端对象会把将来第四个异形服务商送进恰好当兜底的那条分支。
  三个必须做对否则静默失败的点：现役 Claude 模型已移除 `temperature` 等采样参数，收到就返回 400，所以流水线的逐次 temperature 不能透传；`max_tokens` 是必填且与思考共享上限，给小了笔记会被截断（触顶时打警告）；拒答返回 HTTP 200 但没有正文，不检查 `stop_reason` 就会被当成"模型没话说"存下一份空笔记。长笔记走流式以免撞上非流式 HTTP 超时。
- 长转录可以一次成文：Claude 的上下文足够装下整份转录稿，配合已有的"直接上下文"模式，13 万字的转录从 20+ 次模型调用变成 1 次，且不再需要"分章写完再拼接"。多步流水线（切段抽证据 → 大纲 → 逐章 → 统一文风 → 覆盖度检查 → 按需返修）本质是对上下文不足的补偿，DeepSeek 仍需要它，因此原样保留，只是不再是 Claude 的必经之路。
- 自动配图不再必须配置阿里云百炼：选帧调用过去硬编码 `provider="qwen"`，现在跟随笔记服务商（能看图就用它，纯文本服务商如 DeepSeek 才回退 Qwen）。「哪个模型能看图」的判断收敛到 `ai_client.vision_model()`，不再由两个调用点各自内联一个服务商的环境变量。
- 前端服务商判定改为查表（`AI_PROVIDERS`）而非三元链：三元链会把未知值静默落到最后一个分支，新增服务商的典型后果就是被存成 deepseek、还配上 deepseek 的模型名。
- `scripts/check_mcp_server.py` 改为直接读取 MCP 工具注册表，不再维护第二份工具清单（那份已经漏了一个工具，而且检查是子集比较，清单过时只会让工具悄悄不再被验证）；并修复它在非 UTF-8 的中文 Windows 环境下用 GBK 解码子进程输出、遇到含中文的任务包直接崩掉的问题。
- 存储的任务记录去掉重复副本：一条三小时的转录曾把转录稿存 3 份、字幕分段存 3 份，1011 KB 里有 583 KB 是逐字节相同的副本或没有任何读取方的字段（`stt_raw_segments` 写两处、读零处）。写入时由 `normalize_result_for_storage` 只留一份，读取时 `normalize_result_for_read` 再补回派生字段——瘦身是存储决定，不能变成 API 变更（一度让 `GET /jobs/{id}` 对 3886 段的记录返回 `display_segments: []`）。已有记录用 `scripts/compact_job_results.py` 迁移：先备份、逐行比对读取结果、只在读取内容完全不变时才重写，`--check` 只报告，`--restore` 可回滚。本机实测 3072 KB → 772 KB。
