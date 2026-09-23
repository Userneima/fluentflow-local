# FluentFlow Local Changelog

## Unreleased

- 设置页最上面新增「笔记」一栏：先填文本模型的 API Key（默认 DeepSeek），可选再填 Anthropic API Key 让笔记结合画面来写，每个 Key 旁边都有创建入口。此前文本模型 Key 收在折叠区里，Anthropic Key 只能写进 `.env`。
- 首页在没有任何可用的模型 Key 时显示提示条，说明这次只会得到转录稿和字幕，并给出去设置的入口。
- Windows 安装脚本缺 Python 或 Node.js 时用 winget 直接装上，并检查 Python 不低于 3.10。
- README 补上机器要求、不用 git 的安装方式、第一次使用、飞书导出、AI 工具接入、更新和排错。

## 0.4.0 - 2026-09-18

- 转录默认用 large-v3。此前前端每次提交都发 medium，把后端的默认盖掉了，于是为 large-v3 调过的那些判断从来没有生效过。只能用 CPU 的机器仍会自动降到 medium。
- 转录模型在安装时就下好，不再留给用户的第一个任务。连不上 huggingface.co 时自动改用镜像源，也可以用 `HF_ENDPOINT` 或 `--mirror` 自己指定。
- 「结合画面的笔记」用你自己的 Anthropic API Key。
- 没有任何 Claude 凭据时，笔记退回 `AI_PROVIDER` 指定的服务写纯文字版，而不是什么都不产出。
- macOS 和 Windows 都新增卸载脚本 `uninstall-local.sh` / `uninstall-local.ps1`：默认只卸程序，转录模型和任务数据各自需要显式开口，可先用 `--dry-run` 看清单。
- 安装过程不再中途提问。缺 FFmpeg 或 Node.js 直接装上，每步开始前说明在做什么。
- 启动前的就绪检查会说明这台机器走哪条转录路径、模型在不在本机。

- macOS 新增一次性安装脚本 `launchers/macos/setup-local.sh`，与 Windows 的 `setup-local.ps1` 对等：建虚拟环境、装依赖、构建前端、跑就绪检查、生成桌面启动器。
- 本地版现在作为独立开发仓库维护；不再由线上版源码导出或被导出流程覆盖。
- 补齐本地版的构建、测试、CI 与平台启动器，后续本地功能直接在本仓库演进。
- 仓库边界已写入 Local 的代理入口、Claude 入口和维护文档：Hosted 与 Local 只通过显式、经验证的移植协作，不存在自动同步或导出关系。
