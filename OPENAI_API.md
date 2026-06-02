# OpenAI 兼容 API 文档

本服务提供 OpenAI 风格接口，用于把支持自定义 OpenAI Base URL 的客户端接入本地 BizyAir 生图队列。

## 基础信息

默认 Base URL：

```text
http://127.0.0.1:8787/v1
```

如果服务绑定到局域网地址或 `0.0.0.0`，请以前端“OpenAI 设置”弹窗展示的 Base URL 为准。

所有 `/v1/*` 接口都使用 Bearer Token 鉴权：

```http
Authorization: Bearer <ADMIN_TOKEN>
```

其中 `<ADMIN_TOKEN>` 是服务配置中的访问口令。

## 兼容范围

当前支持以下 OpenAI 风格接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/v1/models` | 获取支持的模型列表。 |
| `POST` | `/v1/images/generations` | 文生图。 |
| `POST` | `/v1/images/edits` | 图生图 / 图片编辑。 |
| `POST` | `/v1/chat/completions` | 以 Chat Completions 形式提交生图请求。 |

这些接口会把请求转换为本项目内部任务，进入同一个后台队列，由 BizyAir 上游生成图片。

## 通用说明

### 支持的模型

可通过 `GET /v1/models` 获取当前服务支持的模型。当前模型包括：

- `gpt-image-1`
- `gpt-image-2`
- `gpt-image-2-official`
- `gemini-2.5-flash-image`
- `gemini-3-pro-image-preview`
- `gemini-3-pro-image-preview-official`
- `gemini-3.1-flash-image-preview`
- `gemini-3.1-flash-image-preview-official`

### 通用扩展参数

除了 OpenAI 常见字段，本服务还支持透传部分生图参数：

| 参数 | 说明 |
| --- | --- |
| `aspect_ratio` | 宽高比，只接受当前模型支持的值，例如 `1:1`、`16:9`、`9:16`。 |
| `resolution` | 分辨率，只接受当前模型支持的值，例如 `1k`、`2k`、`4k` 或 `1K`、`2K`、`4K`。 |
| `quality` | 质量，只接受当前模型支持的值，例如 `low`、`medium`、`high`。 |
| `variants` | 变体数量，只接受当前模型支持的值。 |
| `temperature` | Gemini 模型可用。 |
| `top_p` | Gemini 模型可用。 |
| `seed` | Gemini 模型可用，非负整数。 |
| `max_tokens` | Gemini 模型可用，非负整数。 |

### `size` 映射

OpenAI 风格的 `size` 会尽量映射为本服务的 `aspect_ratio`：

| `size` | 映射结果 |
| --- | --- |
| `1024x1024` | `aspect_ratio=1:1` |
| `1024x1536` | `aspect_ratio=2:3` |
| `1536x1024` | `aspect_ratio=3:2` |

如果同时传入 `aspect_ratio`，则优先使用显式传入的 `aspect_ratio`。

### `n` 与 `variants`

- 如果模型支持 `variants`，`n` 会被映射为 `variants`。
- 如果模型不支持多变体，只接受 `n=1`。
- 对不支持多变体的模型传入其他 `n` 值会返回错误。

### 响应格式

图片接口支持：

| `response_format` | 说明 |
| --- | --- |
| `b64_json` | 返回图片 base64。默认值。 |
| `url` | 返回图片访问 URL。 |

建议本地或自动化调用优先使用 `url`，避免大图 base64 导致响应体过大。

## `GET /v1/models`

获取当前支持的模型列表。

### 请求示例

```bash
ADMIN_TOKEN="replace-with-your-admin-token"

curl http://127.0.0.1:8787/v1/models \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### 响应示例

```json
{
  "object": "list",
  "data": [
    {
      "id": "gpt-image-2",
      "object": "model",
      "created": 0,
      "owned_by": "bizyair",
      "permission": [],
      "root": "gpt-image-2",
      "parent": null
    }
  ]
}
```

## `POST /v1/images/generations`

文生图接口。

### 请求字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `model` | 是 | 模型名，必须是 `/v1/models` 返回的模型之一。 |
| `prompt` | 是 | 文本提示词。 |
| `size` | 否 | OpenAI 风格尺寸，会映射为宽高比。 |
| `n` | 否 | 图片变体数量，部分模型支持。 |
| `response_format` | 否 | `b64_json` 或 `url`，默认 `b64_json`。 |
| `aspect_ratio` | 否 | 宽高比。 |
| `resolution` | 否 | 分辨率。 |
| `quality` | 否 | 质量。 |
| `variants` | 否 | 变体数量。 |
| `temperature` | 否 | Gemini 模型可用。 |
| `top_p` | 否 | Gemini 模型可用。 |
| `seed` | 否 | Gemini 模型可用。 |
| `max_tokens` | 否 | Gemini 模型可用。 |

### 请求示例

```bash
ADMIN_TOKEN="replace-with-your-admin-token"

curl -X POST http://127.0.0.1:8787/v1/images/generations \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image-2",
    "prompt": "一只橘猫坐在窗边，电影感光影",
    "aspect_ratio": "1:1",
    "resolution": "1k",
    "response_format": "url"
  }'
```

### 响应示例

`response_format=url`：

```json
{
  "created": 1760000000,
  "data": [
    {
      "url": "http://127.0.0.1:8787/api/images/1/file"
    }
  ]
}
```

`response_format=b64_json`：

```json
{
  "created": 1760000000,
  "data": [
    {
      "b64_json": "iVBORw0KGgoAAAANSUhEUg..."
    }
  ]
}
```

## `POST /v1/images/edits`

图生图 / 图片编辑接口。请求必须使用 `multipart/form-data`，图片会先上传到 BizyAir 输入资源，再创建本地生图任务。

### 表单字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `model` | 是 | 模型名，必须是 `/v1/models` 返回的模型之一。 |
| `prompt` | 是 | 文本提示词。 |
| `image` | 是 | 待编辑图片，可传一张或多张。也支持字段名 `image[]`。 |
| `size` | 否 | OpenAI 风格尺寸，会映射为宽高比。 |
| `n` | 否 | 图片变体数量，部分模型支持。 |
| `response_format` | 否 | `b64_json` 或 `url`，默认 `b64_json`。 |
| `aspect_ratio` | 否 | 宽高比。 |
| `resolution` | 否 | 分辨率。 |
| `quality` | 否 | 质量。 |
| `variants` | 否 | 变体数量。 |

### 上传限制

- 只允许图片文件。
- 支持扩展名：`.png`、`.jpg`、`.jpeg`、`.webp`、`.gif`。
- 图片数量全局最多 10 张，并受模型自身输入图上限限制。
- 上传大小受服务配置 `MAX_UPLOAD_MB` 限制。

### 请求示例

```bash
ADMIN_TOKEN="replace-with-your-admin-token"

curl -X POST http://127.0.0.1:8787/v1/images/edits \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -F "model=gpt-image-2" \
  -F "prompt=把图片改成赛博朋克夜景风格" \
  -F "image=@input.png" \
  -F "aspect_ratio=1:1" \
  -F "response_format=url"
```

多图输入示例：

```bash
curl -X POST http://127.0.0.1:8787/v1/images/edits \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -F "model=gemini-3-pro-image-preview" \
  -F "prompt=参考两张图，生成一张统一风格的新图" \
  -F "image=@reference-1.png" \
  -F "image=@reference-2.jpg" \
  -F "response_format=url"
```

## `POST /v1/chat/completions`

Chat Completions 兼容接口。适用于只能调用 OpenAI Chat Completions 的客户端。本服务会从 `messages` 中提取文本和图片 URL，转换成生图任务，完成后返回 assistant 消息。

### 请求字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `model` | 是 | 模型名，必须是 `/v1/models` 返回的模型之一。 |
| `messages` | 是 | OpenAI 风格消息数组。 |
| `stream` | 否 | 不支持 `true`。传入 `true` 会返回错误。 |
| `aspect_ratio` | 否 | 宽高比。 |
| `resolution` | 否 | 分辨率。 |
| `quality` | 否 | 质量。 |
| `variants` / `n` | 否 | 变体数量，部分模型支持。 |
| `temperature` | 否 | Gemini 模型可用。 |
| `top_p` | 否 | Gemini 模型可用。 |
| `seed` | 否 | Gemini 模型可用。 |
| `max_tokens` | 否 | Gemini 模型可用。 |

### 消息解析规则

- `messages` 必须是数组。
- 文本内容会按消息顺序合并为提示词。
- 字符串 `content` 会作为文本。
- 多模态数组中的 `{ "type": "text" }` 会作为文本。
- 多模态数组中的 `{ "type": "image_url" }` 会提取 `image_url.url` 作为输入图 URL。
- `image_url.url` 只支持 `http://` 或 `https://`。
- 不支持流式输出。

### 纯文本请求示例

```bash
ADMIN_TOKEN="replace-with-your-admin-token"

curl -X POST http://127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-image-2",
    "messages": [
      {
        "role": "user",
        "content": "生成一张未来城市天际线，黄昏，超现实风格"
      }
    ],
    "aspect_ratio": "16:9",
    "resolution": "1k"
  }'
```

### 多模态请求示例

```bash
curl -X POST http://127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3-pro-image-preview",
    "messages": [
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "参考这张图，改成水彩插画风格"},
          {"type": "image_url", "image_url": {"url": "https://example.com/input.png"}}
        ]
      }
    ],
    "aspect_ratio": "1:1"
  }'
```

### 响应示例

```json
{
  "id": "chatcmpl_123",
  "object": "chat.completion",
  "created": 1760000000,
  "model": "gpt-image-2",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "![image](http://127.0.0.1:8787/api/images/1/file)"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

## 错误响应

错误响应使用 OpenAI 风格结构：

```json
{
  "error": {
    "message": "请输入提示词",
    "type": "invalid_request_error",
    "param": "prompt",
    "code": null
  }
}
```

常见错误：

| HTTP 状态码 | 场景 |
| --- | --- |
| `400` | 请求参数错误、缺少提示词、不支持 `stream=true`、上传文件格式错误。 |
| `401` / `403` | 未携带或未通过 Bearer Token 鉴权。 |
| `404` | 模型不存在。 |
| `502` | 上游任务失败或图片上传失败。 |
| `504` | 等待上游任务超时。 |

## 接入 OpenAI SDK 示例

### Python

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8787/v1",
    api_key="replace-with-your-admin-token",
)

result = client.images.generate(
    model="gpt-image-2",
    prompt="一只橘猫坐在窗边，电影感光影",
    response_format="url",
)

print(result.data[0].url)
```

### JavaScript

```js
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://127.0.0.1:8787/v1",
  apiKey: "replace-with-your-admin-token",
});

const result = await client.images.generate({
  model: "gpt-image-2",
  prompt: "一只橘猫坐在窗边，电影感光影",
  response_format: "url",
});

console.log(result.data[0].url);
```

## 注意事项

- 这些接口是 OpenAI 风格兼容，不是完整 OpenAI API 实现。
- 生图请求是同步等待本地队列结果，耗时取决于上游 BizyAir 任务完成时间。
- 如果任务等待超过服务配置的最大轮询时间，会返回 `504`。
- `b64_json` 会由服务下载结果图后转成 base64，图片较大时响应会更慢。
- Chat Completions 接口返回的是包含图片 Markdown 链接的 assistant 消息，不返回文本模型推理结果。
