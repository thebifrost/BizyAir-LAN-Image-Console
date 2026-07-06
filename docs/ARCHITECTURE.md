# 架构说明

本文档面向维护者，说明代码边界和常见改动入口。

## 请求流

```text
Browser / OpenAI-compatible client
  |
  v
server.http_routes
  |
  v
server.handlers.LanGatewayHandler
  |
  +--> server.database.Database
  +--> server.job_runner.JobRunner
  +--> server.upstream_client.UpstreamClient
  +--> server.image_cache.ImageCache
```

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `upload_server.py` | 兼容旧启动方式的入口。 |
| `server/app.py` | 组装配置、数据库、上游客户端、任务队列和 HTTP server。 |
| `server/http_routes.py` | 集中维护 GET/POST/DELETE 路由分发和认证动作。 |
| `server/handlers.py` | 具体 HTTP 处理逻辑，例如上传、任务管理、OpenAI 兼容接口和静态文件输出。 |
| `server/config.py` | 环境变量加载、类型转换、默认值和 provider 配置解析。 |
| `server/env_store.py` | 前端运行时配置保存和 `.env` 更新。 |
| `server/database.py` | SQLite schema、任务状态、审计日志和本地图片记录。 |
| `server/job_runner.py` | 后台 worker、任务执行、轮询、取消、结果图归档。 |
| `server/upstream_client.py` | BizyAir 和 OpenAI-compatible 上游 HTTP 客户端。 |
| `server/image_cache.py` | 远程图片缓存、本地结果归档和下载安全限制。 |
| `server/schemas.py` | 模型参数 schema、白名单清洗和输入 URL 规则。 |
| `static/js/` | 前端状态、API、上传、任务队列、历史、配置等模块。 |

## 新增接口流程

1. 在 `server/http_routes.py` 添加路由和认证 action。
2. 在 `server/handlers.py` 添加 `_handle_*` 方法。
3. 如果涉及状态持久化，在 `server/database.py` 添加最小必要方法。
4. 如果涉及配置，更新 `.env.example`、README 和 `server/env_store.py`。
5. 补测试并运行 `python scripts/check.py`。

## 维护原则

- 路由分发不要放回 `do_GET`、`do_POST` 或 `do_DELETE` 的大段条件判断里。
- 不要把上游 API 细节散落到多个模块；优先集中到 `server/upstream_client.py`。
- 不要绕过 `validate_params` 直接把前端参数发给上游。
- 所有读写本地文件的代码都应限制根目录、校验文件名或使用固定生成的 id。
