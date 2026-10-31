"""妙笔 · 配置存储（JSON 原子写 + 自动备份）。"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .models import normalize_presets, normalize_provider, normalize_settings
from .prompt_presets import presets_for

BACKUP_KEEP = 20

# 内置预设库版本：2 = NAI 与 Anima 画师串拆开。低于此值的 tag 模型会重播一次
PRESETS_VERSION = 2


def _is_tag_model(model: dict[str, Any], provider: dict[str, Any] | None = None) -> bool:
    """只有 NovelAI / Anima 这类吃 Danbooru tag 的模型才需要画师串预设。"""
    text = f"{model.get('model_id') or ''} {model.get('display_name') or ''}".lower()
    if any(key in text for key in ("nai-diffusion", "novelai", "anima")):
        return True
    return str((provider or {}).get("protocol") or "") == "novelai"


def _ensure_presets(providers: list[dict[str, Any]]) -> bool:
    """模型回填 provider_id，并给 tag 模型补上内置预设。

    返回是否发生了改动：load_providers 需要据此把结果写回磁盘，
    否则版本号永远停在 0，每次启动都会重新播种、覆盖用户的修改。

    presets_seeded 记住「补过一次」：用户把预设全删掉后，不会再被自动塞回来。
    非 tag 模型的提示词交给 LLM 优化，不生成画师串预设。
    """
    changed = False
    for provider in providers:
        for model in provider["models"]:
            model["provider_id"] = provider["id"]
            if not _is_tag_model(model, provider):
                if not model.get("prompt_presets") and not model.get("presets_seeded"):
                    model["presets_seeded"] = True
                    changed = True
                continue
            # 预设库版本 2：NAI 与 Anima 的画师串拆开（artist: 前缀 vs 裸写）。
            # 版本落后就一次性重播，之后不再动用户的修改。
            if int(model.get("presets_version") or 0) < PRESETS_VERSION:
                model["prompt_presets"] = normalize_presets(
                    presets_for(
                        model.get("model_id") or "",
                        provider.get("protocol") or "",
                        model.get("capabilities"),
                    )
                )
                model["presets_version"] = PRESETS_VERSION
                model["presets_seeded"] = True
                changed = True
                continue
            if not model.get("prompt_presets") and not model.get("presets_seeded"):
                model["prompt_presets"] = normalize_presets(
                    presets_for(
                        model.get("model_id") or "",
                        provider.get("protocol") or "",
                        model.get("capabilities"),
                    )
                )
                model["presets_seeded"] = True
                changed = True
    return changed


class ConfigStore:
    """providers.json / settings.json 的读写与备份。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.backup_dir = self.root / "backups"
        self.providers_path = self.root / "providers.json"
        self.settings_path = self.root / "settings.json"

    # ---------- 底层 ----------

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            # utf-8-sig：兼容被外部编辑器写出 BOM 的文件。
            # 用纯 utf-8 读带 BOM 的文件会抛 JSONDecodeError，
            # 之前就是这样把整份配置读成默认值、再被保存覆盖掉的。
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            logger.error("[妙笔] 读取 %s 失败: %s", path.name, exc)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            try:
                shutil.copy2(path, path.with_suffix(path.suffix + f".broken_{stamp}"))
            except Exception:  # noqa: BLE001
                pass
            # 读坏了先尝试用最近一次备份自愈，避免让界面拿着空配置再覆盖一次
            restored = self._restore_from_backup(path)
            if restored is not None:
                return restored
            return default

    def _restore_from_backup(self, path: Path) -> Any:
        if not self.backup_dir.exists():
            return None
        pattern = f"{path.stem}.*.json"
        for candidate in sorted(self.backup_dir.glob(pattern), reverse=True):
            try:
                data = json.loads(candidate.read_text(encoding="utf-8-sig"))
            except Exception:  # noqa: BLE001
                continue
            logger.warning(
                "[妙笔] %s 内容损坏，已用备份 %s 自动恢复", path.name, candidate.name
            )
            return data
        return None

    def _backup(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            target = self.backup_dir / f"{path.stem}.{stamp}.json"
            shutil.copy2(path, target)
            files = sorted(self.backup_dir.glob(f"{path.stem}.*.json"))
            for old in files[:-BACKUP_KEEP]:
                old.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[妙笔] 备份 %s 失败: %s", path.name, exc)

    def _write_json(self, path: Path, payload: Any) -> None:
        self._backup(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    # ---------- providers ----------

    def load_providers(self) -> list[dict[str, Any]]:
        raw = self._read_json(self.providers_path, {"providers": []})
        items = raw.get("providers") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            items = []
        providers = [normalize_provider(item) for item in items if isinstance(item, dict)]
        if _ensure_presets(providers):
            # 预设版本升级必须落盘，否则下次启动又从 0 重播，覆盖用户的修改
            self._write_json(self.providers_path, {"version": 1, "providers": providers})
        return providers

    def save_providers(self, providers: list[dict[str, Any]]) -> None:
        normalized = [normalize_provider(item) for item in providers]
        _ensure_presets(normalized)
        self._write_json(self.providers_path, {"version": 1, "providers": normalized})

    # ---------- settings ----------

    def load_settings(self, providers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """providers 用于把旧配置里的物理模型 key 迁移成 catalog id。"""
        return normalize_settings(self._read_json(self.settings_path, {}), providers)

    def save_settings(self, settings: dict[str, Any]) -> None:
        self._write_json(self.settings_path, normalize_settings(settings))
