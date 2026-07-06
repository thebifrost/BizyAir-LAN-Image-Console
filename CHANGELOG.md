# Changelog

本项目采用简化的变更日志格式。正式发布时建议按版本补充日期和变更摘要。

## Unreleased

- 补充开源维护文档、CI、开发依赖和本地检查脚本。
- 将 HTTP 路由分发从 `server/handlers.py` 抽到 `server/http_routes.py`，降低新增接口时的维护成本。
