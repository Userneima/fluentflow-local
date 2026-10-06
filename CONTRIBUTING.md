# Contributing

欢迎提交问题报告、讨论和代码 Pull Request。本仓库是 FluentFlow Local 的独立开发源；请在这里实现和评审本地版行为。

请附上可复现步骤与环境信息，但不要提交用户媒体、转录、笔记、数据库、`.env`、访问令牌或私有运行记录。每项改动应同时更新相关测试；本地前端改动至少运行 `npm run lint:frontend`、`npm run build:frontend` 与 `npm run test:frontend`。后端改动运行 `.venv/bin/python -m pytest tests/ -q` 与 `.venv/bin/python -m ruff check backend/ scripts/`（规则和临时的逐文件豁免在 `pyproject.toml`，清理完一个文件就把它那行豁免删掉）。版本号只改 `VERSION` 文件，`package.json` 的 `version` 要与它一致。
