# FluentFlow Local 仓库边界（当前事实）

状态：2026-09-09 起生效。本文是本地版仓库关于版本归属的权威声明；新对话、Claude、Codex 和人工维护者都必须先按此判断工作范围。

## 两个独立产品仓库

| 仓库 | 维护对象 | 入口与构建 | 维护规则 |
| --- | --- | --- | --- |
| 当前仓库 `fluentflow-local` | FluentFlow Local | `backend.local_main:app`、`vite.local.config.mjs`、`frontend/dist-local/` | 只维护本机优先的处理、启动器、本地依赖与本地 CI。 |
| 独立仓库 `fluentflow` | FluentFlow Hosted | `backend.main:app`、`vite.config.mjs`、`frontend/dist/` | 只维护托管版的账号、配额、云端处理、运营与生产部署。 |

两者不是镜像、不是同一仓库的两种构建，也不存在“从托管版导出本地版”的流程。任何需要两边都有的能力，必须分别移植、审查、测试并提交；不能假定一次改动会自动同步到另一仓库。

## Local 硬边界

- 本仓库的 `main` 是本地版的唯一开发与发布来源；不要新增导出器、export manifest、导出报告或“由 Hosted 生成”的 provenance 文件。
- 不要把 Hosted 的账号、配额、生产部署、云端管理台或运行配置带进本仓库。
- 文档中出现的“上游导出”“镜像”“上一版从 Hosted 生成”只能作为历史背景，不是当前维护指令。
- 需要改 Hosted 时，切换到 `fluentflow` 仓库并读取其 `AGENTS.md`；不要在本仓库做未经验证的反向同步。

## 两个版本在同一台机器上怎么区分

本地版占 8000（桌面 `FluentFlow Local.app`、`launchers/macos/FluentFlowLocal.command`），
托管版在开发机上用 8001。两版都提供 `/agent/v1/...`，但入口能力不同，所以身份由后端自己声明，
不靠调用方推断：`/health` 带 `edition`、`edition_label`、`accepted_agent_inputs`，字段来自
`backend/core/edition_identity.py`。托管版仓库有它自己的一份，值不同；改一边要一起改另一边，
否则声明和判断会分叉。

`scripts/fluentflow_mcp_server.py` 是客户端，两个仓库各有一份，连哪个后端由
`FLUENTFLOW_API_BASE` 决定。需要本地版的两个工具（`submit_local_media`、
`write_note_from_cut_media`）在发请求前先读一次 `/health` 的 `edition`，对端不是本地版就说明
是哪个版本在应答、该起哪一个。身份每次现读不缓存：这套机制要防的就是同一个端口上换了后端。

本机 Agent API 要靠仓库根目录 `.env` 里的 `FLUENTFLOW_ACCESS_TOKEN` 打开，值要和调用方
（例如 `~/.claude.json` 的 MCP 条目）一致，否则提交返回 401。

## 协作与交接

开始新任务时先确定目标是 Local 还是 Hosted；目标不清时先问，不要跨仓库猜测。交接、任务说明、PR 和提交信息必须写明 edition / repository。跨仓库移植必须记录来源提交、目标仓库验证和未移植部分；这不是同步或导出。
