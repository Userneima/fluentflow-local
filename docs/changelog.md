# FluentFlow Local Changelog

## Unreleased

- macOS 新增一次性安装脚本 `launchers/macos/setup-local.sh`，与 Windows 的 `setup-local.ps1` 对等：建虚拟环境、装依赖、构建前端、跑就绪检查、生成桌面启动器。
- 本地版现在作为独立开发仓库维护；不再由线上版源码导出或被导出流程覆盖。
- 补齐本地版的构建、测试、CI 与平台启动器，后续本地功能直接在本仓库演进。
- 仓库边界已写入 Local 的代理入口、Claude 入口和维护文档：Hosted 与 Local 只通过显式、经验证的移植协作，不存在自动同步或导出关系。
