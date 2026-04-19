"""core/session_store.py — Session 持久化儲存。

結論先寫:
    - SessionStore 管理所有 session 設定的儲存與讀取。
    - 儲存路徑:Windows → %APPDATA%/AndyTerm/sessions.json;
                Linux/macOS → ~/.config/andyterm/sessions.json。
    - 密碼不儲存於 JSON;走 keyring (呼叫端負責)。
    - 支援資料夾分組 (folder: str | None)。
    - 內建唯讀 Quick Profiles (Moxa 現場常用設定)。

分層原則:本模組位於 core/,不可 import Qt。
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from andyterm.core.session import SerialConfig, SessionConfig, SshConfig

__all__ = ["SessionStore"]

# ---------------------------------------------------------------------------
# 內建 Quick Profiles (唯讀)
# ---------------------------------------------------------------------------

_QUICK_PROFILES: list[dict[str, Any]] = [
    {
        "id": "builtin-v3400",
        "name": "Moxa V3400 Console",
        "folder": "Quick Profiles",
        "type": "SERIAL",
        "port": "COM1",
        "baudrate": 115200,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1.0,
        "xonxoff": False,
        "rtscts": False,
        "encoding": "utf-8",
        "_builtin": True,
    },
    {
        "id": "builtin-v1200",
        "name": "Moxa V1200 U-Boot/Linux Console",
        "folder": "Quick Profiles",
        "type": "SERIAL",
        "port": "COM1",
        "baudrate": 921600,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1.0,
        "xonxoff": False,
        "rtscts": False,
        "encoding": "utf-8",
        "_builtin": True,
    },
    {
        "id": "builtin-v2406c",
        "name": "Moxa V2406C Console",
        "folder": "Quick Profiles",
        "type": "SERIAL",
        "port": "COM1",
        "baudrate": 115200,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1.0,
        "xonxoff": False,
        "rtscts": False,
        "encoding": "utf-8",
        "_builtin": True,
    },
    {
        "id": "builtin-nport-rfc2217",
        "name": "Moxa NPort RFC2217 Template",
        "folder": "Quick Profiles",
        "type": "SERIAL",
        "port": "rfc2217://192.168.127.254:4001",
        "baudrate": 115200,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1.0,
        "xonxoff": False,
        "rtscts": False,
        "encoding": "utf-8",
        "_builtin": True,
    },
]


def _get_store_path() -> Path:
    """回傳 sessions.json 儲存路徑 (平台相關)。"""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "AndyTerm" / "sessions.json"
    return Path.home() / ".config" / "andyterm" / "sessions.json"


class SessionStore:
    """Session 設定的持久化儲存管理器。

    結論:
        - 建構時載入 sessions.json (不存在則空白初始化)。
        - add / update / remove / get / list 提供 CRUD。
        - as_tree() 回傳資料夾分組結構,供 UI tree view 使用。
        - builtin profiles 以唯讀方式合入 list(),不寫入 JSON。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _get_store_path()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._sessions = {s["id"]: s for s in data if "id" in s}
            except (json.JSONDecodeError, KeyError):
                self._sessions = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = list(self._sessions.values())
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, config: SessionConfig, folder: str | None = None) -> str:
        """新增 session;回傳分配的 ID。"""
        session_id = str(uuid.uuid4())
        entry = json.loads(config.model_dump_json())
        entry["id"] = session_id
        entry["folder"] = folder
        self._sessions[session_id] = entry
        self._save()
        return session_id

    def update(self, session_id: str, config: SessionConfig) -> None:
        """更新 session 設定。"""
        if session_id not in self._sessions:
            raise KeyError(f"Session 不存在 / Session not found: {session_id}")
        folder = self._sessions[session_id].get("folder")
        entry = json.loads(config.model_dump_json())
        entry["id"] = session_id
        entry["folder"] = folder
        self._sessions[session_id] = entry
        self._save()

    def remove(self, session_id: str) -> None:
        """刪除 session。"""
        self._sessions.pop(session_id, None)
        self._save()

    def get(self, session_id: str) -> dict[str, Any] | None:
        """取得單一 session 資料 (包含 builtin)。"""
        for p in _QUICK_PROFILES:
            if p["id"] == session_id:
                return dict(p)
        return dict(self._sessions[session_id]) if session_id in self._sessions else None

    def list_sessions(self, include_builtin: bool = True) -> list[dict[str, Any]]:
        """列出所有 session;builtin profiles 在前。"""
        result: list[dict[str, Any]] = []
        if include_builtin:
            result.extend(_QUICK_PROFILES)
        result.extend(self._sessions.values())
        return result

    # ------------------------------------------------------------------
    # Tree 結構 (供 UI 使用)
    # ------------------------------------------------------------------

    def as_tree(self, include_builtin: bool = True) -> dict[str | None, list[dict[str, Any]]]:
        """以資料夾分組回傳 session tree。

        回傳:
            {folder_name: [session_dict, ...], None: [...unfiled...]}
        """
        tree: dict[str | None, list[dict[str, Any]]] = {}
        for s in self.list_sessions(include_builtin=include_builtin):
            folder = s.get("folder")
            tree.setdefault(folder, []).append(s)
        return tree

    # ------------------------------------------------------------------
    # 便利建構
    # ------------------------------------------------------------------

    @staticmethod
    def config_from_dict(data: dict[str, Any]) -> SessionConfig:
        """從 session dict 還原 SessionConfig 物件。"""
        session_type = data.get("type", "SERIAL")
        if session_type in ("SSH", "SFTP"):
            return SshConfig(**{
                k: v for k, v in data.items()
                if k in SshConfig.model_fields
            })
        return SerialConfig(**{
            k: v for k, v in data.items()
            if k in SerialConfig.model_fields
        })
