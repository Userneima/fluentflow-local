# 已知问题（2026-10-08 复审 0.6.0）

来源：2026-10-08 对 0.6.0 的四路复审：代码复审（v0.5.0..v0.6.0）、隔离环境真实任务（本地视频、抖音、B 站、重复提交、重试）、设置页与界面审查、启动器与文档审查。修好一条就从这里删掉一条，并在 `docs/changelog.md` 记一笔。「实测」指真实跑过；其余为读代码得出。

## 安装与启动（未在 Windows 实机验证）

- Windows 启动器很可能一启动就闪退：`$ErrorActionPreference = "Stop"` 下，uvicorn 写到 stderr 的第一行会被 PowerShell 5.1 当成致命错误；`.cmd` 没有 `pause`，看不到原因。
- Windows 卸载脚本读错了 `/health` 字段，认不出正在运行的服务，会在服务运行中删虚拟环境。
- 启动过程中再点一次图标会起第二个启动流程，两个 pip 可能同时升级 yt-dlp；yt-dlp 升级按总时长强杀 pip，可能装坏一半；升级的那几秒里开始的链接下载会被报成「yt-dlp 没装」。
- 国内用户安装时设的 `PIP_INDEX_URL` 不会保存，之后每天的 yt-dlp 自动更新都去连 pypi.org，静默失败。
- 端口读法在启动器、卸载脚本、Windows 之间不一致；前端没构建时提示的 `npm run build:frontend:local` 不存在；Windows 脚本全是英文提示。
