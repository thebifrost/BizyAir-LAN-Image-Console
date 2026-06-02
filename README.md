# BizyAir 局域网生图网关 by Bifrost

一个面向本机和可信局域网使用的 BizyAir 生图网关与图片生成控制台。它把 BizyAir API Key 保存在后端 `.env` 中，浏览器只通过本地网关提交任务、上传参考图、查看队列和管理历史结果，适合个人创作、工作室内网批量出图、多人共用同一台出图主机等场景。

> 安全定位：本项目默认用于本机或可信局域网，不是面向公网的多租户 SaaS。不要在未配置 HTTPS、反向代理、防火墙和强认证的情况下直接暴露到公网。

## 目录

- [核心功能](#核心功能)
- [工作原理](#工作原理)
- [项目结构](#项目结构)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [启动与访问](#启动与访问)
- [前端使用指南](#前端使用指南)
- [HTTP API](#http-api)
- [支持的模型与参数](#支持的模型与参数)
- [运行数据与目录](#运行数据与目录)
- [日志与审计](#日志与审计)
- [局域网部署](#局域网部署)
- [安全建议](#安全建议)
- [常见问题](#常见问题)
- [开发指南](#开发指南)
- [开源前检查清单](#开源前检查清单)
- [贡献指南](#贡献指南)
- [联系方式](#联系方式)
- [许可证](#许可证)

## 核心功能

- 浏览器图形界面：无需把 API Key 填到前端页面。
- 文生图与图生图：支持提示词、主图、参考图和模型参数组合。
- 主图/参考图上传：支持拖拽上传和文件选择。
- 历史上传复用：可从历史资源中选择图片作为主图或参考图。
- 批量提示词：一次提交多条提示词，后端拆分为多个子任务。
- 任务队列：本地队列按子任务执行，可查看排队、运行、成功、失败、取消状态。
- 多 BizyAir Key：支持单 Key 或多 Key，任务按 Key 轮询分配。
- 结果本地归档：生成结果会保存到本地 `data/result-images/`，减少远程图片失效影响。
- 历史画廊：支持查看、搜索、分页、预览、下载、复用历史结果。
- OpenAI 兼容接口：提供 `/v1/models`、`/v1/chat/completions`、`/v1/images/generations`、`/v1/images/edits`，可让支持 OpenAI API 的客户端接入本地 BizyAir 生图能力。
- OpenAI 设置弹窗：前端可查看 OpenAI Base URL、当前活跃队列数量、监听端口，并可保存端口配置或触发服务重启。
- 运行日志查看：前端可刷新查看应用日志和审计日志，后端只读取日志尾部，适合排查问题。
- 访问口令保护：前端请求受 `ADMIN_TOKEN` 保护，OpenAI 兼容接口同样使用 Bearer Token 认证。
- 本地日志与审计：记录运行日志、访问操作和任务操作，敏感 Key 会做脱敏处理。
- 可选请求调试日志：`DEBUG_REQUESTS` 默认关闭，排障时可临时打开完整请求/响应日志。

## 工作原理

```text
浏览器控制台
  │
  │  Authorization: Bearer <ADMIN_TOKEN>
  ▼
本地/局域网 Python 网关
  │
  ├─ 读取 .env 中的 BizyAir API Key
  ├─ 上传输入图片到 BizyAir 提供的 OSS 临时凭证
  ├─ 创建 BizyAir 生图任务
  ├─ 按间隔轮询上游任务状态
  ├─ 将结果写入 SQLite
  └─ 将结果图归档到本地 result-images
  │
  ▼
BizyAir API
```

浏览器只知道 `ADMIN_TOKEN`，不直接持有 BizyAir API Key。`ADMIN_TOKEN` 保存在浏览器 `sessionStorage` 中，关闭标签页后需要重新输入。

## 项目结构

```text
.
├── upload_server.py          # 启动入口
├── start.bat                 # Windows 双击启动脚本
├── index.html                # 前端页面
├── static/
│   ├── css/app.css           # 前端样式
│   └── js/                   # 前端逻辑
├── server/
│   ├── app.py                # HTTP 服务组装与启动
│   ├── config.py             # 环境变量与配置加载
│   ├── handlers.py           # HTTP 路由、认证、上传、任务 API
│   ├── database.py           # SQLite 存储与任务状态管理
│   ├── job_runner.py         # 后台任务队列与上游轮询
│   ├── upstream_client.py    # BizyAir 生图/账户 API 客户端
│   ├── bizyUpImage.py        # BizyAir 输入图片上传客户端
│   ├── image_cache.py        # 远程图片缓存与结果图归档
│   ├── key_pool.py           # 多 Key 轮询分配
│   ├── logging_utils.py      # 日志配置与敏感信息脱敏
│   └── schemas.py            # 模型参数白名单与校验
├── .env.example              # 配置模板，可提交到仓库
├── requirements.txt          # Python 依赖
├── .gitignore                # 开源忽略规则
└── LICENSE                   # GPL-3.0 许可证
```

运行后会自动生成 `data/`、`logs/` 等目录。这些目录可能包含任务记录、生成图片、访问痕迹或本地数据，不应提交到公开仓库。

## 环境要求

- Python 3.10 或更高版本
- 可访问 BizyAir API 的网络环境
- 有效的 BizyAir API Key
- Windows、macOS 或 Linux

Python 依赖：

- `requests`
- `alibabacloud-oss-v2`

## 快速开始

### 1. 获取代码

```bash
git clone <your-repository-url>
cd <your-repository-directory>
```

如果你是直接下载 ZIP，解压后进入项目根目录即可。

### 2. 创建虚拟环境

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Windows CMD：

```bat
python -m venv .venv
.venv\Scripts\activate.bat
```

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 创建配置文件

```bash
cp .env.example .env
```

Windows 也可以直接复制 `.env.example` 并重命名为 `.env`。

### 5. 编辑 `.env`

至少填写：

```env
BIZYAIR_API_KEY=your-bizyair-api-key
ADMIN_TOKEN=replace-with-a-random-token-at-least-16-chars
APP_HOST=127.0.0.1
APP_PORT=8787
```

`ADMIN_TOKEN` 至少 16 个字符。建议使用随机字符串，不要使用简单密码。

### 6. 启动服务

```bash
python upload_server.py
```

Windows 用户也可以双击：

```text
start.bat
```

### 7. 打开浏览器

```text
http://127.0.0.1:8787
```

页面会要求输入 `.env` 中配置的 `ADMIN_TOKEN`。

## 配置说明

项目启动时会读取根目录下的 `.env`。如果同名环境变量已经存在，系统环境变量优先，`.env` 不会覆盖已有环境变量。

### 必填配置

| 变量 | 示例 | 说明 |
| --- | --- | --- |
| `BIZYAIR_API_KEY` | `sk-...` | 单 Key 模式下的 BizyAir API Key。 |
| `BIZYAIR_API_KEYS` | `key-1,key-2,key-3` | 多 Key 模式。配置后优先于 `BIZYAIR_API_KEY`。 |
| `ADMIN_TOKEN` | `a-long-random-token` | 局域网访问口令，最少 16 字符。 |

`BIZYAIR_API_KEY` 和 `BIZYAIR_API_KEYS` 二选一即可。

### 服务配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_HOST` | `127.0.0.1` | HTTP 服务监听地址。`127.0.0.1` 仅本机访问，`0.0.0.0` 可局域网访问。 |
| `APP_PORT` | `8787` | HTTP 服务端口。也可在前端“OpenAI 设置”弹窗中修改，保存后需要重启服务生效。 |
| `UPLOAD_SERVER_PORT` | `8787` | 旧端口变量，仅在未设置 `APP_PORT` 时作为兼容读取。 |
| `CORS_ORIGINS` | `http://127.0.0.1:<port>,http://localhost:<port>` | 允许跨域来源，逗号分隔。 |
| `DEBUG_REQUESTS` | 关闭 | 请求调试日志开关。设置为 `1`、`true`、`yes` 或 `on` 时，会把请求方法、路径、请求头、请求体和 JSON 响应写入 `app.log` 与控制台。只建议短时间排障使用，默认应保持关闭。 |

### BizyAir 上游配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BIZYAIR_BASE_URL` | `https://api.bizyair.cn/x/v1` | 生图任务 API 基地址。 |
| `BIZYAIR_WALLET_URL` | `https://api.bizyair.cn/y/v1/wallet` | 钱包余额查询地址。 |
| `BIZYAIR_METADATA_URL` | `https://api.bizyair.cn/x/v1/user/metadata` | 账户信息查询地址。 |
| `BIZYAIR_KEY_LABELS` | 空 | 多 Key 标签，逗号分隔，显示在前端账户列表和任务归属中。 |
| `BIZYAIR_KEY_LABEL` | `主账号` | 单 Key 模式下的 Key 标签。 |

### 任务与轮询配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WORKER_THREADS` | `32` | 后台 worker 数量。数字越大并发越高，也更容易触发上游限流或消耗额度。 |
| `POLL_INTERVAL_SECONDS` | `5` | 轮询 BizyAir 任务状态的间隔，最小 0.5 秒。 |
| `MAX_POLL_SECONDS` | `1800` | 单个子任务最大轮询时间，最小 30 秒。超时后标记失败。 |

### 上传与存储配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MAX_UPLOAD_MB` | `20` | 单个上传文件大小上限，代码内硬上限为 20 MB。 |
| `UPLOAD_RETRY_ATTEMPTS` | `2` | 上传失败后额外重试次数，`0` 表示不重试。 |
| `UPLOAD_RETRY_DELAY_SECONDS` | `1` | 上传失败后每次重试前等待秒数。 |
| `DATA_DIR` | `./data` | SQLite 数据库与结果图片目录。 |
| `LOG_DIR` | `./logs` | 日志目录。 |
| `RESULT_IMAGE_DIR` | `DATA_DIR/result-images` | 生成结果图本地归档目录。 |

### 图片缓存配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `IMAGE_CACHE_DIR` | 系统临时目录下的 `bizyair-lan-image-cache` | 远程图片显示缓存目录。 |
| `IMAGE_CACHE_MAX_MB` | `50` | 单张缓存图片大小上限。 |
| `IMAGE_CACHE_TTL_HOURS` | `168` | 缓存有效期，默认 7 天。 |
| `IMAGE_CACHE_TOTAL_MB` | `2048` | 缓存总容量上限。 |

### 单 Key 配置示例

```env
BIZYAIR_API_KEY=replace-with-your-bizyair-key
ADMIN_TOKEN=replace-with-a-random-token-at-least-16-chars
APP_HOST=127.0.0.1
APP_PORT=8787
```

### 多 Key 配置示例

```env
BIZYAIR_API_KEYS=key-a,key-b,key-c
BIZYAIR_KEY_LABELS=主账号,备用账号,团队账号
ADMIN_TOKEN=replace-with-a-random-token-at-least-16-chars
APP_HOST=127.0.0.1
APP_PORT=8787
```

多 Key 模式下，后台会按轮询方式为上传和任务分配 Key。已经绑定过 Key 的任务继续使用原 Key。

## 启动与访问

### 前台启动

```bash
python upload_server.py
```

启动成功后日志会显示：

```text
BizyAir LAN gateway running at http://127.0.0.1:8787
```

### Windows 双击启动

`start.bat` 会进入脚本所在目录，检查 `.env` 是否存在，然后执行：

```bat
python upload_server.py
```

如果缺少 `.env`，脚本会提示复制 `.env.example`。

### 停止服务

在终端中按：

```text
Ctrl+C
```

服务会停止 HTTP 监听，并通知后台 worker 退出。

## 前端使用指南

### 登录口令

1. 打开 `http://127.0.0.1:8787`。
2. 输入 `.env` 中的 `ADMIN_TOKEN`。
3. 点击保存口令。
4. 如需查看账户和余额，点击“查询账户与余额”。

口令只保存在当前浏览器标签页的 `sessionStorage`。关闭标签页后需要重新输入。

### 上传主图和参考图

- 主图：拖入“主图”区域，或点击“选择主图”。
- 参考图：拖入“参考图”区域，或点击“选择参考图”。
- 支持格式：`png`、`jpg`、`jpeg`、`webp`、`gif`。
- 单个文件默认最大 20 MB。
- 单次提交最多使用 10 张输入图。

主图固定作为第一张输入图。参考图从第二张开始拼接。如果选择多张主图，提交时会按任务自动轮换。

### 选择历史上传

点击“选择历史上传”，可以把历史上传或历史结果重新加入主图/参考图区域。

### 设置提示词和参数

在“生图工作区”填写提示词，选择模型、宽高比、分辨率、质量、变体数量、Seed 等参数。不同模型会显示不同可用字段。

### 提交生成

- “提交生成”：提交当前提示词和参数。
- “批次生成”：适合多条提示词批量提交。
- “取消当前”：取消当前任务。
- “失败自动重试”：前端在任务失败后自动调用重试接口，重试次数可在页面中配置。

后端会把一个批次拆成多个子任务，写入 SQLite，并交给后台 worker 执行。

### OpenAI 设置与运行状态

点击顶部“OpenAI 设置”可以打开运行设置弹窗。弹窗包含：

- `Base URL`：当前服务对外提供的 OpenAI 兼容接口地址，通常是 `http://127.0.0.1:8787/v1` 或局域网访问地址加 `/v1`。
- `队列`：当前数据库中仍处于 `queued` 或 `running` 的活跃子任务数量。该数值包含已被 worker 取走但仍在生成中的任务，不只是内存队列长度。
- `端口`：写入 `.env` 中的 `APP_PORT`。保存后不会立刻迁移当前进程端口，需要重启服务。
- `重启`：请求后端重启当前服务。重启前会停止接收新队列并等待已入队任务处理完成，减少正在执行的上游任务被重复提交的概率。
- `刷新`：刷新运行状态、Base URL、队列数量和新增外部 OpenAI 任务。

如果服务绑定 `APP_HOST=0.0.0.0`，OpenAI URL 响应会优先使用请求中的 `Origin` 或 `Host` 推导可访问地址，避免返回不可访问的 `0.0.0.0` 链接。

### 查看任务队列

任务队列显示每个任务的状态、进度和结果。常见状态：

| 状态 | 含义 |
| --- | --- |
| `queued` | 已入队，等待执行。 |
| `running` | 正在创建或轮询上游任务。 |
| `succeeded` | 已成功完成。 |
| `failed` | 执行失败。 |
| `cancelled` | 已取消。 |

### 使用历史画廊

历史画廊可用于：

- 预览生成结果。
- 下载本地归档图。
- 复制图片链接。
- 设置为主图。
- 添加为参考图。
- 重新生成。
- 加载历史参数。
- 删除本地历史图片记录。

删除历史图片会删除本地归档文件，并把数据库中的图片记录标记为删除。

## HTTP API

除 `/health`、静态文件和图片读取接口外，API 请求通常需要：

```http
Authorization: Bearer <ADMIN_TOKEN>
```

所有 JSON API 通常返回：

```json
{
  "status": true,
  "data": {}
}
```

失败时返回：

```json
{
  "status": false,
  "message": "错误信息"
}
```

### `GET /health`

健康检查，不需要认证。

响应示例：

```json
{
  "status": true,
  "data": {
    "version": "v0.1",
    "time": "2026-05-20T10:00:00+00:00",
    "queue_length": 0,
    "storage": "ready"
  }
}
```

### `GET /api/config`

获取前端可见配置，需要认证。

返回字段包括版本、轮询间隔、上传限制、worker 数量、Key 标签和模型列表。

### `GET /api/models`

获取支持的模型参数 schema，需要认证。

### `GET /api/account`

查询 BizyAir 账户、会员和余额，需要认证。

单 Key 返回一个账户摘要；多 Key 返回：

```json
{
  "status": true,
  "data": {
    "keys": [
      {
        "id": "key-1",
        "label": "主账号",
        "account": "账户名",
        "status": "状态",
        "membership": "会员等级",
        "expire_at": "到期时间",
        "total_balance": "总余额",
        "charge_balance": "充值余额",
        "gift_balance": "赠送余额"
      }
    ]
  }
}
```

### `GET /api/inputs`

查询 BizyAir 已上传输入资源，需要认证。

查询参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `current` | `1` | 页码，最小 1。 |
| `page_size` | `20` | 每页数量，最大 100。 |

示例：

```text
GET /api/inputs?current=1&page_size=20
```

### `POST /api/upload`

上传输入图片到 BizyAir，需要认证。

请求类型：

```http
Content-Type: multipart/form-data
```

表单字段：

| 字段 | 说明 |
| --- | --- |
| `file` | 图片文件。 |

限制：

- 文件不能为空。
- 扩展名必须是 `png`、`jpg`、`jpeg`、`webp`、`gif`。
- MIME 类型必须以 `image/` 开头。
- 文件大小不能超过 `MAX_UPLOAD_MB`。

### `POST /api/jobs`

创建生图任务，需要认证。

请求体：

```json
{
  "model": "gpt-image-2",
  "prompts": [
    "一只橘猫坐在窗边，电影感光影"
  ],
  "params": {
    "aspect_ratio": "1:1",
    "resolution": "1k",
    "urls": ["https://example.com/input.png"],
    "seed": 0
  }
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `model` | string | 是 | 必须是支持的模型名。 |
| `prompts` | string[] | 是 | 提示词数组，最多 100 条空白提示词会被忽略。 |
| `params` | object | 否 | 模型参数，会按白名单清洗。 |

成功响应状态码为 `201`。

### `GET /api/jobs`

获取任务列表，需要认证。按更新时间倒序返回。

### `GET /api/jobs/{job_id}`

获取单个任务详情，需要认证。

### `POST /api/jobs/{job_id}/cancel`

取消任务，需要认证。

已排队的子任务会立即标记为取消；正在运行的子任务会在下一次轮询时检测取消状态。

### `POST /api/jobs/{job_id}/retry`

重试失败或已取消的任务，需要认证。

只有 `failed` 或 `cancelled` 状态的任务可以重试。重试会清空旧的上游 request id，并重新入队。

### `GET /api/image-cache?url=<remote-image-url>`

通过本地缓存代理读取远程图片。用于前端显示远程图片。

### `GET /api/images/{image_id}`

读取本地归档结果图。

`image_id` 必须是 32 位十六进制字符串。

### `GET /api/images/{image_id}/download`

下载本地归档结果图。

### `GET /api/admin/runtime`

获取运行状态，需要认证。前端“OpenAI 设置”弹窗会调用该接口。

返回字段包括：

| 字段 | 说明 |
| --- | --- |
| `host` | 当前配置的监听地址。 |
| `port` | 当前配置的监听端口。 |
| `openai_base_url` | OpenAI 兼容接口 Base URL，格式通常为 `<当前来源>/v1`。 |
| `queue_length` | 活跃队列数量，统计数据库中 `queued` 和 `running` 的子任务。 |
| `worker_threads` | 后台 worker 数量。 |
| `log_dir` | 日志目录。 |
| `app_log` | 应用日志文件路径。 |
| `audit_log` | 审计日志文件路径。 |

### `POST /api/admin/config`

保存运行配置，需要认证。目前支持修改 `APP_PORT`。

请求示例：

```json
{
  "port": 8788
}
```

保存成功后会写入 `.env`，并返回 `restart_required` 标识当前进程是否需要重启后才会使用新端口。

### `POST /api/admin/restart`

重启服务，需要认证。接口会先写入审计日志，然后在后台线程中停止 runner、等待已入队任务完成、关闭 HTTP 服务并用当前 Python 命令重新执行进程。

### `GET /api/logs`

读取日志尾部，需要认证。用于前端日志面板。

查询参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `type` | `app` | 日志类型，可选 `app` 或 `audit`。 |
| `lines` | `120` | 返回最后多少行，范围为 1-500。 |

后端会按块读取日志文件尾部，而不是把整个日志文件载入内存；返回前仍会对敏感内容做脱敏处理。

### OpenAI 兼容接口

独立接入文档见 [OPENAI_API.md](OPENAI_API.md)。

以下接口使用 OpenAI 风格路径与 Bearer Token 认证，适合接入支持自定义 OpenAI Base URL 的客户端。Base URL 可在前端“OpenAI 设置”中查看，通常为：

```text
http://127.0.0.1:8787/v1
```

鉴权方式：

```http
Authorization: Bearer <ADMIN_TOKEN>
```

#### `GET /v1/models`

返回当前支持的模型列表。模型来源与前端模型 schema 一致。

#### `POST /v1/images/generations`

OpenAI 风格文生图接口。请求会被转换为本地任务，进入同一个后台队列，由 BizyAir 上游生成图片。

常用请求字段：

| 字段 | 说明 |
| --- | --- |
| `model` | 必填，必须是本项目支持的模型名。 |
| `prompt` | 必填，文本提示词。 |
| `size` | 可选，OpenAI 风格尺寸，会尽量映射为模型支持的宽高比或分辨率。 |
| `n` | 可选，部分模型可映射为 `variants`；不支持多变体的模型只接受 `1`。 |
| `response_format` | 可选，`url` 或 `b64_json`。未指定时按 OpenAI 习惯返回 `b64_json`。 |

示例：

```bash
ADMIN_TOKEN="replace-with-your-admin-token"
curl -X POST http://127.0.0.1:8787/v1/images/generations \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-image-2","prompt":"一只橘猫坐在窗边","response_format":"url"}'
```

#### `POST /v1/images/edits`

OpenAI 风格图生图/编辑接口，使用 `multipart/form-data` 上传图片。上传图片会先转存到 BizyAir 输入资源，再进入本地任务队列。

常用表单字段：

| 字段 | 说明 |
| --- | --- |
| `model` | 必填，必须是本项目支持的模型名。 |
| `prompt` | 必填，文本提示词。 |
| `image` | 必填，可传一张或多张图片。 |
| `size` | 可选，OpenAI 风格尺寸映射。 |
| `n` | 可选，部分模型可映射为 `variants`。 |
| `response_format` | 可选，`url` 或 `b64_json`。 |

#### `POST /v1/chat/completions`

OpenAI 风格 Chat Completions 接口。该接口用于兼容把图片生成能力包装成对话请求的客户端：服务会从 `messages` 中提取文本和图片 URL，转换成 BizyAir 生图任务，完成后返回包含图片 Markdown 链接的 assistant 消息。

限制：

- 不支持 `stream=true`。
- `messages` 必须是数组。
- 文本内容会合并为提示词。
- 图片输入会从 OpenAI 多模态消息结构中提取 URL。

### `DELETE /api/images/{image_id}`

删除本地归档结果图，需要认证。

该操作会：

1. 将数据库中的图片记录标记为 `deleted`。
2. 删除本地图片文件。
3. 写入审计日志。

## 支持的模型与参数

模型参数定义在 `server/schemas.py`。当前支持：

| 模型 | 宽高比 | 分辨率 | 质量 | 变体 | 输入图上限 |
| --- | --- | --- | --- | --- | --- |
| `gpt-image-1` | `1:1`, `2:3`, `3:2` | - | - | `1`, `2`, `4` | 10 |
| `gpt-image-2` | `1:1`, `2:3`, `3:2`, `4:5`, `5:4`, `3:4`, `4:3`, `16:9`, `9:16`, `21:9` | `1k`, `2k`, `4k` | - | - | 10 |
| `gpt-image-2-official` | `1:1`, `1:3`, `3:1`, `2:3`, `3:2`, `4:5`, `5:4`, `3:4`, `4:3`, `16:9`, `9:16`, `21:9` | `1k`, `2k`, `4k` | `low`, `medium`, `high` | - | 10 |
| `gemini-2.5-flash-image` | `1:1`, `16:9`, `9:16`, `4:3`, `3:4` | - | - | - | 5 |
| `gemini-3-pro-image-preview` | `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9` | `1K`, `2K`, `4K` | - | - | 10 |
| `gemini-3-pro-image-preview-official` | `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9` | `1K`, `2K`, `4K` | - | - | 10 |
| `gemini-3.1-flash-image-preview` | `1:1`, `1:4`, `4:1`, `1:8`, `8:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9` | `1K`, `2K`, `4K` | - | - | 10 |
| `gemini-3.1-flash-image-preview-official` | `1:1`, `1:4`, `4:1`, `1:8`, `8:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9` | `1K`, `2K`, `4K` | - | - | 10 |

通用参数：

| 参数 | 说明 |
| --- | --- |
| `aspect_ratio` | 宽高比，只接受当前模型 schema 中列出的值。 |
| `resolution` | 分辨率，只接受当前模型 schema 中列出的值。 |
| `quality` | 质量，只接受当前模型 schema 中列出的值。 |
| `variants` | 变体数量，只接受当前模型 schema 中列出的值。`gpt-image-1` 使用 `4` 时会自动设置 `provider=KieAI`。 |
| `urls` | 输入图片 URL 数组，只接受 `http://` 或 `https://`。全局最多 10 张，并受模型上限限制。 |
| `temperature` | Gemini 模型可用，范围由 schema 限制。 |
| `top_p` | Gemini 模型可用，范围由 schema 限制。 |
| `seed` | Gemini 模型可用，非负整数。 |
| `max_tokens` | Gemini 模型可用，非负整数。 |

## 运行数据与目录

默认运行数据：

```text
data/jobs.sqlite
data/result-images/
logs/app.log
logs/audit.log
```

### SQLite 表

| 表 | 说明 |
| --- | --- |
| `jobs` | 批次任务主表。 |
| `job_items` | 子任务表，每条提示词对应一个子任务。 |
| `audit_logs` | 审计日志表。 |
| `job_images` | 本地归档结果图记录。 |

### 不应提交的数据

以下内容已被 `.gitignore` 排除，不应开源提交：

```text
.env
data/
logs/
*.sqlite
__pycache__/
.venv/
.temp/
.claude/settings.local.json
```

开源前请确认仓库中没有真实 API Key、生成图片、用户提示词、审计日志或本地数据库。

## 日志与审计

### 应用日志

路径：

```text
logs/app.log
```

记录服务启动、HTTP 请求、后台任务、上游轮询、异常等信息。

如果临时开启 `DEBUG_REQUESTS=1`，还会记录请求头、请求体和 JSON 响应内容。该模式可能包含提示词、图片 URL、Token 或其他敏感信息，也会显著增加日志体积；排障完成后应改回 `DEBUG_REQUESTS=0` 并重启服务。

前端日志面板通过 `/api/logs` 读取日志尾部。后端按块读取最后若干行，避免大日志文件被完整读入内存。

### 审计日志

路径：

```text
logs/audit.log
```

同时也会写入 SQLite 的 `audit_logs` 表。记录字段包括：

- 时间
- 客户端 IP
- 操作名称
- 操作结果
- 安全处理后的详情

常见操作：

- `config`
- `models`
- `inputs:list`
- `account:get`
- `upload`
- `jobs:create`
- `jobs:cancel`
- `jobs:retry`
- `images:delete`

## 局域网部署

默认配置只允许本机访问：

```env
APP_HOST=127.0.0.1
```

如果需要同一局域网内其他设备访问，改为：

```env
APP_HOST=0.0.0.0
```

并显式配置允许来源，例如服务器局域网 IP 为 `192.168.1.10`：

```env
CORS_ORIGINS=http://127.0.0.1:8787,http://localhost:8787,http://192.168.1.10:8787
```

然后其他设备访问：

```text
http://192.168.1.10:8787
```

如果无法访问，请检查：

1. 服务是否正在运行。
2. `APP_HOST` 是否为 `0.0.0.0`。
3. 端口是否正确。
4. Windows 防火墙是否放行 Python 或对应端口。
5. 访问设备是否在同一局域网。
6. 路由器是否开启客户端隔离。
7. `CORS_ORIGINS` 是否包含实际访问地址。

## 安全建议

### 不要提交敏感文件

不要提交：

- `.env`
- 真实 API Key
- `data/`
- `logs/`
- 生成图片
- 用户提示词历史
- SQLite 数据库

如果 API Key 已经泄露，请立即到 BizyAir 后台轮换。

### ADMIN_TOKEN 要足够随机

错误示例：

```env
ADMIN_TOKEN=1234567890123456
```

建议使用随机字符串，例如密码管理器生成的 32 位以上 token。

### 不建议直接公网开放

本项目内置的是轻量级 Bearer Token 认证，适合可信局域网。公网部署至少需要自行增加：

- HTTPS
- 反向代理
- 防火墙
- 强认证
- 访问频率限制
- 日志轮转和监控
- 备份与密钥轮换策略

### 上传限制

上传接口会检查扩展名、MIME 类型和大小，但它不是完整的内容安全扫描器。不要让不可信用户直接上传任意文件。

### 图片缓存限制

图片缓存用于显示远程图片和归档结果图。对于远程 URL，服务会进行一定限制，但仍建议只在可信网络和可信用户范围内使用。

## 常见问题

### 启动时报“缺少 ADMIN_TOKEN，或长度小于 16 个字符”

检查 `.env` 是否存在，并确认：

```env
ADMIN_TOKEN=至少16个字符
```

### 启动时报“缺少 BIZYAIR_API_KEY 或 BIZYAIR_API_KEYS”

单 Key 模式配置：

```env
BIZYAIR_API_KEY=your-key
```

多 Key 模式配置：

```env
BIZYAIR_API_KEYS=key-a,key-b
```

### 浏览器提示未授权

确认页面输入的口令和 `.env` 中的 `ADMIN_TOKEN` 完全一致。修改 `.env` 后需要重启服务。

### 页面打不开

检查服务是否启动，端口是否被占用，访问地址是否正确。默认地址是：

```text
http://127.0.0.1:8787
```

### 局域网设备无法访问

确认 `APP_HOST=0.0.0.0`，并检查防火墙、端口和服务器局域网 IP。

### 上传失败

可能原因：

- 文件超过 `MAX_UPLOAD_MB`。
- 文件扩展名不在允许列表中。
- BizyAir API Key 无效或额度不足。
- 网络无法访问 BizyAir 或 OSS。
- 上游上传凭证获取失败。

查看 `logs/app.log` 获取具体错误。

### 任务一直 running

可能原因：

- 上游任务仍在排队或生成。
- `POLL_INTERVAL_SECONDS` 设置较大。
- 上游返回一直处于 `running`、`queuing` 或 `saving`。
- 网络请求较慢。

超过 `MAX_POLL_SECONDS` 后，子任务会失败并记录超时错误。

### 任务失败但没有图片

查看任务详情和 `logs/app.log`。常见原因包括上游返回失败、额度不足、参数不被上游接受、输入图 URL 失效或网络异常。

### 修改配置后没有生效

`.env` 只在服务启动时读取。修改后需要重启服务。

### 结果图很多，占用磁盘

结果图默认保存在：

```text
data/result-images/
```

可以通过历史画廊删除不需要的图片，也可以调整 `DATA_DIR` 或 `RESULT_IMAGE_DIR` 到更大的磁盘位置。

## 开发指南

### 本地运行

```bash
python upload_server.py
```

### 推荐开发流程

1. 创建虚拟环境。
2. 安装依赖。
3. 复制 `.env.example` 为 `.env`。
4. 使用测试 API Key 或低额度 Key。
5. 启动服务并在浏览器验证。
6. 修改后至少测试：登录、账户查询、上传、单条生成、批量生成、取消、重试、历史画廊。

### 后端开发要点

- 新增路由通常在 `server/handlers.py`。
- 新增环境变量通常在 `server/config.py`。
- 修改任务状态或数据库字段通常在 `server/database.py`。
- 修改上游任务创建/轮询逻辑通常在 `server/job_runner.py` 和 `server/upstream_client.py`。
- 新增模型或参数白名单通常在 `server/schemas.py`。

### 前端开发要点

- 页面结构在 `index.html`。
- 样式在 `static/css/app.css`。
- 状态管理在 `static/js/state.js`。
- API 请求在 `static/js/api.js`。
- 认证逻辑在 `static/js/auth.js`。
- 上传逻辑在 `static/js/upload.js`。
- 任务队列在 `static/js/tasks.js`。
- 历史画廊在 `static/js/history.js`。
- 事件绑定在 `static/js/events.js`。

### API 调试示例

健康检查：

```bash
curl http://127.0.0.1:8787/health
```

获取模型：

```bash
ADMIN_TOKEN="replace-with-your-admin-token"
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://127.0.0.1:8787/api/models
```

创建任务：

```bash
ADMIN_TOKEN="replace-with-your-admin-token"
curl -X POST http://127.0.0.1:8787/api/jobs \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-image-2","prompts":["一只橘猫坐在窗边"],"params":{"aspect_ratio":"1:1","resolution":"1k"}}'
```

上传图片：

```bash
ADMIN_TOKEN="replace-with-your-admin-token"
curl -X POST http://127.0.0.1:8787/api/upload \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -F "file=@example.png"
```

### 发布版本建议

开源发布前建议：

1. 确认 `requirements.txt` 存在并可安装。
2. 确认 `.env.example` 不包含真实 Key。
3. 确认 `.gitignore` 排除了运行数据。
4. 删除本地 `data/`、`logs/`、`.temp/`、`__pycache__/` 后再提交。
5. 重新 clone 到干净目录测试快速开始流程。
6. 确认 `LICENSE` 与 README 中的许可证一致。
7. 在 GitHub Release 中说明版本、兼容 Python 版本和重要变更。

## 开源前检查清单

- [ ] `.env` 未被提交。
- [ ] `.env.example` 只包含占位符。
- [ ] `data/` 未被提交。
- [ ] `logs/` 未被提交。
- [ ] 生成图片未被提交。
- [ ] SQLite 数据库未被提交。
- [ ] `__pycache__/` 未被提交。
- [ ] `.temp/` 未被提交。
- [ ] README 中没有真实 IP、Key、账户名或私人路径。
- [ ] `requirements.txt` 可用于安装依赖。
- [ ] `python upload_server.py` 可以正常启动。
- [ ] 浏览器可以登录并访问 `/api/config`。
- [ ] 上传、创建任务、取消、重试、历史画廊经过手动验证。

## 贡献指南

欢迎提交 Issue 和 Pull Request。建议贡献前先说明你的使用场景、问题复现步骤或预期改动。

### Issue 建议包含

- 操作系统和 Python 版本。
- 项目版本或提交号。
- 复现步骤。
- 期望行为。
- 实际行为。
- 相关日志片段，注意去除 API Key、Token、私人图片 URL 和账户信息。

### Pull Request 建议包含

- 改动目的。
- 主要变更点。
- 手动测试步骤。
- 是否涉及配置变更。
- 是否涉及数据库结构变更。
- 是否涉及安全边界变化。

### 代码约定

- 不要把 API Key、Token 或真实用户数据写入代码、测试或文档。
- 后端新增外部输入时需要做边界校验。
- 前端不要保存 BizyAir API Key。
- 不要把本地路径、私有网络地址或生成图片作为示例提交。
- 变更模型参数时同步更新 `server/schemas.py` 和文档。

## 联系方式

- 作者：Bifrost
- 微信：ct221678

## 许可证

本项目使用 [GNU General Public License v3.0](LICENSE) 开源。

你可以自由使用、复制、修改和分发本项目，但需要遵守 GPL-3.0 的条款。该软件按“原样”提供，不提供任何明示或暗示担保。使用第三方 API 产生的费用、合规责任和服务条款责任由使用者自行承担。
