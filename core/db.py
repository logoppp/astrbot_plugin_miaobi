"""妙笔 · sqlite 索引（任务 + 资产元数据）。

每次操作独立连接，避免多 loop / 多线程共享连接的问题；WAL 模式保证读写不打架。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_JOB_FIELDS = (
    "id",
    "capability",
    "catalog_id",
    "provider_id",
    "model_key",
    "model_label",
    "prompt",
    "optimized_prompt",
    "params",
    "state",
    "error",
    "progress",
    "source",
    "umo",
    "queue_position",
    "created",
    "started",
    "finished",
)

_ASSET_FIELDS = (
    "id",
    "job_id",
    "kind",
    "capability",
    "path",
    "thumb_path",
    "mime",
    "width",
    "height",
    "bytes",
    "provider_id",
    "provider_name",
    "model_key",
    "model_id",
    "prompt",
    "optimized_prompt",
    "negative_prompt",
    "params",
    "seed",
    "duration",
    "elapsed",
    "created",
    "favorite",
    "deleted",
)


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    capability TEXT,
                    catalog_id TEXT,
                    provider_id TEXT,
                    model_key TEXT,
                    model_label TEXT,
                    prompt TEXT,
                    optimized_prompt TEXT,
                    params TEXT,
                    state TEXT,
                    error TEXT,
                    progress TEXT,
                    source TEXT,
                    umo TEXT,
                    queue_position INTEGER,
                    created REAL,
                    started REAL,
                    finished REAL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);

                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    job_id TEXT,
                    kind TEXT,
                    capability TEXT,
                    path TEXT,
                    thumb_path TEXT,
                    mime TEXT,
                    width INTEGER,
                    height INTEGER,
                    bytes INTEGER,
                    provider_id TEXT,
                    provider_name TEXT,
                    model_key TEXT,
                    model_id TEXT,
                    prompt TEXT,
                    optimized_prompt TEXT,
                    negative_prompt TEXT,
                    params TEXT,
                    seed TEXT,
                    duration REAL,
                    elapsed REAL,
                    created REAL,
                    favorite INTEGER DEFAULT 0,
                    deleted INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_assets_created ON assets(created DESC);
                CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);
                """
            )
            # v2 补列：逻辑模型标识（旧库直接加列，不动已有数据）
            cols = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            if "catalog_id" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN catalog_id TEXT")

    # ---------- jobs ----------

    @staticmethod
    def _dump(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            return "{}"

    @staticmethod
    def _loads(value: Any, default: Any = None) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except Exception:  # noqa: BLE001
            return default

    def create_job(self, job: dict[str, Any]) -> None:
        payload = {key: job.get(key) for key in _JOB_FIELDS}
        payload["params"] = self._dump(job.get("params") or {})
        payload["progress"] = self._dump(job.get("progress") or {})
        payload["created"] = payload.get("created") or time.time()
        columns = ", ".join(payload)
        marks = ", ".join("?" for _ in payload)
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO jobs ({columns}) VALUES ({marks})",
                tuple(payload.values()),
            )

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        payload = {k: v for k, v in fields.items() if k in _JOB_FIELDS}
        for key in ("params", "progress"):
            if key in payload:
                payload[key] = self._dump(payload[key])
        if not payload:
            return
        assignments = ", ".join(f"{key} = ?" for key in payload)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",
                tuple(payload.values()) + (job_id,),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def fail_stale_jobs(self, message: str = "插件重启，任务已中断") -> int:
        """启动时回收上次残留的 queued/running 任务，否则它们会永远停在「生成中」。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM jobs WHERE state IN ('queued', 'running')"
            ).fetchone()
            total = int(row["c"]) if row else 0
            if total:
                conn.execute(
                    "UPDATE jobs SET state = 'failed', finished = ?, error = ? "
                    "WHERE state IN ('queued', 'running')",
                    (time.time(), message),
                )
        return total

    def list_jobs(self, limit: int = 30, states: list[str] | None = None) -> list[dict]:
        sql = "SELECT * FROM jobs"
        args: list[Any] = []
        if states:
            marks = ", ".join("?" for _ in states)
            sql += f" WHERE state IN ({marks})"
            args.extend(states)
        sql += " ORDER BY created DESC LIMIT ?"
        args.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(args)).fetchall()
        return [self._row(row) for row in rows]

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in ("params", "progress"):
            if key in item:
                item[key] = self._loads(item[key], {})
        return item

    # ---------- assets ----------

    def add_asset(self, asset: dict[str, Any]) -> None:
        payload = {key: asset.get(key) for key in _ASSET_FIELDS}
        payload["params"] = self._dump(asset.get("params") or {})
        payload["created"] = payload.get("created") or time.time()
        payload["favorite"] = int(bool(asset.get("favorite")))
        payload["deleted"] = int(bool(asset.get("deleted")))
        columns = ", ".join(payload)
        marks = ", ".join("?" for _ in payload)
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO assets ({columns}) VALUES ({marks})",
                tuple(payload.values()),
            )

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM assets WHERE id = ? AND deleted = 0", (asset_id,)
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["params"] = self._loads(item.get("params"), {})
        return item

    def list_assets(
        self,
        *,
        limit: int = 40,
        offset: int = 0,
        kind: str = "",
        query: str = "",
        favorite_only: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        where = ["deleted = 0"]
        args: list[Any] = []
        if kind:
            where.append("kind = ?")
            args.append(kind)
        if query:
            where.append("(prompt LIKE ? OR optimized_prompt LIKE ? OR model_key LIKE ?)")
            like = f"%{query}%"
            args.extend([like, like, like])
        if favorite_only:
            where.append("favorite = 1")
        clause = " WHERE " + " AND ".join(where)
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM assets{clause}", tuple(args)
            ).fetchone()["c"]
            rows = conn.execute(
                f"SELECT * FROM assets{clause} ORDER BY created DESC LIMIT ? OFFSET ?",
                tuple(args) + (int(limit), int(offset)),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["params"] = self._loads(item.get("params"), {})
            items.append(item)
        return items, int(total)

    def delete_asset(self, asset_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE assets SET deleted = 1 WHERE id = ?", (asset_id,))

    def toggle_favorite(self, asset_id: str, value: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE assets SET favorite = ? WHERE id = ?", (int(bool(value)), asset_id)
            )

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(bytes), 0) AS bytes "
                "FROM assets WHERE deleted = 0"
            ).fetchone()
            kinds = conn.execute(
                "SELECT kind, COUNT(*) AS c FROM assets WHERE deleted = 0 GROUP BY kind"
            ).fetchall()
        return {
            "total": int(row["total"]),
            "bytes": int(row["bytes"]),
            "kinds": {item["kind"]: int(item["c"]) for item in kinds},
        }
