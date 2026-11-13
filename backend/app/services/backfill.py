"""历史回填业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "backfill"
REQUIRED_FIELDS = ["作业编号", "目标表名", "回填起始"]
STATUS_ORDER = ["草稿", "待执行", "运行中", "待同步", "已完成", "已失败"]
ACTION_RULES = {
    "提交执行": "待执行",
    "启动回填": "运行中",
    "提交同步": "待同步",
    "完成同步": "已完成",
    "终止回填": "已失败",
    "归档结果": "已完成",
}
NEGATIVE_ACTIONS = ["终止回填"]
# 目标表仍在被写入的状态：这些状态的回填作业会阻止快照恢复。
WRITING_STATUSES = ["待执行", "运行中", "待同步"]


class BackfillService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("作业编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"回填作业 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于历史回填可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target not in {"已完成", "已失败"}
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"回填作业已{action}"
