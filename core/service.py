"""妙笔 · 生成服务编排。

职责链：解析模型 → 算分辨率 → 优化提示词 → 进供应商队列 → 调后端 → 落盘 → 写索引。
所有生成都在后台任务里跑，不阻塞聊天；完成后通过回调通知调用方。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from astrbot.api import logger

from .db import Database
from .http import BackendError
from .media import MediaStore
from .models import CAP_I2I, CAP_T2I, CAP_T2V, new_id
from .optimizer import PromptOptimizer
from .queue import QueueFullError, QueueManager
from .registry import ModelRef, ModelRegistry
from .sizing import resolve_duration, resolve_size

CAP_LABEL = {CAP_T2I: "文生图", CAP_I2I: "图生图", CAP_T2V: "文生视频"}


class _Fallback(Exception):
    """可以换下一个供应商的失败。"""

    def __init__(self, cause: BaseException):
        super().__init__(str(cause))
        self.cause = cause


def _should_fallback(exc: BaseException) -> bool:
    """这个失败值不值得换供应商。

    边界：请求还没真正落到上游（连接失败、限流、5xx、鉴权/额度、队列满）就换；
    请求已经发出去之后超时，上游可能已经生成并计费，换一家等于重复扣费，直接判失败。
    """
    if isinstance(exc, QueueFullError):
        return True
    status = int(getattr(exc, "status", 0) or 0)
    if status:
        if status in (401, 403, 404, 429):
            return True
        if 500 <= status < 600:
            # 504 是本插件轮询超时，任务可能已在上游跑完，不能当没发生
            return status != 504
        return False
    text = str(exc).lower()
    if any(key in text for key in ("timeout", "timed out", "超时")):
        return False
    if any(
        key in text
        for key in (
            "connect",
            "connection",
            "resolve",
            "dns",
            "unreachable",
            "refused",
            "network",
        )
    ):
        return True
    return False


class StudioService:
    def __init__(
        self,
        *,
        context,
        store,
        db: Database,
        media: MediaStore,
        queue: QueueManager,
        registry: ModelRegistry,
        settings: dict[str, Any],
        optimizer: PromptOptimizer,
    ):
        self.context = context
        self.store = store
        self.db = db
        self.media = media
        self.queue = queue
        self.registry = registry
        self.settings = settings or {}
        self.optimizer = optimizer
        self.on_finished: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self.on_enqueued: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._tasks: set[asyncio.Task] = set()

    # ---------- 配置 ----------

    def reload(
        self,
        *,
        providers: list[dict[str, Any]] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        if providers is not None:
            self.registry.reload(providers)
        if settings is not None:
            self.settings = settings
            self.optimizer.settings = settings

    # ---------- 查询 ----------

    def models(self, capability: str | None = None) -> list[dict[str, Any]]:
        """逻辑模型清单：一条 catalog = 一个模型 + 有序的供应商实现。"""
        out: list[dict[str, Any]] = []
        for entry in self.registry.catalog_entries(self.settings):
            caps = [
                c
                for c in (entry.get("capabilities") or [])
                if c in (CAP_T2I, CAP_I2I, CAP_T2V)
            ]
            if capability and capability not in caps:
                continue
            chain: list[dict[str, Any]] = []
            first_ref = None
            for binding, ref in self.registry.all_chain_refs(entry):
                if ref is None:
                    chain.append(
                        {
                            "provider_id": str(binding.get("provider_id") or ""),
                            "model_id": str(binding.get("model_id") or ""),
                            "provider_name": "",
                            "label": str(binding.get("model_id") or ""),
                            "protocol": "",
                            "available": False,
                            "reason": "供应商或模型已不存在",
                        }
                    )
                    continue
                if first_ref is None:
                    first_ref = ref
                chain.append(
                    {
                        "provider_id": ref.provider_id,
                        "model_id": ref.model_id,
                        "provider_name": ref.provider_name,
                        "label": ref.label,
                        "protocol": ref.protocol,
                        "capabilities": ref.capabilities,
                        "available": True,
                        "reason": "",
                    }
                )
            own = (
                self.optimizer.family_for(first_ref, entry)
                if first_ref is not None
                else {"label": "", "style": "text"}
            )
            brief = first_ref.brief() if first_ref is not None else {}
            # 把首个候选的尺寸/参数平铺出来：工作区沿用它渲染表单，
            # 但 key 换成 catalog id，提交时只传逻辑模型
            item = dict(brief)
            item.update(
                {
                    "key": str(entry.get("id") or ""),
                    "id": str(entry.get("id") or ""),
                    "name": str(entry.get("name") or entry.get("id") or ""),
                    "label": str(entry.get("name") or brief.get("label") or ""),
                    "family": str(entry.get("family") or ""),
                    "capabilities": caps,
                    "chain": chain,
                    "providers_ready": sum(1 for c in chain if c.get("available")),
                    "optimizer_prompt": str(entry.get("optimizer_prompt") or ""),
                    "enabled": bool(entry.get("enabled", True)),
                    "ready": bool(
                        self.registry.chain_refs(
                            entry, capability or (caps[0] if caps else CAP_T2I)
                        )
                    ),
                    "optimizer_family": str(own.get("label") or ""),
                    "optimizer_style": str(own.get("style") or "text"),
                }
            )
            out.append(item)
        return out

    def _fallback_ref(self) -> ModelRef | None:
        """只为取家族文案；catalog 里没有任何可用绑定时返回 None。"""
        refs = self.registry.refs
        return refs[0] if refs else None

    def resolve(
        self, capability: str, token: str = "", *, strict: bool = False
    ) -> tuple[dict[str, Any] | None, list[ModelRef]]:
        """strict=True 时，显式给了模型名却解析不到就抛错，不再静默回落到默认模型。"""
        token = str(token or "").strip()
        entry, refs = self.registry.resolve_target(capability, self.settings, token)
        if refs:
            return entry, refs
        if token and strict:
            raise ValueError(
                f"模型「{token}」不可用（可能已停用、已删除，或不支持"
                f"「{CAP_LABEL.get(capability, capability)}」）"
            )
        return entry, []

    # ---------- 提交 ----------

    async def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        capability = str(request.get("capability") or CAP_T2I)
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("提示词不能为空")

        entry, refs = self.resolve(capability, request.get("model") or "", strict=True)
        if not refs:
            raise RuntimeError(f"没有可用于「{CAP_LABEL.get(capability, capability)}」的模型")
        primary = refs[0]

        images = list(request.get("images") or [])
        if capability == CAP_I2I and not images:
            raise ValueError("图生图需要至少一张参考图")

        job_id = new_id("job")
        job = {
            "id": job_id,
            "capability": capability,
            "catalog_id": str((entry or {}).get("id") or ""),
            "provider_id": primary.provider_id,
            "model_key": primary.key,
            "model_label": str((entry or {}).get("name") or primary.label),
            "prompt": prompt,
            "optimized_prompt": "",
            "params": {
                "size": request.get("size") or "",
                "aspect_ratio": request.get("aspect_ratio") or "",
                "duration": request.get("duration") or 0,
                "preset_id": request.get("preset_id") or "",
                "extra": request.get("params") or {},
            },
            "state": "queued",
            "progress": {},
            "source": request.get("source") or "web",
            "umo": request.get("umo") or "",
            "created": time.time(),
        }
        self.db.create_job(job)

        if self.on_enqueued:
            try:
                await self.on_enqueued(job)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[妙笔] 入队通知失败: %s", exc)

        payload = dict(request)
        payload["images"] = images
        # 落库时要用 job_id 把资产挂回任务，submit 的入参里没有这个字段
        payload["job_id"] = job_id
        task = asyncio.create_task(self._run(job_id, entry or {}, refs, payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return self.db.get_job(job_id) or job

    async def _run(
        self,
        job_id: str,
        entry: dict[str, Any],
        refs: list[ModelRef],
        request: dict[str, Any],
    ) -> None:
        capability = str(request.get("capability") or CAP_T2I)
        started = time.time()
        self.db.update_job(job_id, state="running", started=started)
        attempts: list[dict[str, Any]] = []
        try:
            primary = refs[0]
            preset = self._find_preset(
                primary, str(request.get("preset_id") or ""), entry
            )
            params = dict(request.get("params") or {})
            if preset:
                if preset.get("negative_prompt") and not params.get("negative_prompt"):
                    params["negative_prompt"] = preset["negative_prompt"]
                for key, value in (preset.get("params") or {}).items():
                    params.setdefault(key, value)

            # 单次请求可以覆盖全局开关：工作区勾选框、命令、LLM 工具都能决定要不要优化
            flag = request.get("optimize")
            if flag is None:
                flag = bool((self.settings.get("optimizer") or {}).get("enabled", True))
            optimized, opt_meta = await self._compose_prompt(
                str(request.get("prompt") or ""),
                primary,
                preset,
                umo=str(request.get("umo") or ""),
                optimize=bool(flag),
                entry=entry,
            )
            opt_meta["requested"] = bool(flag)
            self._merge_progress(job_id, optimizer=opt_meta, attempts=attempts)
            self.db.update_job(job_id, optimized_prompt=optimized)

            # 优化只做一次：换供应商不改提示词，避免同一任务被不同模型反复改写
            ordered = self._order_by_cooldown(refs)
            last: BaseException | None = None
            for index, ref in enumerate(ordered):
                try:
                    await self._attempt(
                        job_id,
                        ref,
                        entry,
                        capability,
                        request,
                        optimized,
                        params,
                        started,
                        allow_wait=index == len(ordered) - 1,
                    )
                    return
                except _Fallback as exc:
                    last = exc.cause
                    attempts.append(
                        {
                            "provider": ref.provider_name,
                            "model": ref.label,
                            "reason": str(exc.cause)[:200],
                        }
                    )
                    logger.warning(
                        "[妙笔] %s 不可用（%s），切换回退路径第 %d/%d 个",
                        ref.provider_name,
                        str(exc.cause)[:120],
                        index + 2,
                        len(ordered),
                    )
                    self._merge_progress(job_id, attempts=attempts)
            raise last or RuntimeError("所有供应商都不可用")
        except QueueFullError as exc:
            self._fail(job_id, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("[妙笔] 任务失败 %s", job_id)
            self._fail(job_id, f"{type(exc).__name__}: {exc}")
        finally:
            job = self.db.get_job(job_id)
            if job and self.on_finished:
                try:
                    await self.on_finished(job)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[妙笔] 完成通知失败: %s", exc)

    async def shutdown(self) -> None:
        """取消所有在跑的任务；插件卸载时调用。"""
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    def _merge_progress(self, job_id: str, **extra: Any) -> None:
        """progress 是多步共写的，永远合并、不覆盖。"""
        current = self.db.get_job(job_id) or {}
        progress = dict(current.get("progress") or {})
        progress.update(extra)
        self.db.update_job(job_id, progress=progress)

    def _order_by_cooldown(self, refs: list[ModelRef]) -> list[ModelRef]:
        """冷却中的供应商排到最后：优先换一家，全都冷却才等最短的那个。"""
        ready: list[ModelRef] = []
        cooling: list[tuple[float, ModelRef]] = []
        for ref in refs:
            wait = self.queue.queue_for(ref.provider).remaining()
            if wait <= 0:
                ready.append(ref)
            else:
                cooling.append((wait, ref))
        cooling.sort(key=lambda item: item[0])
        return ready + [ref for _wait, ref in cooling]

    async def _attempt(
        self,
        job_id: str,
        ref: ModelRef,
        entry: dict[str, Any],
        capability: str,
        request: dict[str, Any],
        optimized: str,
        params: dict[str, Any],
        started: float,
        *,
        allow_wait: bool,
    ) -> None:
        """单个供应商的一次完整尝试；值得换家的失败抛 _Fallback。"""
        backend = None
        try:
            size_result = resolve_size(
                self._size_request(request, ref),
                model_limits=ref.model.get("limits"),
                protocol_limits=ref.backend_cls.protocol_limits,
                global_limits=self.settings.get("limits"),
            )
            duration = int(request.get("duration") or 0)
            if capability == CAP_T2V:
                duration, duration_notes = resolve_duration(
                    duration or (ref.model.get("defaults") or {}).get("duration") or 5,
                    model_limits=ref.model.get("limits"),
                )
                if duration_notes:
                    logger.info("[妙笔] 时长调整: %s", "；".join(duration_notes))

            backend = ref.create(media=self.media, settings=self.settings)
            # 参数白名单按协议裁：每个候选的协议可能不同，必须逐个裁
            send_params, ignored_params = backend.filter_params(dict(params))
            if ignored_params:
                logger.info(
                    "[妙笔] 协议 %s 不支持参数，已忽略: %s",
                    backend.protocol,
                    ", ".join(ignored_params),
                )

            queue = self.queue.queue_for(ref.provider)
            wait = queue.remaining()
            if wait > 0 and not allow_wait:
                raise _Fallback(
                    RuntimeError(f"{ref.provider_name} 冷却中（{wait:.0f}s）")
                )

            self._merge_progress(
                job_id,
                size=size_result.size,
                size_notes=size_result.notes,
                provider=ref.provider_name,
                model=ref.label,
                ignored_params=ignored_params or None,
            )

            async def _call():
                if capability == CAP_T2V:
                    return await backend.generate_video(
                        optimized,
                        size=size_result.size,
                        aspect_ratio=str(request.get("aspect_ratio") or ""),
                        duration=duration,
                        images=request.get("images") or None,
                        params=send_params,
                    )
                return await backend.generate_image(
                    optimized,
                    size=size_result.size,
                    aspect_ratio=str(request.get("aspect_ratio") or ""),
                    images=request.get("images") or None,
                    params=send_params,
                )

            def _on_wait(wait_seconds: float, position: int) -> None:
                # 合并而不是覆盖：size / optimizer 是上一步写进去的，不能被清掉
                self._merge_progress(
                    job_id,
                    cooldown_remaining=round(wait_seconds, 1),
                    queue_position=position,
                )
                self.db.update_job(job_id, queue_position=position)

            raw = await queue.submit(_call, on_wait=_on_wait)
            items = await self._persist(
                raw,
                capability,
                ref,
                request,
                optimized,
                size_result.size,
                duration if capability == CAP_T2V else 0,
                started=started,
                size_notes=size_result.notes,
            )

            elapsed = time.time() - started
            self._merge_progress(
                job_id,
                elapsed=round(elapsed, 2),
                assets=[item["id"] for item in items],
                provider=ref.provider_name,
                model=ref.label,
            )
            self.db.update_job(
                job_id,
                state="completed",
                finished=time.time(),
                error="",
                catalog_id=str(entry.get("id") or ""),
                provider_id=ref.provider_id,
                model_key=ref.key,
                model_label=str(entry.get("name") or ref.label),
            )
        except _Fallback:
            raise
        except Exception as exc:  # noqa: BLE001
            if _should_fallback(exc):
                raise _Fallback(exc) from exc
            raise
        finally:
            # 连接池必须在这里释放：后端实例是每个任务新建的
            if backend is not None:
                await backend.aclose()

    def _fail(self, job_id: str, message: str) -> None:
        self.db.update_job(
            job_id, state="failed", finished=time.time(), error=message[:800]
        )

    def _find_preset(
        self,
        ref: ModelRef,
        preset_id: str,
        entry: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """画师串预设只对 tag 类模型（NovelAI / Anima）生效。

        其他模型的提示词交给 LLM 优化就够了：画师名、权重语法这类知识
        靠预设硬拼反而会干扰模型，也会把简单需求变复杂。
        """
        family = self.optimizer.family_for(ref, entry)
        if str(family.get("style") or "") != "tag":
            return None
        token = str(preset_id or "").strip() or str(
            ref.model.get("default_preset_id") or ""
        ).strip()
        if not token:
            return None
        for item in ref.model.get("prompt_presets") or []:
            if str(item.get("id")) == token:
                return item
        return None

    @staticmethod
    def _apply_preset_only(
        prompt: str,
        preset: dict[str, Any] | None,
        family: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """不走 LLM，只按预设组装提示词。"""
        meta: dict[str, Any] = {
            "enabled": False,
            "used": False,
            "skipped": "本次未启用提示词优化",
            "family": str(family.get("label") or ""),
            "style": str(family.get("style") or "text"),
        }
        if not preset:
            return prompt, meta

        mode = str(preset.get("mode") or "append")
        name = str(preset.get("name") or "")
        body = str(preset.get("prompt") or "")
        if mode == "append":
            text = (
                f"{prompt}, {body}"
                if (prompt and body.strip())
                else (prompt or body)
            )
        elif mode == "wrap":
            if body.strip():
                text = (
                    body.replace("{prompt}", prompt)
                    if "{prompt}" in body
                    else f"{prompt}, {body}"
                )
            else:
                text = prompt
        else:
            text = body or prompt
        meta.update({"preset": name, "preset_mode": mode})
        return text, meta

    async def _compose_prompt(
        self,
        prompt: str,
        ref: ModelRef,
        preset: dict[str, Any] | None,
        *,
        umo: str = "",
        optimize: bool = True,
        entry: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """决定「先优化还是先拼接」——顺序不同，结果差很多。

        append   把用户输入单独交给优化模型（tag 家族会转成 Danbooru 标签），
                 回来再拼预设的画师串。这样模型不会去改写画师名。
        wrap     先把 {prompt} 换成用户输入，整体交给优化模型按结构生成。
        replace  直接拿预设内容去优化。
        没有预设    就和以前一样，整句优化。
        optimize=False 时完全跳过 LLM，只按预设组装。

        非 tag 模型的 preset 一律为 None（见 _find_preset），所以后三条分支
        实际只会被 NovelAI / Anima 走到。
        """
        family = self.optimizer.family_for(ref, entry)
        style = str(family.get("style") or "text")
        if not optimize:
            return self._apply_preset_only(prompt, preset, family)
        if not preset:
            return await self.optimizer.optimize(
                prompt, ref, umo=umo, style=style, entry=entry
            )

        mode = str(preset.get("mode") or "append")
        name = str(preset.get("name") or "")
        body = str(preset.get("prompt") or "")

        if mode == "append":
            optimized, meta = await self.optimizer.optimize(
                prompt, ref, umo=umo, style=style, entry=entry
            )
            # 只填了负面词的预设没有正文，别拼出 "xxx, " 这种尾巴
            joined = (
                f"{optimized}, {body}" if (optimized and body.strip()) else (optimized or body)
            )
            meta.update({"preset": name, "preset_mode": "append"})
            return joined, meta

        if mode == "wrap":
            if body.strip():
                filled = (
                    body.replace("{prompt}", prompt)
                    if "{prompt}" in body
                    else f"{prompt}, {body}"
                )
            else:
                filled = prompt
            optimized, meta = await self.optimizer.optimize(
                filled, ref, umo=umo, style=style, entry=entry
            )
            meta.update({"preset": name, "preset_mode": "wrap"})
            return optimized, meta

        optimized, meta = await self.optimizer.optimize(
            body or prompt, ref, umo=umo, style=style, entry=entry
        )
        meta.update({"preset": name, "preset_mode": "replace"})
        return optimized, meta

    @staticmethod
    def _size_request(request: dict[str, Any], ref: ModelRef) -> str:
        explicit = str(request.get("size") or "").strip()
        if explicit:
            return explicit
        ratio = str(request.get("aspect_ratio") or "").strip()
        defaults = ref.model.get("defaults") or {}
        if ratio:
            by_ratio = (defaults.get("ratio_sizes") or {}).get(ratio)
            if by_ratio:
                return str(by_ratio)
            # 纯比例交给 sizing 用标准预置档，避免按默认尺寸面积算出非标值
            return ratio
        return str(defaults.get("size") or defaults.get("aspect_ratio") or "1:1")

    async def _persist(
        self,
        raw: dict[str, Any],
        capability: str,
        ref: ModelRef,
        request: dict[str, Any],
        prompt: str,
        size: str,
        duration: int,
        started: float = 0.0,
        size_notes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        items = raw.get("items") or []
        saved: list[dict[str, Any]] = []
        for item in items:
            kind = item.get("kind") or "image"
            data = item.get("data")
            mime = item.get("mime") or ""
            if not data and item.get("url"):
                data, mime = await self.media.download(
                    str(item["url"]),
                    proxy=(ref.provider.get("proxy") or None),
                    timeout=int(ref.provider.get("timeout") or 300),
                )
            if not data:
                continue
            meta = {
                "job_id": request.get("job_id") or "",
                "capability": capability,
                "provider": ref.provider_name,
                "provider_id": ref.provider_id,
                "model": ref.label,
                "model_id": ref.model_id,
                "prompt": request.get("prompt") or "",
                "optimized_prompt": prompt,
                "size_requested": request.get("size") or "",
                "size": size,
                "aspect_ratio": request.get("aspect_ratio") or "",
                "duration": duration,
                "params": request.get("params") or {},
                "seed": item.get("seed"),
                "source": request.get("source") or "web",
                "created": int(time.time()),
            }
            info = self.media.save(
                data, kind=kind, mime=mime, prefix=ref.protocol, meta=meta
            )
            asset = {
                "id": new_id("a"),
                "job_id": request.get("job_id") or "",
                "kind": kind,
                "capability": capability,
                "path": info["path"],
                "thumb_path": info.get("thumb_path") or "",
                "mime": info.get("mime") or "",
                "width": info.get("width") or 0,
                "height": info.get("height") or 0,
                "bytes": info.get("bytes") or 0,
                "provider_id": ref.provider_id,
                "provider_name": ref.provider_name,
                "model_key": ref.key,
                "model_id": ref.model_id,
                "prompt": request.get("prompt") or "",
                "optimized_prompt": prompt,
                "negative_prompt": str((request.get("params") or {}).get("negative_prompt") or ""),
                "params": {
                    **(request.get("params") or {}),
                    "size": size,
                    "aspect_ratio": request.get("aspect_ratio") or "",
                    "duration": duration,
                    "seed": item.get("seed"),
                    "size_notes": list(size_notes or []),
                },
                "seed": str(item.get("seed") or ""),
                "duration": duration,
                "elapsed": round(time.time() - (started or time.time()), 2),
                "created": time.time(),
            }
            self.db.add_asset(asset)
            saved.append(asset)
        if not saved:
            raise RuntimeError("上游没有返回任何可保存的媒体文件")
        return saved

    # ---------- 画廊 ----------

    def gallery(
        self,
        *,
        limit: int = 40,
        offset: int = 0,
        kind: str = "",
        query: str = "",
        favorite_only: bool = False,
    ) -> dict[str, Any]:
        items, total = self.db.list_assets(
            limit=limit, offset=offset, kind=kind, query=query, favorite_only=favorite_only
        )
        return {"items": items, "total": total, "limit": limit, "offset": offset}
