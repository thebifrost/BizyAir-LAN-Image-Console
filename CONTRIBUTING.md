# 贡献指南

感谢你愿意改进这个项目。这个仓库的定位是本机或可信局域网生图网关，因此维护优先级是：稳定、安全、可排查、易配置。

## 本地开发

1. 创建虚拟环境。

   ```bash
   python -m venv .venv
   ```

2. 激活虚拟环境并安装依赖。

   ```bash
   pip install -r requirements.txt -r requirements-dev.txt
   ```

3. 复制配置模板。

   ```bash
   cp .env.example .env
   ```

4. 填写 `.env` 中的 `ADMIN_TOKEN` 和至少一种上游 API Key。

5. 启动服务。

   ```bash
   python upload_server.py
   ```

## 提交前检查

提交 PR 前请运行：

```bash
python scripts/check.py
```

该脚本会执行 Python 编译检查、可用时执行 Ruff 基线检查，并运行单元测试。

## 代码组织

- `server/http_routes.py` 只负责 HTTP 路由分发与认证入口选择。
- `server/handlers.py` 负责具体 HTTP 请求处理逻辑。
- `server/job_runner.py` 负责后台任务队列、上游调用和轮询。
- `server/database.py` 负责 SQLite 表结构和任务状态维护。
- `server/config.py` 与 `server/env_store.py` 负责配置加载和运行时配置保存。
- `static/js/` 按前端功能拆分模块，避免把新逻辑直接写进 `index.html`。

新增接口时，优先在 `server/http_routes.py` 加路由，在 `server/handlers.py` 添加一个聚焦的小处理方法，并补充对应测试或文档。

## 安全要求

- 不要提交 `.env`、真实 API Key、生成图片、SQLite 数据库或日志。
- 新增日志时必须考虑 `server/logging_utils.py` 的脱敏能力。
- 新增远程 URL 读取能力时必须考虑 SSRF、防私网访问、大小限制和 Content-Type 校验。
- 公网部署相关改动必须同时更新 README 和 `SECURITY.md`。

## PR 建议

- 每个 PR 尽量只处理一个主题。
- 行为变化要更新 README 或相关文档。
- 修复 bug 时优先补一个能复现问题的测试。
