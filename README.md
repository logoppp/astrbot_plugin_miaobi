# 妙笔 · AstrBot 文生图 / 图生图 / 文生视频工作台

一个给 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 用的多供应商生成插件。
把「文生图 / 图生图 / 文生视频」收敛成一套模型抽象：**选模型，不选供应商**；
供应商按优先级排队，挂了自动换下一家。

> 版本 0.1.0 · 作者 logo · MIT License

---

## 它解决什么问题

大多数生图插件的配置方式是「供应商 → 模型」一对一。实际上同一个模型往往能跑在好几家：FLUX 可以走 Replicate、魔搭、本地 ComfyUI；Qwen-Image 可以走通义原生、OpenAI 兼容站、魔搭。

妙笔把这一层拆成两个概念：

| 层 | 管什么 | 举例 |
|---|---|---|
| **逻辑模型（catalog）** | 这个模型叫什么、能干什么、按什么顺序找供应商 | `FLUX.1 Dev` → 魔搭 → Replicate |
| **供应商（provider）** | 地址、密钥、超时、并发、冷却 | `魔搭` / `Replicate` / `本地 ComfyUI` |

默认设置只选逻辑模型。第一家的额度用完、限流、或连接不上，自动换 chain 里的下一家。

---

## 功能

- **多供应商回退**：同一模型可绑多家，按顺序尝试；供应商冷却中会被排到队尾。
- **13 种协议**：OpenAI Images、OpenAI Chat 生图、火山方舟图片/视频、DashScope 原生、Gemini、Stability、Replicate、可灵、MiniMax 视频、NovelAI、魔搭 ModelScope、ComfyUI Roundabout、自定义模板。
- **提示词优化**：每个模型可单独配 system prompt；优化用的 LLM 本身也支持回退链；超时直接复用 AstrBot 供应商配置，不另立一套参数。
- **画师串预设**：只对 NovelAI / Anima 这类 Danbooru tag 模型生效，两者的权重语法与画师标签格式分开处理。
- **任务队列**：按供应商分组，独立并发与冷却；429/5xx 才计冷却，400 不计。
- **元数据画廊**：每张图记录完整参数，一键回填到工作区复现。
- **QQ 命令与 LLM 工具**：`/文生图`、`/图生图`、`/文生视频`，以及可被模型调用的 `image_generate` / `image_edit` / `video_generate`。

## 安装

1. 把整个目录放进 AstrBot 的 `data/plugins/` 下，目录名保持 `astrbot_plugin_miaobi`。
2. 重启 AstrBot，在插件管理里启用「妙笔」。
3. 打开插件页面（工作台），先到「供应商」页加至少一个供应商。

依赖：`aiohttp`（AstrBot 自带）。

## 快速开始

```
供应商页 → 新建供应商 → 选协议 → 填 base_url 和 API Key → 拉取/手填模型
配置页 → 模型与供应商回退 → 给要用的模型排序 → 选默认模型
工作区 → 输入提示词 → 生成
```

配置页的模型清单是**自动生成**的：内置 24 个主流模型的预置定义，会按你已配的供应商自动匹配。
你只需要调整每个模型「用哪几家、谁先谁后」，不需要手动新建模型。

## 命令

| 命令 | 说明 |
|---|---|
| `/文生图 <提示词>` | 用默认文生图模型生成 |
| `/图生图 <提示词>` | 需要附带参考图 |
| `/文生视频 <提示词>` | 用默认视频模型生成 |

命令里可以带模型名、尺寸、比例、时长：

```
/文生图 nai45 白发少女 3:4
/文生图 flux-dev 机械城市 1024x1536
/文生视频 海浪拍岸 8
```

提示词优化开关：`--no-opt` / `不优化` / `原样` 关闭，`--opt` / `优化` 开启。

## 协议支持

| 协议 | 能力 | 备注 |
|---|---|---|
| `openai_images` | t2i / i2i | OpenAI 兼容的 `/images/generations`、`/images/edits` |
| `openai_chat_image` | t2i / i2i | 走 chat/completions 返回图片的平台 |
| `ark_image` | t2i / i2i | 火山方舟 · 豆包 Seedream / SeedEdit |
| `ark_video` | t2v | 火山方舟 · Seedance |
| `dashscope_native` | t2i / i2i | 阿里通义万相原生接口 |
| `gemini_image` | t2i / i2i | Gemini / Imagen |
| `modelscope` | t2i | 魔搭 API-Inference，异步提交 + 轮询 |
| `stability` | t2i | Stability AI |
| `replicate` | t2i | Replicate 模型版本 |
| `kling` | t2i / t2v | 可灵，AK/SK 自签 JWT |
| `minimax_video` | t2v | 海螺 |
| `novelai` | t2i | NovelAI 及兼容站 |
| `comfyui_roundabout` | t2i / i2i / t2v | 本地 ComfyUI + Roundabout 网关 |
| `custom` | 任意 | 自带请求体模板与结果路径 |

## 回退规则

不是所有失败都值得换一家，因为生图是计费操作：

| 情况 | 换下一家 |
|---|---|
| 429 限流 / 5xx 上游故障 / 连接失败 / DNS | ✅ |
| 401 / 403 / 404（密钥、额度、模型不存在） | ✅ |
| 队列已满 / 供应商冷却中 | ✅ |
| 504（本插件轮询超时，任务可能已在上游跑完） | ❌ 可能已扣费 |
| 请求发出后超时 | ❌ 可能已扣费 |
| 400 参数错误 | ❌ 换家也一样 |

## 提示词优化

- 优先级：**模型专属提示词 → 家族规则 → 内置文案**
- 优化 LLM 支持回退链，第一个失败自动换下一个
- 超时沿用 AstrBot 该供应商的 `timeout`，插件只加 30 秒兜底
- 失败默认降级用原提示词，不阻塞出图（可改为让任务失败）

内置家族：NovelAI、Anima、Qwen-Image、Z-Image、Krea/FLUX、MOSS、Seedream、视频、通用。

> NovelAI 用 `{}` `[]` 和 `1.2::tag::` 权重、画师标签写 `artist:名字`；
> Anima 用 `(tag:1.2)` 权重、画师名裸写。两者不通用，插件按模型名自动分流。

## 数据与隐私

- 所有配置、任务记录、生成的图片都在 AstrBot 的 `data/plugin_data/astrbot_plugin_miaobi/` 下，不在插件目录内。
- 插件不向任何第三方上报数据；请求只发往你自己配置的供应商地址。

## 目录结构

```
astrbot_plugin_miaobi/
├── main.py                  # 插件入口、命令、Web API
├── metadata.yaml
├── _conf_schema.json
├── USAGE.md                 # 详细用法
├── core/
│   ├── models.py            # 配置结构与迁移
│   ├── registry.py          # 模型解析与候选链
│   ├── service.py           # 调度、回退、落盘
│   ├── queue.py             # 按供应商分组的队列与冷却
│   ├── optimizer.py         # 提示词优化
│   ├── optimizer_profiles.py# 家族规则
│   ├── prompt_presets.py    # 画师串预设
│   ├── catalog_presets.py   # 内置模型清单
│   ├── presets.py           # 平台预设
│   ├── sizing.py            # 分辨率换算
│   ├── store.py / db.py / media.py / http.py
│   └── backends/            # 各协议实现
└── views/studio/index.html  # 内置工作台
```

## License

MIT

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供插件框架与消息平台适配
- 各家模型与平台文档，是 `core/backends/` 里参数与限制的依据
