"""妙笔 · 文生图 / 图生图 / 文生视频统一工作台。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain, Video
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.web import request as web_request

try:
    # 主动发送必须传 MessageChain，直接给 list 会在平台层报
    # "'list' object has no attribute 'chain'"
    from astrbot.core.message.message_event_result import MessageChain
except Exception:  # noqa: BLE001
    MessageChain = None  # type: ignore[assignment]

from .core.backends import backend_class, protocol_meta
from .core.catalog_presets import preset_catalog
from .core.db import Database
from .core.media import MediaStore, decode_data_url
from .core.models import CAP_I2I, CAP_T2I, CAP_T2V, normalize_provider
from .core.optimizer import PromptOptimizer
from .core.presets import platform_presets
from .core.queue import QueueManager
from .core.registry import ModelRegistry
from .core.service import CAP_LABEL, StudioService
from .core.sizing import preset_choices
from .core.store import ConfigStore

PLUGIN_NAME = "astrbot_plugin_miaobi"
PLUGIN_LABEL = "妙笔"

_RATIO_RE = re.compile(r"^\d{1,3}(?:\.\d+)?\s*[:/]\s*\d{1,3}(?:\.\d+)?$")
_SIZE_RE = re.compile(r"^\d{2,5}\s*[x*×]\s*\d{2,5}$", re.IGNORECASE)
_SIZE_MP_RE = re.compile(r"^\d{1,3}(?:\.\d+)?\s*[:/]\s*\d{1,3}(?:\.\d+)?@\d", re.IGNORECASE)
_DURATION_RE = re.compile(r"^(\d{1,3})\s*[sS秒]?$")

# 命令里的提示词优化开关：命中后从提示词中摘除，只取第一个
_OPT_OFF_WORDS = ("--no-opt", "-noopt", "no-opt", "不优化", "原样", "不优化提示词")
_OPT_ON_WORDS = ("--opt", "-opt", "opt", "优化", "优化提示词")


@register(PLUGIN_NAME, "logo", "妙笔 · 文生图/图生图/文生视频统一工作台", "v0.1.0")
class MiaobiPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.enabled = bool(self.config.get("enable", True))
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.store = ConfigStore(self.data_dir / "config")
        self.db = Database(self.data_dir / "miaobi.db")
        self.media = MediaStore(self.data_dir / "assets")
        self.queue = QueueManager(self.data_dir / "queue")

        self.providers: list[dict[str, Any]] = self.store.load_providers()
        self.settings: dict[str, Any] = self.store.load_settings(self.providers)
        self.registry = ModelRegistry(self.providers)
        self.optimizer = PromptOptimizer(self.context, self.settings)
        self.service = StudioService(
            context=self.context,
            store=self.store,
            db=self.db,
            media=self.media,
            queue=self.queue,
            registry=self.registry,
            settings=self.settings,
            optimizer=self.optimizer,
        )
        self.service.on_finished = self._on_job_finished

    async def initialize(self) -> None:
        stale = self.db.fail_stale_jobs()
        if stale:
            logger.info("[妙笔] 已回收 %d 个上次未完成的任务", stale)
        routes = [
            ("state", self.api_state, ["GET"], "工作台状态"),
            ("providers", self.api_save_providers, ["POST"], "保存供应商"),
            ("settings", self.api_save_settings, ["POST"], "保存全局配置"),
            ("generate", self.api_generate, ["POST"], "提交生成"),
            ("job", self.api_job, ["GET"], "任务状态"),
            ("jobs", self.api_jobs, ["GET"], "任务列表"),
            ("gallery", self.api_gallery, ["GET"], "画廊列表"),
            ("asset", self.api_asset, ["POST"], "资产操作"),
            ("test", self.api_test, ["POST"], "连通性自检"),
            ("models", self.api_models, ["POST"], "探测可用模型"),
        ]
        for path, handler, methods, desc in routes:
            try:
                self.context.register_web_api(
                    f"/{PLUGIN_NAME}/studio/{path}", handler, methods, desc
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[妙笔] 注册接口 %s 失败: %s", path, exc)
        logger.info(
            "[妙笔] 已加载 %d 个供应商、%d 个模型%s",
            len(self.providers),
            len(self.registry.refs),
            "" if self.enabled else "（配置里已停用，命令与工具不响应）",
        )
        if (self.settings.get("optimizer") or {}).get("enabled", True):
            picked = str(
                (self.settings.get("optimizer") or {}).get("llm_provider_id") or ""
            )
            logger.info("[妙笔] 提示词优化使用: %s", picked or "跟随会话默认")
            # Provider 在插件之后才实例化，这里取列表必然是空的，延后探测一次
            asyncio.create_task(self._probe_llm_providers())

    async def _probe_llm_providers(self) -> None:
        """启动后探一次 LLM 供应商，确认提示词优化有模型可用。"""
        for _ in range(6):
            await asyncio.sleep(5)
            try:
                items = self.optimizer.available_providers()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[妙笔] 探测 LLM 供应商失败: %s", exc)
                continue
            if items:
                logger.info("[妙笔] 提示词优化可用 LLM 供应商 %d 个", len(items))
                return
        logger.warning("[妙笔] 未发现可用 LLM 供应商，提示词优化会跳过")

    # ================= 命令 =================
    @filter.command("文生图")
    async def cmd_t2i(self, event: AstrMessageEvent):
        async for item in self._handle_command(event, CAP_T2I, "文生图"):
            yield item

    @filter.command("图生图")
    async def cmd_i2i(self, event: AstrMessageEvent):
        async for item in self._handle_command(event, CAP_I2I, "图生图"):
            yield item

    @filter.command("文生视频")
    async def cmd_t2v(self, event: AstrMessageEvent):
        async for item in self._handle_command(event, CAP_T2V, "文生视频"):
            yield item

    async def _handle_command(self, event: AstrMessageEvent, capability: str, keyword: str):
        if not self.enabled:
            return
        try:
            parsed = self._parse_command(event.message_str or "", keyword, capability)
            images = await self._event_images(event) if capability == CAP_I2I else []
            job = await self.service.submit(
                {
                    "capability": capability,
                    "model": parsed["model"],
                    "prompt": parsed["prompt"],
                    "size": parsed["size"],
                    "aspect_ratio": "",
                    "duration": parsed["duration"],
                    "images": images,
                    "source": "command",
                    "optimize": parsed["optimize"],
                    "umo": event.unified_msg_origin,
                }
            )
            yield event.plain_result(self._enqueue_text(job, parsed["prompt"]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[妙笔] 命令失败: %s", exc)
            yield event.plain_result(f"❌ {exc}")

    def _parse_command(self, text: str, keyword: str, capability: str) -> dict[str, Any]:
        body = (text or "").strip()
        for prefix in ("/", "!", "！", "。"):
            if body.startswith(prefix):
                body = body[1:]
        parts = body.split()
        if parts and parts[0] in (keyword, "妙笔"):
            parts = parts[1:]
        if parts and parts[0] == keyword:
            parts = parts[1:]

        size = ""
        duration = 0
        model = ""
        optimize = None  # None = 不指定，交给全局 optimizer.enabled 决定

        for token in list(parts):
            low = token.lower()
            if low in _OPT_OFF_WORDS:
                optimize = False
                parts.remove(token)
                break
        if optimize is None:
            for token in list(parts):
                if token.lower() in _OPT_ON_WORDS:
                    optimize = True
                    parts.remove(token)
                    break

        if capability == CAP_T2V and parts:
            match = _DURATION_RE.match(parts[-1])
            if match:
                duration = int(match.group(1))
                parts.pop()

        if parts:
            tail = parts[-1]
            if _RATIO_RE.match(tail) or _SIZE_RE.match(tail) or _SIZE_MP_RE.match(tail):
                size = parts.pop()

        if parts:
            # 支持逻辑模型 id（flux-dev）与物理模型名（nai45）两种写法
            _entry, hits = self.registry.resolve_target(
                capability, self.settings, parts[0]
            )
            if hits:
                model = parts.pop(0)

        prompt = " ".join(parts).strip()
        if not prompt:
            raise ValueError("缺少提示词")
        return {
            "model": model,
            "prompt": prompt,
            "size": size,
            "duration": duration,
            "optimize": optimize,
        }

    async def _event_images(self, event: AstrMessageEvent) -> list[bytes]:
        out: list[bytes] = []
        try:
            chain = event.message_obj.message
        except Exception:  # noqa: BLE001
            return out
        for comp in chain or []:
            if not isinstance(comp, Image):
                continue
            data = None
            path = getattr(comp, "path", "") or ""
            if path and Path(path).exists():
                data = Path(path).read_bytes()
            else:
                url = getattr(comp, "url", "") or ""
                if url.startswith("http"):
                    data, _ = await self.media.download(url, timeout=60)
                else:
                    try:
                        data = await comp.get_bytes()  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        data = None
            if data:
                out.append(data)
        return out

    def _enqueue_text(self, job: dict[str, Any], prompt: str) -> str:
        label = CAP_LABEL.get(str(job.get("capability")), "生成")
        short = prompt if len(prompt) <= 30 else prompt[:30] + "…"
        return (
            f"🎨 已加入{label}队列 · {job.get('model_label')}\n"
            f"　 {short}\n"
            f"　 任务 {job.get('id')}，完成后直接发结果"
        )

    # ================= LLM 工具 =================

    @filter.llm_tool(name="image_generate")
    async def tool_image_generate(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        model: str = "",
        aspect_ratio: str = "",
    ):
        """生成图片（文生图）。

        提交后立即返回，出图会单独推送，不要重复提交。

        Args:
            prompt(string): 画面描述，必填，写清主体、风格、构图。
            model(string): 模型名，可留空使用默认模型。
            aspect_ratio(string): 长宽比，例如 3:4、16:9。
        """
        if not self.enabled:
            return "妙笔已在配置里停用，暂时不能生成图片。"
        job = await self.service.submit(
            {
                "capability": CAP_T2I,
                "model": model,
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "source": "llm",
                "umo": event.unified_msg_origin,
            }
        )
        return f"已提交文生图任务 {job['id']}（{job['model_label']}），完成后会直接发送图片。"

    @filter.llm_tool(name="image_edit")
    async def tool_image_edit(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        model: str = "",
        aspect_ratio: str = "",
    ):
        """基于用户发来的图片做图生图或改图。

        提交后立即返回，结果会单独推送。

        Args:
            prompt(string): 修改要求，必填。
            model(string): 模型名，可留空使用默认模型。
            aspect_ratio(string): 长宽比，例如 3:4、16:9。
        """
        if not self.enabled:
            return "妙笔已在配置里停用，暂时不能改图。"
        images = await self._event_images(event)
        if not images:
            return "用户这条消息里没有图片，无法做图生图。"
        job = await self.service.submit(
            {
                "capability": CAP_I2I,
                "model": model,
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "images": images,
                "source": "llm",
                "umo": event.unified_msg_origin,
            }
        )
        return f"已提交图生图任务 {job['id']}（{job['model_label']}），完成后会直接发送结果。"

    @filter.llm_tool(name="video_generate")
    async def tool_video_generate(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        model: str = "",
        aspect_ratio: str = "",
        duration: int = 0,
    ):
        """生成视频（文生视频）。

        提交后立即返回，不要等待，结果会单独推送。

        Args:
            prompt(string): 画面描述，必填，写清主体、动作、镜头。
            model(string): 模型名，可留空使用默认模型。
            aspect_ratio(string): 长宽比，例如 16:9、9:16。
            duration(number): 时长秒数，例如 5 或 8。
        """
        if not self.enabled:
            return "妙笔已在配置里停用，暂时不能生成视频。"
        job = await self.service.submit(
            {
                "capability": CAP_T2V,
                "model": model,
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "duration": duration,
                "source": "llm",
                "umo": event.unified_msg_origin,
            }
        )
        return f"已提交文生视频任务 {job['id']}（{job['model_label']}），完成后会直接发送视频。"

    # ================= 完成通知 =================

    async def _send_chain(self, umo: str, components: list[Any]) -> bool:
        """主动发消息。AstrBot 要求 MessageChain，传 list 会在平台层抛 chain 属性错误。"""
        if not components:
            return False
        message_chain = MessageChain(components) if MessageChain else components
        await self.context.send_message(umo, message_chain)
        return True

    async def _on_job_finished(self, job: dict[str, Any]) -> None:
        umo = str(job.get("umo") or "")
        if not umo:
            return
        state = str(job.get("state") or "")
        queue_cfg = (self.settings.get("queue") or {})
        try:
            if state == "failed":
                if not queue_cfg.get("notify_on_failure", True):
                    return
                await self._send_chain(
                    umo,
                    [
                        Plain(
                            f"❌ {CAP_LABEL.get(job.get('capability'), '生成')}失败"
                            f"（{job.get('model_label')}）\n{str(job.get('error') or '')[:300]}"
                        )
                    ],
                )
                return
            if state != "completed":
                return
            assets = self._job_assets(job)
            if not assets:
                return
            chain: list[Any] = [
                Plain(
                    f"✅ {CAP_LABEL.get(job.get('capability'), '生成')}完成"
                    f" · {job.get('model_label')}"
                )
            ]
            for asset in assets[:4]:
                path = str(asset.get("path") or "")
                if not path or not Path(path).exists():
                    continue
                if asset.get("kind") == "video":
                    chain.append(Video(file=path))
                else:
                    chain.append(Image.fromFileSystem(path))
            if len(chain) == 1:
                # 文件都在但读不出来时不要发一条光秃秃的「完成」
                logger.warning("[妙笔] 任务 %s 完成但资产文件不可读", job.get("id"))
                return
            await self._send_chain(umo, chain)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[妙笔] 推送结果失败: %s", exc)

    def _job_assets(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        ids = (job.get("progress") or {}).get("assets") or []
        out = []
        for asset_id in ids:
            asset = self.db.get_asset(str(asset_id))
            if asset:
                out.append(asset)
        return out

    # ================= Web API =================

    def _q(self, name: str, default: Any = "") -> Any:
        try:
            query = getattr(web_request, "query", None)
            if query is not None and name in query:
                return query.get(name)
        except Exception:  # noqa: BLE001
            pass
        return default

    async def api_state(self) -> dict:
        snapshot = self.queue.snapshots()
        return {
            "status": "ok",
            "plugin": PLUGIN_NAME,
            "label": PLUGIN_LABEL,
            "providers": self.providers,
            "settings": self.settings,
            "models": self.service.models(),
            "catalog_presets": preset_catalog(self.providers),
            "protocols": protocol_meta(),
            "platforms": platform_presets(),
            "presets": preset_choices(),
            "queue": snapshot,
            "stats": self.db.stats(),
            "recent_jobs": self.db.list_jobs(limit=8),
            "llm_providers": self.optimizer.available_providers(),
            "optimizer_families": self.optimizer.family_options(),
        }

    async def api_save_providers(self) -> dict:
        payload = await web_request.json(default={}) or {}
        items = payload.get("providers")
        if not isinstance(items, list):
            return {"status": "error", "message": "providers 必须是数组"}
        try:
            providers = [normalize_provider(item) for item in items if isinstance(item, dict)]
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"配置非法: {exc}"}
        self.store.save_providers(providers)
        self.providers = self.store.load_providers()
        self.service.reload(providers=self.providers)
        return {
            "status": "ok",
            "providers": self.providers,
            "models": self.service.models(),
        }

    async def api_save_settings(self) -> dict:
        payload = await web_request.json(default={}) or {}
        settings = payload.get("settings")
        if not isinstance(settings, dict):
            return {"status": "error", "message": "settings 必须是对象"}
        self.store.save_settings(settings)
        self.settings = self.store.load_settings(self.providers)
        self.service.reload(settings=self.settings)
        return {"status": "ok", "settings": self.settings}

    async def api_generate(self) -> dict:
        if not self.enabled:
            return {"status": "error", "message": "妙笔已在插件配置里停用，开启后才能生成"}
        payload = await web_request.json(default={}) or {}
        capability = str(payload.get("capability") or CAP_T2I)
        # 参考图并发下载：串行 4 张最坏要等 4 个 60s 超时
        urls = [
            str(url).strip()
            for url in (payload.get("image_urls") or [])
            if str(url).strip()
        ][:4]
        results = await asyncio.gather(*(self._fetch_image(url) for url in urls))
        images = [item for item in results if item]
        try:
            job = await self.service.submit(
                {
                    "capability": capability,
                    "model": payload.get("model") or "",
                    "prompt": payload.get("prompt") or "",
                    "preset_id": payload.get("preset_id") or "",
                    "size": payload.get("size") or "",
                    "aspect_ratio": payload.get("aspect_ratio") or "",
                    "duration": payload.get("duration") or 0,
                    "params": payload.get("params") or {},
                    "images": images,
                    "source": "web",
                    # 工作区勾选框：显式传布尔值，缺省才跟随全局配置
                    "optimize": payload.get("optimize"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": str(exc)}
        return {"status": "ok", "job": job}

    async def _fetch_image(self, url: str) -> bytes | None:
        """取参考图。支持 http(s) 下载与 data URL（页面上传的图片）。"""
        try:
            if url.startswith("data:"):
                data, _ = decode_data_url(url)
                return data or None
            data, _ = await self.media.download(url, timeout=60)
            return data
        except Exception as exc:  # noqa: BLE001
            logger.debug("[妙笔] 参考图读取失败 %s: %s", url[:80], exc)
            return None

    async def api_job(self) -> dict:
        job_id = str(self._q("id", "") or "")
        if not job_id:
            return {"status": "error", "message": "缺少 id"}
        job = self.db.get_job(job_id)
        if not job:
            return {"status": "error", "message": "任务不存在"}
        return {"status": "ok", "job": job, "assets": self._job_assets(job)}

    async def api_jobs(self) -> dict:
        """任务列表。前端刷新或离开页面回来后靠它恢复进度。"""
        try:
            limit = int(self._q("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
        raw_states = str(self._q("states", "") or "")
        states = [item.strip() for item in raw_states.split(",") if item.strip()]
        jobs = self.db.list_jobs(
            limit=max(1, min(limit, 60)), states=states or None
        )
        active = [job for job in jobs if job.get("state") in ("queued", "running")]
        return {
            "status": "ok",
            "jobs": jobs,
            "active": [str(job.get("id")) for job in active],
            "queue": self.queue.snapshots(),
        }

    async def api_gallery(self) -> dict:
        limit = int(self._q("limit", 40) or 40)
        offset = int(self._q("offset", 0) or 0)
        return {
            "status": "ok",
            **self.service.gallery(
                limit=max(1, min(limit, 120)),
                offset=max(0, offset),
                kind=str(self._q("kind", "") or ""),
                query=str(self._q("q", "") or ""),
                favorite_only=str(self._q("fav", "")) in ("1", "true"),
            ),
        }

    async def api_asset(self) -> dict:
        payload = await web_request.json(default={}) or {}
        action = str(payload.get("action") or "")
        asset_id = str(payload.get("id") or "")
        asset = self.db.get_asset(asset_id)
        if not asset:
            return {"status": "error", "message": "资源不存在"}
        if action == "delete":
            self.media.remove(str(asset.get("path") or ""))
            self.db.delete_asset(asset_id)
            return {"status": "ok"}
        if action == "favorite":
            self.db.toggle_favorite(asset_id, bool(payload.get("value")))
            return {"status": "ok"}
        if action == "meta":
            return {"status": "ok", "asset": asset}
        if action == "b64":
            # 面板取不到插件目录下的文件，只能把字节转成 data URL 递过去
            from base64 import b64encode

            from .core.media import sniff_mime

            want_thumb = bool(payload.get("thumb"))
            raw_path = str(
                (asset.get("thumb_path") if want_thumb else "") or asset.get("path") or ""
            )
            target = Path(raw_path)
            if not target.is_file():
                return {"status": "error", "message": "文件不存在"}
            data = target.read_bytes()
            mime = sniff_mime(target)
            return {
                "status": "ok",
                "mime": mime,
                "size": len(data),
                "data_url": f"data:{mime};base64," + b64encode(data).decode("ascii"),
            }
        return {"status": "error", "message": f"未知操作: {action}"}

    async def api_test(self) -> dict:
        payload = await web_request.json(default={}) or {}
        model_key = str(payload.get("model") or "")
        ref = self.registry.get(model_key) or self.registry.resolve(model_key)
        if ref is None:
            # 停用的供应商/模型不会进注册表，这里回查原始配置给出准确原因
            for provider in self.providers:
                for model in provider.get("models") or []:
                    if model.get("id") != model_key:
                        continue
                    if not provider.get("enabled", True):
                        return {
                            "status": "error",
                            "message": f"供应商「{provider.get('name')}」已停用，启用后才能测试",
                        }
                    return {
                        "status": "error",
                        "message": f"模型「{model.get('display_name') or model.get('model_id')}」已停用",
                    }
            return {"status": "error", "message": "模型不存在"}
        try:
            backend = ref.create(media=self.media, settings=self.settings)
            result = await backend.test()
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
        return {"status": "ok", "result": result}

    async def api_models(self) -> dict:
        """只读探测上游可用模型，不消耗生成额度。"""
        payload = await web_request.json(default={}) or {}
        provider_id = str(payload.get("provider_id") or "")
        provider = next(
            (p for p in self.providers if str(p.get("id")) == provider_id), None
        )
        if provider is None:
            return {"status": "error", "message": "供应商不存在，请先保存配置再检测"}
        try:
            cls = backend_class(str(provider.get("protocol") or ""))
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": str(exc)}
        probe = {
            "id": "probe",
            "model_id": str(payload.get("model_id") or ""),
            "capabilities": list(cls.capabilities),
        }
        try:
            backend = cls(provider, probe, media=self.media, settings=self.settings)
            result = await backend.list_models()
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
        result["capabilities"] = list(cls.capabilities)
        if not result.get("ok"):
            return {
                "status": "error",
                "message": str(result.get("message") or "探测失败"),
                "result": result,
            }
        return {"status": "ok", "result": result}

    async def terminate(self) -> None:
        for task in list(self.service._tasks):
            task.cancel()
        logger.info("[妙笔] 已卸载")
