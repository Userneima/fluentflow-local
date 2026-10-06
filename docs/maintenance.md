# Independent Local Repository

FluentFlow Local is maintained here as an independent source repository. It has its own local application entrypoint, frontend build, tests, CI, launchers, dependencies and release history.

`docs/edition_boundaries.md` is the mandatory entry point for new conversations
and cross-repository work. It overrides any historical wording that describes
this repository as a generated export.

The hosted FluentFlow repository may retain similar implementations where each product needs them, but neither repository is a generated mirror of the other. Changes required by both editions are intentionally ported and reviewed in each repository; do not create a new shared-base repository merely to remove duplication.

The local app keeps existing user data compatibility through `backend/core/runtime_paths.py`. Repository maintenance must not delete or relocate the system application-data directory, local credentials, task history, media, or generated results.

For normal development, install `requirements-local.txt`, then run `npm run build:frontend` and start `backend.local_main:app`. CI validates the local frontend and the selected local backend suite on Python 3.10.

## 环境变量

后端和脚本读取的每个 `FLUENTFLOW_*` 变量。写进仓库根目录 `.env` 或启动时的 shell 环境都行；没列默认值的，不设就是关闭或不覆盖。来源按 `backend/` 与 `scripts/` 里的 `os.environ` 读取处整理，改了读取处要同步这里。

### 服务与数据位置

| 变量 | 含义 | 默认 |
| --- | --- | --- |
| `FLUENTFLOW_LOCAL_HOST` | 监听地址 | `127.0.0.1` |
| `FLUENTFLOW_LOCAL_PORT` | 监听端口；macOS 启动器也读这一项 | `8000` |
| `FLUENTFLOW_ALLOW_NON_LOOPBACK` | 为真时接受局域网请求，否则只收本机 | 关 |
| `FLUENTFLOW_DATA_DIR` | 运行数据根目录 | 系统应用数据目录，macOS 为 `~/Library/Application Support/FluentFlow` |
| `FLUENTFLOW_CONFIG_PATH` | 设置页写入的凭据与偏好文件 | `<数据目录>/fluentflow_config.json` |
| `FLUENTFLOW_JOB_DB_PATH` | 任务数据库 | `<数据目录>/fluentflow_jobs.sqlite` |
| `FLUENTFLOW_EVENT_DB_PATH` | 事件数据库 | `<数据目录>/fluentflow_events.sqlite` |
| `FLUENTFLOW_SOURCE_DIR` | 上传的源文件 | `<数据目录>/sources` |
| `FLUENTFLOW_ARTIFACT_DIR` | 转录、笔记、剪辑产物 | `<数据目录>/artifacts` |
| `FLUENTFLOW_EDITED_TRANSCRIPT_DIR` | 手动改过的转录稿 | `<数据目录>/edited_transcripts` |
| `FLUENTFLOW_TRANSCRIPT_EDIT_RECORDS_DIR` | 转录稿修改记录 | `<数据目录>/transcript_edit_records` |
| `FLUENTFLOW_VIDEO_SOURCE_DIR` | 从链接下载的视频 | `<数据目录>/video_sources` |
| `FLUENTFLOW_DIARIZATION_MODEL_DIR` | 讲话人分离模型目录，由 `scripts/fetch_diarization_models.py` 填充 | `<数据目录>/models/pyannote` |
| `FLUENTFLOW_ARTIFACT_RETENTION_DAYS` | 产物保留天数，`0` 为不清理 | `30` |
| `FLUENTFLOW_SOURCE_RETENTION_DAYS` | 源文件保留天数，`0` 为不清理 | `7` |

### 限额与超时

| 变量 | 含义 | 默认 |
| --- | --- | --- |
| `FLUENTFLOW_MAX_UPLOAD_MB` | 单个媒体文件上限 | `2048` |
| `FLUENTFLOW_MAX_TRANSCRIPT_UPLOAD_MB` | 单个转录稿文件上限 | `64` |
| `FLUENTFLOW_MAX_QUEUE_FILES` | 一次提交的文件数上限 | `5` |
| `FLUENTFLOW_MAX_MEDIA_DURATION_SECONDS` | 媒体时长上限 | `14400` |
| `FLUENTFLOW_MAX_TRANSCRIPT_EDIT_CHARS` | 编辑转录稿时的字数上限 | `1000000` |
| `FLUENTFLOW_MAX_SUMMARY_EDIT_CHARS` | 编辑笔记时的字数上限 | `500000` |
| `FLUENTFLOW_STALE_JOB_SECONDS` | 任务多久没有进展算卡住 | `7200` |
| `FLUENTFLOW_FILE_CHOOSER_TIMEOUT` | 系统文件选择框等待秒数 | `300` |
| `FLUENTFLOW_YT_DLP_DOWNLOAD_TIMEOUT_SECONDS` | 固定的链接下载超时；不设则按视频时长估算 | 按时长估算 |
| `FLUENTFLOW_YT_DLP_MAX_TIMEOUT_SECONDS` | 估算出的下载超时的上限 | `3600` |

### 处理流程开关

开关类变量用 `1/true/yes/on` 打开、`0/false/no/off` 关闭。

| 变量 | 含义 | 默认 |
| --- | --- | --- |
| `FLUENTFLOW_LOCAL_CUT_FIRST` | 上传后先剪气口再转录 | 开 |
| `FLUENTFLOW_LOCAL_AUTO_NOTE` | 转录完自动写笔记 | 开 |
| `FLUENTFLOW_LOCAL_STT_ENGINE` | 转录引擎：`auto` / `mlx` / `faster_whisper`（`cpu`、`gpu` 也认） | `auto` |
| `FLUENTFLOW_NOTE_MODE` | 笔记模式：`auto` / `direct` / `fast` / `high_fidelity` / `chapter_coverage` | `auto` |
| `FLUENTFLOW_TRANSCRIPT_CORRECTION_ENABLED` | 转录后用模型做高置信纠错（`FLUENTFLOW_TRANSCRIPT_CORRECTION` 是旧名，同义） | 关 |
| `FLUENTFLOW_KEYFRAME_EXTRACTION` | 抽关键帧 | 开 |
| `FLUENTFLOW_KEYFRAME_PROVIDER` | 抽帧实现 | `local_ffmpeg` |
| `FLUENTFLOW_DEBREATH_HARDWARE_ENCODE` | 剪气口重编码时用硬件编码器 | 关 |
| `FLUENTFLOW_MEDIA_PREFLIGHT_ENABLED` | 入库前的媒体检查总开关，设为假时下面每项都关 | 开 |
| `FLUENTFLOW_MEDIA_GUARD_EMPTY_FILE_ENABLED` | 拒绝空文件 | 开 |
| `FLUENTFLOW_MEDIA_GUARD_EXTENSION_ENABLED` | 拒绝扩展名和容器不符的文件 | 开 |
| `FLUENTFLOW_MEDIA_GUARD_CONTAINER_ENABLED` | 拒绝 ffprobe 读不出容器的文件 | 开 |
| `FLUENTFLOW_MEDIA_GUARD_AUDIO_STREAM_ENABLED` | 拒绝没有音轨的文件 | 开 |
| `FLUENTFLOW_MEDIA_GUARD_AUDIO_DECODE_ENABLED` | 拒绝音轨解不出来的文件 | 开 |
| `FLUENTFLOW_MEDIA_GUARD_SILENCE_ENABLED` | 拒绝全程静音的文件 | 开 |
| `FLUENTFLOW_YOUTUBE_SUB_LANGS` | 优先取用的 YouTube 字幕语言 | `en,zh-Hans,zh-Hant,zh` |
| `FLUENTFLOW_BILIBILI_SUB_LANGS` | 优先取用的 Bilibili 字幕语言 | `zh-Hans,zh-CN,zh,ai-zh` |

### 笔记与导出

| 变量 | 含义 | 默认 |
| --- | --- | --- |
| `FLUENTFLOW_VISUAL_NOTE_CHANNEL` | 图文笔记走哪条 Claude 通道：`subscription`（本机登录的 Claude Code）或 `api_key` | 按本机有什么自动选 |
| `FLUENTFLOW_VISUAL_NOTE_MODEL` | 图文笔记用的 Claude 模型 | `claude-opus-5` |
| `FLUENTFLOW_VISUAL_NOTE_TIMEOUT` | 图文笔记单次超时秒数 | `2400` |
| `FLUENTFLOW_CLAUDE_CLI` | `claude` 命令的路径，不设则在 PATH 上找 | 自动 |
| `FLUENTFLOW_LARK_CLI_BIN` | `lark-cli` 的路径，不设则在 PATH 上找 | 自动 |
| `FLUENTFLOW_LARK_CLI_WIKI_SPACE` | 本机身份导出写入的知识空间 | `my_library` |
| `FLUENTFLOW_LARK_DISABLE_OPENAPI_CONVERT` | 为真时飞书应用导出不走 OpenAPI 的 Markdown 转换 | 关 |

### Agent 接口与脚本

| 变量 | 含义 | 默认 |
| --- | --- | --- |
| `FLUENTFLOW_ACCESS_TOKEN` | 开启 `/agent/v1` 的访问令牌；后端和 MCP 客户端两边要一致 | 不设则接口关闭 |
| `FLUENTFLOW_API_BASE` | 脚本和 MCP 服务连哪个后端 | `http://127.0.0.1:8000` |
| `FLUENTFLOW_CLIENT_ID` | 脚本和 MCP 服务发请求时带的客户端标识 | `local-client` |

### 版本标识

构建和 `/health` 报告用，一般由 CI 或构建脚本设置，手动不需要碰。

| 变量 | 含义 | 默认 |
| --- | --- | --- |
| `FLUENTFLOW_VERSION` | 覆盖 `VERSION` 文件里的版本号 | 读 `VERSION` |
| `FLUENTFLOW_GIT_COMMIT` / `FLUENTFLOW_GIT_BRANCH` / `FLUENTFLOW_GIT_DIRTY` | 覆盖从 git 读到的提交、分支、是否有未提交改动 | 读 git |
| `FLUENTFLOW_BUILD_TIME` | 构建时间 | 当前时间 |
