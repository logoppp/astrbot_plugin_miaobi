"""妙笔 · 提示词优化。

按「生图模型 → 优化档案」的映射，把用户提示词交给 AstrBot 里已配置的 LLM provider 改写，
再拿去生成。任何失败都降级用原提示词，绝不阻塞出图。
"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger

from .optimizer_profiles import TAG, family_list, get_family, match_family
from .registry import ModelRef, norm_alias

# 兼容旧引用：没有专属档案时用通用规则
DEFAULT_SYSTEM_PROMPT = family_list()[-1]["system_prompt"]

# NovelAI 这类吃 Danbooru tag 的模型：把自然语言转成 tag，
# 画师串与质量词由预设追加，所以这里明确禁止模型自己加。
TAG_SYSTEM_PROMPT = TAG


class PromptOptimizer:
    def __init__(self, context, settings: dict[str, Any]):
        self.context = context
        self.settings = settings or {}
        self._cache: dict[tuple[str, str], str] = {}

    @property
    def config(self) -> dict[str, Any]:
        return self.settings.get("optimizer") or {}

    def profile_for(self, ref: ModelRef) -> dict[str, Any] | None:
        cfg = self.config
        mapping = cfg.get("map") or {}
        profiles = {str(p.get("id")): p for p in (cfg.get("profiles") or [])}
        keys = [
            ref.key,
            ref.model_id,
            ref.label,
            ref.qualified,
            ref.provider_name,
        ]
        for key in keys:
            profile_id = mapping.get(key)
            if profile_id and profile_id in profiles:
                return profiles[profile_id]
        # 归一化后再试一轮
        needle = {norm_alias(k) for k in keys}
        for raw_key, profile_id in mapping.items():
            if norm_alias(raw_key) in needle and profile_id in profiles:
                return profiles[profile_id]
        return None

    def family_for(
        self, ref: ModelRef, entry: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """模型所属的优化家族。

        优先级：catalog 的模型专属提示词 > catalog 显式指定的 family > 家族级覆盖 > 内置文案。
        """
        entry = entry or {}
        family_key = str(entry.get("family") or "").strip()
        preset = get_family(family_key) if family_key else None
        base = dict(preset or match_family(ref.model_id, ref.protocol, ref.capabilities))
        for item in self.config.get("profiles") or []:
            if str(item.get("family") or "") != base["key"]:
                continue
            text = str(item.get("system_prompt") or "").strip()
            if not text:
                continue
            base["system_prompt"] = text
            base["style"] = str(item.get("style") or base.get("style") or "text")
            base["custom"] = True
            break
        # 模型专属提示词最具体，压过家族级
        own = str(entry.get("optimizer_prompt") or "").strip()
        if own:
            base["system_prompt"] = own
            base["custom"] = True
        return base

    def style_for(self, ref: ModelRef) -> str:
        return str(self.family_for(ref).get("style") or "text")

    def family_options(self) -> list[dict[str, Any]]:
        """配置页用：每个家族当前生效的文案，以及是否被用户改过。"""
        saved = {
            str(item.get("family") or ""): item
            for item in (self.config.get("profiles") or [])
            if str(item.get("family") or "")
        }
        out: list[dict[str, Any]] = []
        for item in family_list():
            current = dict(item)
            # 内置文案单独留着，界面上的「恢复默认」用它
            current["builtin_prompt"] = item["system_prompt"]
            override = saved.get(item["key"])
            if override and str(override.get("system_prompt") or "").strip():
                current["system_prompt"] = str(override["system_prompt"])
                current["style"] = str(override.get("style") or item["style"])
                current["custom"] = True
            else:
                current["custom"] = False
            out.append(current)
        return out

    async def optimize(
        self,
        prompt: str,
        ref: ModelRef,
        *,
        umo: str = "",
        style: str = "",
        entry: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """style 留空时按模型家族自动决定（tag / text）。entry 是 catalog 条目。"""
        cfg = self.config
        family = self.family_for(ref, entry)
        style = style or str(family.get("style") or "text")
        meta: dict[str, Any] = {
            "enabled": bool(cfg.get("enabled")),
            "used": False,
            "style": style,
            "family": str(family.get("label") or ""),
        }
        if not cfg.get("enabled"):
            return prompt, meta
        # 没有绑定档案也用内置提示词优化，否则「优化」这个开关等于摆设
        profile = self.profile_for(ref) or {}
        builtin = str(family.get("system_prompt") or DEFAULT_SYSTEM_PROMPT)
        system_prompt = str(profile.get("system_prompt") or "").strip() or builtin
        meta["profile"] = profile.get("name") or str(family.get("label") or "内置")
        meta["model"] = str((entry or {}).get("id") or "")

        # 同一句提示词在不同模型下结果不同，缓存必须按模型隔离
        cache_scope = str((entry or {}).get("id") or ref.key)
        cache_key = (cache_scope, f"{style}:{prompt}")
        if cfg.get("cache", True) and cache_key in self._cache:
            meta.update({"used": True, "cached": True})
            return self._cache[cache_key], meta

        providers = await self._providers(profile, umo)
        if not providers:
            return self._fail("没有可用的 LLM provider", meta, prompt)

        errors: list[str] = []
        for provider in providers:
            name = _provider_name(provider)
            try:
                response = await self._chat(provider, prompt, system_prompt)
            except asyncio.TimeoutError:
                logger.warning("[妙笔] 提示词优化超时（%s），尝试下一个供应商", name)
                errors.append(f"{name} 超时")
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("[妙笔] 提示词优化失败（%s）: %s", name, exc)
                errors.append(f"{name} {type(exc).__name__}")
                continue

            text = str(getattr(response, "completion_text", "") or "").strip()
            text = text.strip("`").strip()
            if len(text) >= 4:
                if cfg.get("cache", True):
                    if len(self._cache) > 256:
                        self._cache.clear()
                    self._cache[cache_key] = text
                meta.update(
                    {"used": True, "cached": False, "length": len(text), "llm": name}
                )
                return text, meta
            errors.append(f"{name} 空结果")

        return self._fail("；".join(errors[-3:]) or "优化失败", meta, prompt)

    async def _chat(self, provider, prompt: str, system_prompt: str):
        """调用 LLM。超时沿用 AstrBot 供应商自己的配置，这里只加 30 秒兜底。"""
        coro = provider.text_chat(
            prompt=prompt,
            contexts=[],
            image_urls=[],
            func_tool=None,
            system_prompt=system_prompt,
        )
        guard = _guard_timeout(provider)
        if guard > 0:
            return await asyncio.wait_for(coro, timeout=guard)
        return await coro

    def _fail(
        self, message: str, meta: dict[str, Any], prompt: str
    ) -> tuple[str, dict[str, Any]]:
        """按配置决定优化失败后的行为：默认降级用原提示词，可选直接让任务失败。"""
        meta["reason"] = message
        if str(self.config.get("on_error") or "use_original") == "fail":
            raise RuntimeError(f"提示词优化失败：{message}")
        return prompt, meta

    def available_providers(self) -> list[dict[str, str]]:
        """列出 AstrBot 里可用于文本生成的 LLM 供应商，供配置页选择。"""
        getter = getattr(self.context, "get_all_providers", None)
        if not callable(getter):
            return []
        try:
            providers = getter() or []
        except Exception as exc:  # noqa: BLE001
            logger.debug("[妙笔] 取供应商列表失败: %s", exc)
            return []
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for provider in providers:
            if not hasattr(provider, "text_chat"):
                continue
            provider_id = ""
            model = ""
            try:
                meta = provider.meta()
                provider_id = str(getattr(meta, "id", "") or "")
                model = str(getattr(meta, "model", "") or "")
            except Exception:  # noqa: BLE001
                config = getattr(provider, "provider_config", {}) or {}
                provider_id = str(config.get("id") or "")
                model = str(config.get("model") or "")
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            out.append(
                {
                    "id": provider_id,
                    "model": model,
                    "label": f"{provider_id} / {model}" if model else provider_id,
                }
            )
        return out

    async def _providers(self, profile: dict[str, Any], umo: str) -> list:
        """按「档案指定 → 全局回退路径 → 旧单值」组装候选；全都失效才跟随会话默认。"""
        chain: list[str] = []
        for item in [
            profile.get("llm_provider_id"),
            *(self.config.get("llm_providers") or []),
            self.config.get("llm_provider_id"),
        ]:
            pid = str(item or "").strip()
            if pid and pid not in chain:
                chain.append(pid)

        out: list = []
        getter = getattr(self.context, "get_provider_by_id", None)
        if chain and callable(getter):
            for pid in chain:
                try:
                    provider = getter(pid)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[妙笔] 按 id 取 provider 失败（%s）: %s", pid, exc)
                    continue
                if provider is not None and hasattr(provider, "text_chat"):
                    out.append(provider)
                else:
                    logger.warning("[妙笔] 优化用的 LLM 供应商 %s 不存在，跳过", pid)
        if out:
            return out

        # Web 端没有 umo；优先用 async 版（同步版已标记废弃），失败再退回同步
        async_getter = getattr(self.context, "get_using_provider_async", None)
        if callable(async_getter):
            for kwargs in ({"umo": umo or None}, {}):
                try:
                    candidate = await async_getter(**kwargs)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[妙笔] async 取 provider 失败: %s", exc)
                    continue
                if candidate is not None and hasattr(candidate, "text_chat"):
                    return [candidate]

        sync_getter = getattr(self.context, "get_using_provider", None)
        if callable(sync_getter):
            try:
                candidate = sync_getter(umo=umo or None)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[妙笔] 取默认 provider 失败: %s", exc)
                return []
            if candidate is not None and hasattr(candidate, "text_chat"):
                return [candidate]
        return []


def _provider_name(provider) -> str:
    """日志与 meta 里用的短标识，取不到就退回类名。"""
    try:
        meta = provider.meta()
        pid = str(getattr(meta, "id", "") or "")
        if pid:
            return pid
    except Exception:  # noqa: BLE001
        pass
    config = getattr(provider, "provider_config", {}) or {}
    return str(config.get("id") or config.get("model") or type(provider).__name__)


def _guard_timeout(provider) -> float:
    """复用 AstrBot 供应商自己的 timeout，只多留 30 秒；不新增可配置项。"""
    config = getattr(provider, "provider_config", {}) or {}
    raw = config.get("timeout")
    if raw is None:
        raw = getattr(provider, "timeout", 0)
    try:
        base = int(raw or 0)
    except (TypeError, ValueError):
        base = 0
    return float(base) + 30.0 if base > 0 else 0.0
