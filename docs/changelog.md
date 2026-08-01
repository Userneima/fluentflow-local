# FluentFlow Local Changelog

## Unreleased

- Generated local edition.
- Windows 安装脚本现在会将 NVIDIA GPU 转录运行库安装在项目虚拟环境，避免 CTranslate2 因缺少 `cublas64_12.dll` 在首次转录时失败。
- 新增可复现的前端/Backend 静态检查依赖与 GitHub Actions CI。
- 补全本地版发布清单，使前端路由契约测试可在干净环境运行。
