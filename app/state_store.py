"""网页端的少量持久化状态（目前只有「上次使用的配置」）。

设计原则
--------
* **绝不因为状态读写失败而影响主流程**：文件损坏、无权限、被占用时一律按"没有状态"处理。
* 状态文件放在 ``cache/ui_state.json``（缓存目录下的独立文件）：
  清理缓存只删 ``*.bin`` / ``*.meta.json`` / ``city_index.json`` / ``raw/``，
  不会碰到它，因此"上次使用的配置"不会因为清缓存而丢失。
* 写入使用「临时文件 + 原子替换」，避免中途断电留下半个 JSON。
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: 状态文件名（不含目录）；缓存统计会把它排除在外
STATE_NAME = "ui_state.json"
STATE_FILE = PROJECT_ROOT / "cache" / STATE_NAME

_LOCK = threading.Lock()


def _empty() -> dict[str, Any]:
    return {"last_profile": "", "left_ratio": 0.0, "updated_at": ""}


def _clean_ratio(value: Any) -> float:
    """界面的左右分栏比例：只接受有限数字，其余（字符串、NaN、Inf）一律当 0（未设置）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number >= 1:
        return 0.0
    return number


def read_state() -> dict[str, Any]:
    """读取状态；文件缺失或损坏时返回空状态（不抛异常）。"""
    payload = _empty()
    try:
        raw = STATE_FILE.read_text(encoding="utf-8-sig")
    except OSError:
        return payload
    try:
        data = json.loads(raw)
    except ValueError:
        return payload
    if not isinstance(data, dict):
        return payload
    if isinstance(data.get("last_profile"), str):
        payload["last_profile"] = data["last_profile"].strip()
    payload["left_ratio"] = _clean_ratio(data.get("left_ratio"))
    if isinstance(data.get("updated_at"), str):
        payload["updated_at"] = data["updated_at"]
    return payload


def update_state(**changes: Any) -> dict[str, Any]:
    """合并写入状态；返回写入后的状态。失败时静默返回当前状态。"""
    with _LOCK:
        payload = read_state()
        payload.update(changes)
        payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, STATE_FILE)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return payload


def last_profile() -> str:
    """上次使用的配置名；没有则返回空串。"""
    return read_state()["last_profile"]


def remember_profile(name: Optional[str]) -> dict[str, Any]:
    """记住「上次使用的配置」。``None`` / 空串表示清除。"""
    text = str(name or "").strip()
    return update_state(last_profile=text)


def left_ratio() -> float:
    """左右分栏的左栏占比；没有记录则返回 0（由前端用默认值）。"""
    return read_state()["left_ratio"]


def remember_ratio(value: Any) -> dict[str, Any]:
    """记住左右分栏比例（非法值当作"未设置"）。"""
    return update_state(left_ratio=_clean_ratio(value))


def reset() -> None:
    """清除状态文件（测试与「重置」用）。"""
    with _LOCK:
        try:
            STATE_FILE.unlink(missing_ok=True)
        except OSError:
            pass


def state_path() -> Path:
    return STATE_FILE
