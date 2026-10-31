"""妙笔 · 模型注册与解析。

一个 provider 可以挂多个 model（同一个 base_url 多种型号），调用时按模型解析。
解析顺序：精确 model key → 别名 → model_id → 显示名 → 唯一前缀（归一化后比较）。
"""

from __future__ import annotations

import re
from typing import Any

from astrbot.api import logger

from .backends import Backend, backend_class


def norm_alias(text: str) -> str:
    return re.sub(r"[\s\-_/·]+", "", str(text or "").strip().lower())


class ModelRef:
    __slots__ = ("provider", "model", "backend_cls")

    def __init__(self, provider: dict[str, Any], model: dict[str, Any]):
        self.provider = provider
        self.model = model
        self.backend_cls = backend_class(provider.get("protocol") or "")

    # ---------- 标识 ----------

    @property
    def key(self) -> str:
        return str(self.model.get("id") or "")

    @property
    def model_id(self) -> str:
        return str(self.model.get("model_id") or self.key)

    @property
    def label(self) -> str:
        return str(self.model.get("display_name") or self.model_id)

    @property
    def provider_id(self) -> str:
        return str(self.provider.get("id") or "")

    @property
    def provider_name(self) -> str:
        return str(self.provider.get("name") or self.provider_id)

    @property
    def protocol(self) -> str:
        return str(self.provider.get("protocol") or "")

    @property
    def capabilities(self) -> list[str]:
        return list(self.model.get("capabilities") or [])

    @property
    def qualified(self) -> str:
        return f"{self.provider_name}/{self.label}"

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    def create(self, *, media=None, settings: dict | None = None) -> Backend:
        return self.backend_cls(self.provider, self.model, media=media, settings=settings)

    def brief(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "model_id": self.model_id,
            "label": self.label,
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "protocol": self.protocol,
            "capabilities": self.capabilities,
            "defaults": self.model.get("defaults") or {},
            "limits": self.model.get("limits") or {},
            "params": self.model.get("params") or {},
            "prompt_presets": self.model.get("prompt_presets") or [],
            "default_preset_id": self.model.get("default_preset_id") or "",
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ModelRef {self.qualified} {self.protocol}>"


class ModelRegistry:
    def __init__(self, providers: list[dict[str, Any]] | None = None):
        self._providers: list[dict[str, Any]] = []
        self._refs: list[ModelRef] = []
        self.reload(providers or [])

    def reload(self, providers: list[dict[str, Any]]) -> None:
        self._providers = list(providers or [])
        refs: list[ModelRef] = []
        for provider in self._providers:
            if not provider.get("enabled", True):
                continue
            for model in provider.get("models") or []:
                if not model.get("enabled", True):
                    continue
                try:
                    refs.append(ModelRef(provider, model))
                except Exception:  # noqa: BLE001 - 未知协议跳过，不影响其它模型
                    continue
        self._refs = refs
        self._by_key = {ref.key: ref for ref in refs}

    # ---------- 查询 ----------

    @property
    def refs(self) -> list[ModelRef]:
        return list(self._refs)

    def list_by_capability(self, capability: str | None = None) -> list[ModelRef]:
        if not capability:
            return list(self._refs)
        return [ref for ref in self._refs if ref.has(capability)]

    def get(self, key: str) -> ModelRef | None:
        return self._by_key.get(str(key or ""))

    # ---------- 逻辑模型（catalog） ----------

    @staticmethod
    def catalog_entries(settings: dict[str, Any] | None) -> list[dict[str, Any]]:
        return [
            item
            for item in (settings or {}).get("catalog") or []
            if isinstance(item, dict)
        ]

    @staticmethod
    def catalog_entry(catalog_id: str, settings: dict[str, Any] | None) -> dict[str, Any] | None:
        want = str(catalog_id or "").strip()
        if not want:
            return None
        for item in ModelRegistry.catalog_entries(settings):
            if str(item.get("id")) == want:
                return item
        return None

    def _ref_by_binding(self, provider_id: str, model_id: str) -> ModelRef | None:
        """按 provider_id + model_id 找回物理条目；model_id 空时用物理 key 兜底。"""
        pid = str(provider_id or "")
        mid = str(model_id or "")
        if not pid and not mid:
            return None
        for ref in self._refs:
            if pid and ref.provider_id != pid:
                continue
            if mid and ref.model_id != mid and ref.key != mid:
                continue
            return ref
        return None

    def chain_refs(
        self, entry: dict[str, Any], capability: str
    ) -> list[ModelRef]:
        """catalog 的候选实现，按 chain 顺序过滤掉停用/缺能力的条目。"""
        out: list[ModelRef] = []
        seen: set[str] = set()
        for binding in entry.get("chain") or []:
            if not isinstance(binding, dict):
                continue
            ref = self._ref_by_binding(
                str(binding.get("provider_id") or ""),
                str(binding.get("model_id") or ""),
            )
            if ref is None or not ref.has(capability) or ref.key in seen:
                continue
            seen.add(ref.key)
            out.append(ref)
        return out

    def all_chain_refs(
        self, entry: dict[str, Any]
    ) -> list[tuple[dict[str, Any], ModelRef | None]]:
        """原样列出每个绑定及其解析结果（None 表示供应商或模型已不存在），给界面用。"""
        out: list[tuple[dict[str, Any], ModelRef | None]] = []
        for binding in entry.get("chain") or []:
            if not isinstance(binding, dict):
                continue
            ref = self._ref_by_binding(
                str(binding.get("provider_id") or ""),
                str(binding.get("model_id") or ""),
            )
            out.append((binding, ref))
        return out

    @staticmethod
    def _pseudo_entry(ref: ModelRef, capability: str) -> dict[str, Any]:
        """命令里直接点物理模型时的临时条目，让上层逻辑不必区分两种来源。"""
        return {
            "id": "",
            "name": ref.label,
            "family": "",
            "capabilities": [capability],
            "chain": [],
            "optimizer_prompt": "",
            "enabled": True,
        }

    def resolve_target(
        self,
        capability: str,
        settings: dict[str, Any] | None,
        token: str = "",
    ) -> tuple[dict[str, Any] | None, list[ModelRef]]:
        """返回 (逻辑模型条目, 有序候选)。

        token 优先按 catalog id 解析；解析不到再退回物理模型/别名，
        这样老的 /文生图 nai45 用法不会断。
        """
        settings = settings or {}
        raw = str(token or "").strip()

        if raw:
            entry = self.catalog_entry(raw, settings)
            if entry and entry.get("enabled", True):
                refs = self.chain_refs(entry, capability)
                if refs:
                    return entry, refs
                logger.warning(
                    "[妙笔] 模型「%s」的绑定都不支持 %s（或已停用），改用其它模型",
                    raw,
                    capability,
                )
            ref = self.resolve(raw, capability)
            if ref is not None:
                return self._pseudo_entry(ref, capability), [ref]
            return None, []

        configured = str((settings.get("defaults") or {}).get(capability) or "")
        if configured:
            entry = self.catalog_entry(configured, settings)
            if entry and entry.get("enabled", True):
                refs = self.chain_refs(entry, capability)
                if refs:
                    return entry, refs
                logger.warning(
                    "[妙笔] 默认模型「%s」没有可用供应商（已停用或缺少 %s），改用备选",
                    configured,
                    capability,
                )
            elif entry is None:
                # 还没迁移的旧配置：defaults 里存的是物理模型 key
                ref = self.resolve(configured, capability) or self.get(configured)
                if ref is not None and ref.has(capability):
                    return self._pseudo_entry(ref, capability), [ref]
                logger.warning(
                    "[妙笔] 默认模型「%s」不可用（已停用、已删除或缺少 %s 能力），改用备选",
                    configured,
                    capability,
                )

        for entry in self.catalog_entries(settings):
            if not entry.get("enabled", True):
                continue
            refs = self.chain_refs(entry, capability)
            if refs:
                return entry, refs

        for token in (settings.get("fallback_chain") or {}).get(capability) or []:
            ref = self.resolve(str(token), capability)
            if ref is not None:
                return self._pseudo_entry(ref, capability), [ref]

        pool = self.list_by_capability(capability)
        if pool:
            return self._pseudo_entry(pool[0], capability), [pool[0]]
        return None, []

    # ---------- 解析 ----------

    def candidates(self, token: str, capability: str | None = None) -> list[ModelRef]:
        raw = str(token or "").strip()
        if not raw:
            return []
        pool = self.list_by_capability(capability)
        needle = norm_alias(raw)

        exact = [ref for ref in pool if raw == ref.key]
        if exact:
            return exact

        alias_hit: list[ModelRef] = []
        for ref in pool:
            names = [ref.model_id, ref.label, ref.key, *(ref.model.get("aliases") or [])]
            if any(str(name).strip() == raw for name in names if str(name).strip()):
                alias_hit.append(ref)
        if alias_hit:
            return alias_hit

        # provider/模型 形式
        if "/" in raw:
            provider_part, model_part = raw.split("/", 1)
            want_provider = norm_alias(provider_part)
            want_model = norm_alias(model_part)
            scoped = [
                ref
                for ref in pool
                if want_provider in (norm_alias(ref.provider_name), norm_alias(ref.provider_id))
                and want_model
                in {
                    norm_alias(ref.model_id),
                    norm_alias(ref.label),
                    norm_alias(ref.key),
                    *(norm_alias(a) for a in (ref.model.get("aliases") or [])),
                }
            ]
            if scoped:
                return scoped

        normalized = [
            ref
            for ref in pool
            if needle
            and needle
            in {
                norm_alias(ref.model_id),
                norm_alias(ref.label),
                norm_alias(ref.key),
                *(norm_alias(a) for a in (ref.model.get("aliases") or [])),
            }
        ]
        if normalized:
            return normalized

        return [
            ref
            for ref in pool
            if needle
            and any(
                norm_alias(name).startswith(needle)
                for name in (ref.model_id, ref.label, *(ref.model.get("aliases") or []))
            )
        ]

    def resolve(self, token: str, capability: str | None = None) -> ModelRef | None:
        hits = self.candidates(token, capability)
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            # 同名时优先 provider 前缀完全一致的，否则按配置顺序取第一个
            first = hits[0]
            same = [ref for ref in hits if ref.model_id == first.model_id]
            return same[0] if same else first
        return None

    def pick_default(
        self, capability: str, settings: dict[str, Any]
    ) -> ModelRef | None:
        """默认模型的首个可用实现。

        catalog 内部按 chain 顺序回退（同一模型换供应商）；跨模型回退不再自动进行，
        避免一个任务的提示词与画风被换成另一个模型。
        """
        _entry, refs = self.resolve_target(capability, settings)
        return refs[0] if refs else None

