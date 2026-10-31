# 妙笔（astrbot_plugin_miaobi）功能与使用说明

> 版本 v0.1.0 · 最后更新 2026-10-30（含本次新增的「提示词优化开关」）
> 配置目录：`data/plugin_data/astrbot_plugin_miaobi/config/`（providers.json / settings.json）

---

## 一、它是什么

把「文生图 / 图生图 / 文生视频」三件事收进一个插件：多供应商、多模型、统一任务队列、
异步出图推送、网页工作台（画廊 + 队列 + 供应商配置）、可选的提示词 LLM 优化。

核心特点：**提交即返回，结果单独推送**。命令或工具调用后立刻拿到任务号，出图/出片后由插件
自己把成品发回原会话，不要让 LLM 或用户重复提交。

---

## 二、三种触发方式

| 方式 | 怎么用 | 特点 |
|---|---|---|
| **命令**（推荐） | `/文生图 …` `/图生图 …` `/文生视频 …` | 不经过 LLM 判断，最稳、最快、必达 |
| **自然语言** | 群里 @ 它 或用唤醒词「大肥鱼…」 | 由 LLM 决定是否调用 `image_generate` 等工具，**大上下文下可能不触发** |
| **网页工作台** | 面板 → 妙笔 | 可视化提交、选预设、看队列/画廊/任务状态 |

LLM 工具（`image_generate` / `image_edit` / `video_generate`）参数只有 `prompt`、`model`、
`aspect_ratio`（视频多一个 `duration`），**不支持尺寸精确值和临时优化开关**，优化与否跟随全局配置。

---

## 三、命令格式

```
/文生图   [优化开关] [模型] 提示词 [尺寸]
/图生图   [优化开关] [模型] 提示词 [尺寸]        ← 必须同时发一张图
/文生视频 [优化开关] [模型] 提示词 [时长]
```

**解析顺序**（照这个顺序写就不会错）：

1. 去掉开头前缀 `/ ! ！ 。`
2. 去掉命令名或「妙笔」
3. **摘出优化开关词**（可放在任意位置，只取第一个）
4. 视频：末尾如果是纯数字/带 s/秒 → 当作时长
5. 末尾如果是尺寸或比例 → 当作尺寸
6. 开头如果是已知模型 → 当作模型
7. 剩下的全部作为提示词

命令前缀也支持 `/` 之外的 `!` `！` `。`；唤醒词前缀由 AstrBot 决定，不受此限。

---

## 四、提示词优化开关（本次新增）

| 意图 | 可写的词（任取一个） |
|---|---|
| **关闭优化**，提示词原样直出 | `--no-opt` · `-noopt` · `no-opt` · `不优化` · `原样` · `不优化提示词` |
| **强制开启优化** | `--opt` · `-opt` · `opt` · `优化` · `优化提示词` |
| **不写** | 跟随全局 `optimizer.enabled`（当前 = **开**） |

⚠️ **开关词会被从提示词里摘掉**，不会发给模型。所以如果你的画面描述里本来要出现「优化」二字，
要么别用这个词，要么改用 `--opt` / `--no-opt` 之外的方式表达。

示例：

```
/文生图 不优化 一只赛璐璐风格的粉色小猪 3:4
/文生图 --no-opt 赛璐璐风格粉色小猪
/文生图 原样 best quality, 1girl, sakura
/文生图 --opt krea2 月光下的精灵弓手
/文生图 优化 zimage 水墨山水
/文生视频 海浪拍岸 8 不优化
```

后端本来就读 `request["optimize"]`（`core/service.py:168`），所以这只在命令层加了解析，
优化器本身没动：请求没带这个字段时行为完全不变。

---

## 五、尺寸怎么写（三种）

| 写法 | 含义 | 例子 |
|---|---|---|
| `832x1216` | 精确像素（`x` `*` `×` 都行） | `1024x1536` |
| `3:4` | 只给比例，按约 1MP 推算 | `3:4` `16:9` `1:1` |
| `3:4@1.5` | 比例 + 目标百万像素 | `9:16@2` |

兜底规则：先按**模型限制 → 协议限制 → 全局限制**逐层收紧，被调整过会在任务里留 note。
全局限制：`min_side=256 / max_side=4096 / 像素 4096~36,000,000`。

常用比例在 1MP 档的基准尺寸：

| 比例 | 1:1 | 3:4 | 4:3 | 9:16 | 16:9 | 2:3 | 3:2 | 21:9 |
|---|---|---|---|---|---|---|---|---|
| 尺寸 | 1024×1024 | 896×1152 | 1152×896 | 768×1344 | 1344×768 | 832×1248 | 1248×832 | 1536×640 |

**视频时长**：写在末尾，支持 `8`、`8s`、`8秒`。

---

## 六、当前可用模型

✅ = 供应商已启用，❌ = 已停用（写了也会被解析成「无匹配」）

| 模型 | 能力 | 推荐写法 | 状态 |
|---|---|---|---|
| `nai-diffusion-4-5-full`（**t2i 默认**） | 文生图 | `m_nai45`、`nai`、`novelai`、`nai-diffusion-4-5-full` | ✅ |
| `qwen-image-3.0` | 文生图 / 图生图 | `m_qwen3`、`qwen3`、`qwen-image-3.0` | ✅ |
| `qwen-image-2.1-pro` | 文生图 / 图生图 | `m_qwen21p`、`qwen21p`、`qwen-image-2.1-pro` | ✅ |
| `z-image-turbo` | 文生图 | `m_zimage`、`zimage`、`z-image-turbo` | ✅ |
| `qwen-image-2.0`（**i2i 默认**） | 文生图 / 图生图 | `m_c23b94d1`、`qwen-image-2.0` | ✅ |
| `Anima-2.9B-int8-turbo-merged` | 文生图 | `m_2aad393d`、`anima` | ✅ |
| krea2-two-stage / moss-image-t2i / qwen21-turbo-edit | 图/编辑 | — | ❌ 本地 ComfyUI 停用 |
| doubao-seedream 4.0 / 4.5 / 5.0 | 文生图 / 图生图 | — | ❌ 火山图片停用 |
| doubao-seedance 2.5 / 1.0-pro | 文生视频 | — | ❌ 火山视频停用 |
| minimax-h3-t2v-local | 文生视频 | — | ❌ 本地 ComfyUI 停用 |

**⚠️ 视频目前没有可用模型**：唯一的两个 t2v（H3、seedance）都随供应商停用，
`defaults.t2v` 和 `fallback_chain.t2v` 也都是空 → 现在发 `/文生视频` 会失败。要用视频得先启用对应供应商。

**⚠️ 别名坑**：`nai45` 会被前缀匹配到 **Anima**（不是 NovelAI）。要 NovelAI 请写 `m_nai45` / `nai` / `novelai`。
同理 `qwen-image` 会落到 `qwen-image-3.0`（配置顺序第一个），想要 2.0 就写全名。

图生图备选链：`qwen-image-2.1-pro`；文生图备选链：`qwen-image-2.1-pro → z-image-turbo → nai45`。
注意 fallback 只在「选谁」时生效，**运行中失败不会自动换模型**（避免重复计费）。

---

## 七、提示词优化（LLM 改写）

全局配置（`settings.json`）：

```
optimizer.enabled          = true                       # 总开关
optimizer.llm_provider_id  = qwenox/qwen3.8-flash-next  # 用哪个模型改写
optimizer.on_error         = use_original               # 优化失败就用原提示词
optimizer.cache            = true
```

**按模型自动选改写风格**（`core/optimizer_profiles.py`，按模型名/协议匹配）：

| 族 | 匹配 | 风格 |
|---|---|---|
| tag | `nai-diffusion` / `novelai` / `anima` | Danbooru tag 串 |
| qwen-image | `qwen-image` / `qwen21` / `qwen-edit` | 中文自然语言长句 |
| z-image | `z-image` / `zimage` | 简洁中文描述 |
| krea2 | `krea` / `flux` | 英文摄影向 |
| moss | `moss` | 中文长描述 |
| seedream | `seedream` / `seededit` | 主体+行为+环境 |
| video | `seedance` / `hailuo` / `kling` / `veo` / `sora` | 强调动作与镜头 |
| generic | 其他 | 通用 |

优化结果会写进任务的 `optimized_prompt`，工作台里能看到「优化前后」。

---

## 八、提示词预设（画师串）

- 预设挂在**模型**上，分 `append` / `wrap` / `replace` 三种模式，可带 `negative_prompt` 和 `params`
- 只有模型设了 `default_preset_id` 才会自动生效；**命令和 LLM 工具都无法指定预设**（只有网页工作台能选）
- 当前**唯一**设了默认预设的是 `nai-diffusion-4-5-full` → `ps_4f7661`「肉感二次元」（append 模式），
  即每次用它生图都会自动追加 `3::very aesthetic…::` + `1.1::artist:akita_hika::`
- 不想让它生效：把该模型的 `default_preset_id` 清空即可（预设内容不会丢）

拼接顺序（优化开启时）：先按族改写 → 再按模式拼预设；预设的 `negative_prompt` 只在请求没带时补，
`params` 用 `setdefault`，你显式给的参数优先。

---

## 九、队列与结果

- 提交后立刻回：`🎨 已加入文生图队列 · 模型名 / 提示词前 30 字 / 任务 xxx，完成后直接发结果`
- 完成后由插件自己把图/视频发回原会话（`context.send_message`），**不经过 LLM**
- 失败会通知（`queue.notify_on_failure = true`）
- 启动时回收上次未完成任务（`stale`）
- 429 不在 HTTP 层重试，交给供应商队列冷却，避免拖长等待

---

## 十、网页工作台

插件注册了这些接口（挂载在 `/astrbot_plugin_miaobi/studio/<path>`）：

`state`（工作台状态）· `providers`（保存供应商）· `settings`（保存全局配置）·
`generate`（提交生成）· `job`（任务状态）· `jobs`（任务列表）·
`gallery`（画廊）· `asset`（资产操作）· `test`（连通性自检）· `models`（探测可用模型）

页面模板在 `views/studio/index.html`，从面板的插件入口进入。
**选预设、改供应商/模型参数、看画廊**，目前只有工作台能做。

---

## 十一、已知坑 / 注意事项

1. **大上下文会让 LLM 不敢调工具**：会话里失败记录太多时，模型只输出思考、正文和 `tool_calls` 都为空，
   结果被 `respond/stage.py` 当空消息链静默丢弃（群里表现为「完全没反应」）。
   → 遇到就用命令 `/文生图 …`，或清理该群会话。
2. **视频暂不可用**（见第六节）。
3. **千问图片模型有免费额度**，频繁测试会扣量。
4. **图生图必须带图**（同一条消息里发图片 + 命令）。
5. `nai45` 会错配（见第六节别名坑）。
6. LLM 工具路径**不支持**本次新增的优化开关，只能靠全局 `optimizer.enabled` 控制。

---

## 十二、本次改动记录（2026-10-30）

只改了 `main.py` 三处 + 新增两个常量，**未动优化器、服务层和任何第三方代码**：

| 位置 | 改动 |
|---|---|
| `main.py:43-45` | 新增 `_OPT_OFF_WORDS` / `_OPT_ON_WORDS` 常量 |
| `_parse_command()` | 摘出开关词，返回字段增加 `optimize`（未指定 = `None`） |
| `_handle_command()` | `submit()` 请求增加 `"optimize": parsed["optimize"]` |

备份：NAS 上 `main.py.bak_20261030_0945`（旧 md5 `89a561fc…`，新 md5 `abdbdc6c…`）。
容器内单测覆盖 11 组命令用例，全部通过。
