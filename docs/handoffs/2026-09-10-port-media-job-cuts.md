# 交接：把托管版的流水线拆分移植过来（2026-09-10）

本文写给一个没有上下文的执行会话。目标仓库是 `fluentflow-local`（本仓库）。
来源仓库是独立的 `fluentflow`（Hosted），两者按 `docs/edition_boundaries.md`
分开维护，本文描述的是一次**人工移植**，不是同步，也不是导出。

## 要做的事

Hosted 仓库在 2026-09-09 把 `_stream_media_job` 拆小了。那个函数是
`backend/core/media_job.py` 里最大的一块，整条转写流水线都在里面。本仓库的
同名文件还是拆分前的状态。把下面列出的每一刀按顺序移过来，单独验证、单独提交。

顺带还有一刀在前端：`frontend/src/routes/editor.jsx` 里的提示词模板那一簇
被提成了一个 hook。

## 已核验的事实

以下都是 2026-09-10 实际执行命令确认过的，不是推断。

**两边的起点文件逐字节相同。** 本仓库当前的 `backend/core/media_job.py` 和
`frontend/src/routes/editor.jsx`，与 Hosted 仓库在第一刀之前的同名文件完全
一致。`tests/test_speaker_diarization.py` 也一致。核验方法：

```bash
diff <(git -C ../fluentflow show 314dd5d1^:backend/core/media_job.py) backend/core/media_job.py
```

这意味着移植可以按 commit 逐个套用，不需要手工重写。

**本仓库没有 `backend/core/server_helpers.py`。** 版本分离时它留在了 Hosted。
这直接影响第一刀，见下面「需要你判断的」。

**本仓库的任务上下文不提供配额回调。** `local_processing.py` 构造
`MediaJobContext` 时 `release_task_usage` 和 `finalize_task_usage` 都没有传，
因为本地版没有配额。移过来的代码对此有保护（`if ctx.release_task_usage:`），
Hosted 那边也留了一条专门测这个场景的用例。移植后确认那条用例跟着过来了。

**本仓库有两个调用方，Hosted 只剩一个。** `backend/routers/local_processing.py`
和 `backend/routers/local_video_sources.py` 都会进 `execute_media_job`。Hosted
在分离后只剩 `processing.py`，所以那边的验证没有覆盖第二条路径。这是本次移植
需要额外留意的地方。

**环境已经装好，基线已经跑过。** Python 环境在 `.venv/`，Node 依赖已安装。
2026-09-10 在这个环境上跑出的基线，四条全绿：

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 后端测试 | `.venv/bin/python -m pytest tests/ -q` | 525 passed |
| 后端静态检查 | `.venv/bin/python -m pylint backend/ --errors-only --disable=import-error,no-member` | 无输出 |
| 前端测试 | `npm run test:frontend` | 123 passed |
| 前端 lint 与构建 | `npm run lint:frontend`、`npm run build:frontend` | 通过 |

开工前自己重跑一遍，用你当时的数字当基线，不要沿用上面这四个：它们是一次
测量，不是承诺。

**这个环境是 Python 3.14，CI 用的是 3.10**（见 `.github/workflows/ci.yml`）。
机器上没有 3.10，Xcode 自带的 3.9 装不上依赖（`requirements-local.txt` 里
写了原因）。3.14 上全部依赖都装成功、测试也全过，但版本差异真实存在：如果
出现本地绿而 CI 挂，先怀疑这里。

`pytest` 和 `pylint` 不在 `requirements-local.txt` 里，CI 单独装它们，本地
也是单独装的。跑后端检查一律走 `.venv/bin/python -m ...`，不要用系统
`python3`。

**本仓库的上下文提供了移过来的代码要读的全部字段**：`friendly_error`、
`stt_provider_labeler`、`auto_lark_exporter`、`diarization_requested`、`loop`、
`task_started_at`、`do_lark`、`display_title_value`、`title`。`account_user`、
`lark_app_id`、`lark_app_secret` 传的是 `None`，这是本地版的正常形态。

## 来源提交

按这个顺序移，每一刀都建立在上一刀之上。在 `../fluentflow` 里 `git show` 查看。

1. `314dd5d1` — 把重复的媒体时长探测合并成 `backend/core/media_probe.py`
2. `c92714b4` — 取消和失败两条收尾路径提到 `backend/core/media_job_outcome.py`
3. `82d683e0` — 飞书导出阶段提到 `backend/core/media_job_stages.py`
4. `b120e7fb` — 发言人标注阶段提到同一个 stages 模块
5. `319c661a` — 前端提示词模板簇提成 `frontend/src/lib/usePromptEditing.js`

还有一刀 `faba28da`（转写清理阶段）也在 stages 模块里，可以跟第 4 刀一起移。

每个提交都自带守卫测试，测试文件一并移过来。这些路径在拆分前一条测试都没有。

## 需要你判断的

**第一刀在本仓库失去了它原本的理由。** 它解决的是「两个文件各有一份时长探测、
且已经长歪」，而本仓库没有 `server_helpers.py`，不存在那份重复。

两个选择：照移一份 `media_probe.py`，让两边文件保持一致，以后再移植成本低；
或者跳过它、保留 `media_job.py` 里原来的私有实现。后面几刀不依赖
`media_probe.py`，但第 2 刀之后的 `media_job.py` 会 import 它，跳过的话要改
那处 import。

建议照移，理由是保持两边一致；但这是个判断，不是结论。

**本仓库的 `media_job.py` 顶部有一句注释提到 server_helpers 的队列 worker。**
那个文件在本仓库已经不存在，注释是分离留下的。顺手改掉。

## 来源仓库故意没做的部分

**STT 阶段没有拆。** 它是 `_stream_media_job` 里剩下最大的一块，但它跟已经拆掉
的那些性质不同：它一边跑一边往外发进度事件，所以只能拆成异步生成器；同时它产出
八九个后面阶段要用的值。两样加在一起，拆出来会变成「一边发事件、一边往传进去的
可变对象里塞结果」，比现在更难读。**这不是没做完，是判断后决定不做。** 除非你有
更好的手法，否则不要把它当成遗留任务捡起来。

**`server_helpers.py` 那边也没动**，与本仓库无关，此处只是说明来源仓库的状态。

## 验证

按本仓库 `AGENTS.md` 的规矩，不要照抄 Hosted 的命令，两边不一样。

后端改动跑相关 `pytest`。前端改动跑 `npm run lint:frontend`、
`npm run build:frontend`、`npm run test:frontend`。提交前 `git diff --check`。

**lint 不能省。** 移植过程中有两个真实的破绽是 lint 抓到的，而测试和构建都是
绿的：一处变量搬走后下面还在读（`pylint --errors-only` 抓到），一处按钮还在调
已经移走的函数（`npm run lint:frontend` 抓到，`no-undef`）。

## 第一步

在干净的工作区上重跑一遍上面那四条检查，把你自己的数字记下来当基线。后面每
移一刀都跟它比，这是判断一刀有没有移坏的唯一依据。

```bash
git status --short
.venv/bin/python -m pytest tests/ -q
npm run test:frontend
```

基线拿到之后，从来源提交列表的第 1 刀开始：读
`git -C ../fluentflow show 314dd5d1`，先回答「需要你判断的」里的第一条，再动手。
