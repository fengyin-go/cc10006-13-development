"""版本快照业务规则：状态流转、依赖链校验、恢复校验与幂等恢复。

这里的规则分成三层：

1. 快照自身的生命周期严格单向：草稿 -> 待创建 -> 已生效 -> 已过期。
2. 依赖快照必须已经生效，依赖链要递归校验；任一缺失或未生效都阻止生效与恢复。
3. 恢复校验必须先行通过，恢复还会检查目标表的聚合视图与回填作业，
   正在写入或待同步的引用会阻止恢复；恢复成功只产生一次目标表变更，
   重复请求返回同一张恢复回执。
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.store import store
from app.services.backfill import WRITING_STATUSES as BACKFILL_WRITING_STATUSES

MODULE = "snapshot"

REQUIRED_FIELDS = ["快照编号", "目标表名", "快照时间"]
OPTIONAL_FIELDS = ["快照大小", "存储介质", "过期策略", "恢复校验", "依赖快照"]

STATUS_ORDER = ["草稿", "待创建", "已生效", "已过期"]
STATUS_STEP = {
    "提交创建": ("草稿", "待创建"),
    "生效快照": ("待创建", "已生效"),
    "标记过期": ("已生效", "已过期"),
}

RESTORE_CHECK_PENDING = "待校验"
RESTORE_CHECK_PASSED = "校验通过"
RESTORE_CHECK_ACTION_ALIASES = {"校验恢复", "恢复校验", "校验快照", "提交校验", "校验"}
RESTORE_ACTION_ALIASES = {"恢复快照", "恢复", "执行恢复"}
CORE_ACTIONS = set(STATUS_STEP) | RESTORE_CHECK_ACTION_ALIASES | RESTORE_ACTION_ALIASES

# 兼容历史数据里常见的几种写法，统一按“校验通过”处理。
_PASSED_ALIASES = {"校验通过", "校验完成", "已校验", "已通过", "通过", "校验成功"}
_PENDING_ALIASES = {"", "未校验", "待校验", "校验中", "未通过"}

# 目标表上仍然会写入的引用：聚合层里已启用/待校验的视图，
# 以及历史回填里运行中/待执行（含待同步变更）的作业。
_ACTIVE_GOLD_STATUSES = {"已启用", "待校验"}
_ACTIVE_BACKFILL_STATUSES = set(BACKFILL_WRITING_STATUSES)

_DEP_SPLIT = re.compile(r"[,，、;；/\s]+")
_DEP_EMPTY = {"", "-", "—", "无", "none", "null", "None"}
_RESTORE_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _canonical_restore_check(value: Any) -> str:
    text = str(value or "").strip()
    if text in _PASSED_ALIASES:
        return RESTORE_CHECK_PASSED
    if text in _PENDING_ALIASES:
        return RESTORE_CHECK_PENDING
    return text


def _split_dependencies(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = [item.strip() for item in _DEP_SPLIT.split(str(value or ""))]
    return [item for item in raw_items if item not in _DEP_EMPTY]


def _next_status(current: str, target: str) -> bool:
    """只允许沿状态序列顺序前进一步，不能回退也不能跳级。"""
    if current not in STATUS_ORDER or target not in STATUS_ORDER:
        return False
    return STATUS_ORDER.index(target) == STATUS_ORDER.index(current) + 1


@dataclass
class Outcome:
    """服务层统一的结果对象，路由层据此组装 ActionResult。"""

    ok: bool
    message: str
    entry: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    missing_dependencies: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    duplicate: bool = False
    applied: bool = False


class SnapshotService:
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
            rows = [row for row in rows if keyword in str(row.get("快照编号", ""))]
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
        entry = {"id": _next_id(rows)}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field_name in OPTIONAL_FIELDS:
            if field_name in values:
                entry[field_name] = values.get(field_name)
        entry["依赖快照"] = entry.get("依赖快照") or ""
        # 恢复校验只能从待校验单向推进到校验通过，登记时不允许直接写入结果。
        entry["恢复校验"] = RESTORE_CHECK_PENDING
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    # ------------------------------------------------------------------
    # 依赖链
    # ------------------------------------------------------------------
    def _find_dependency(self, reference: str) -> dict[str, Any] | None:
        reference = str(reference).strip()
        if not reference:
            return None
        for row in store.rows(MODULE):
            if str(row.get("快照编号", "")).strip() == reference:
                return row
        if reference.isdigit():
            return store.find(MODULE, int(reference))
        return None

    def _dependency_issues(
        self,
        entry: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """递归校验依赖链，返回结构化问题与可读的缺失依赖清单。"""
        issues: list[dict[str, Any]] = []
        labels: list[str] = []
        seen_labels: set[str] = set()
        stack: set[int] = set()

        def add_issue(reference: str, reason: str, dependency: dict[str, Any] | None = None) -> None:
            label = str((dependency or {}).get("快照编号") or reference)
            display = f"{label}（{reason}）"
            if display in seen_labels:
                return
            seen_labels.add(display)
            issues.append(
                {
                    "依赖快照": label,
                    "状态": str((dependency or {}).get("status") or "不存在"),
                    "原因": reason,
                }
            )
            labels.append(display)

        def walk(node: dict[str, Any], linked_by: str | None) -> None:
            node_id = int(node.get("id", 0))
            if node_id in stack:
                add_issue(str(node.get("快照编号") or linked_by or node_id), "依赖链存在循环引用", node)
                return
            stack.add(node_id)
            for reference in _split_dependencies(node.get("依赖快照")):
                dependency = self._find_dependency(reference)
                if dependency is None:
                    add_issue(reference, "不存在")
                    continue
                dependency_status = str(dependency.get("status") or "")
                if dependency_status != "已生效":
                    add_issue(reference, f"状态为{dependency_status or '空'}", dependency)
                walk(dependency, reference)
            stack.discard(node_id)

        walk(entry, None)
        return issues, labels

    # ------------------------------------------------------------------
    # 目标表写入引用
    # ------------------------------------------------------------------
    @staticmethod
    def _mentions_target(value: Any, target: str) -> bool:
        """字段里出现目标表名即视为引用了这张表，逗号/顿号等分隔的多值也能命中。"""
        if value is None or not target:
            return False
        for item in _DEP_SPLIT.split(str(value)):
            if item.strip() == target:
                return True
        return str(value).strip() == target

    def _blocking_references(self, entry: dict[str, Any]) -> list[str]:
        """恢复前检查目标表的聚合视图与回填作业，正在写入的引用必须阻止恢复。"""
        target = str(entry.get("目标表名") or "").strip()
        if not target:
            return []
        blockers: list[str] = []
        for row in store.rows("gold"):
            if str(row.get("status") or "") not in _ACTIVE_GOLD_STATUSES:
                continue
            candidates = [row.get("上游清洗表"), row.get("目标表名"), row.get("视图编号")]
            if any(self._mentions_target(value, target) for value in candidates):
                label = row.get("视图编号") or row.get("id")
                blockers.append(f"聚合视图 {label}（{row.get('status')}）")
        for row in store.rows("backfill"):
            if str(row.get("status") or "") not in _ACTIVE_BACKFILL_STATUSES:
                continue
            if self._mentions_target(row.get("目标表名"), target):
                label = row.get("作业编号") or row.get("id")
                blockers.append(f"回填作业 {label}（{row.get('status')}）")
        return blockers

    # ------------------------------------------------------------------
    # 审计
    # ------------------------------------------------------------------
    @staticmethod
    def _audit(entry: dict[str, Any], operation: str, detail: str, *, ok: bool) -> dict[str, Any]:
        rows = store.rows("audit")
        next_id = _next_id(rows)
        audit = {
            "id": next_id,
            "status": "已归档",
            "pending": False,
            "abnormal": not ok,
            "日志编号": f"AUDI-{next_id:04d}",
            "操作类型": operation,
            "操作对象": str(entry.get("快照编号") or entry.get("id")),
            "操作详情": detail,
            "操作来源": "版本快照",
            "执行时间": _now(),
            "影响行数": 1 if ok else 0,
            "回滚能力": "不可回滚",
        }
        rows.append(audit)
        return audit

    # ------------------------------------------------------------------
    # 动作执行
    # ------------------------------------------------------------------
    def perform_action(self, entry_id: int, action: str) -> Outcome:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return Outcome(ok=False, message=f"快照记录 {entry_id} 不存在或已归档")

        action = str(action or "").strip()
        if action not in CORE_ACTIONS:
            return Outcome(ok=False, message=f"动作「{action}」不属于版本快照可执行范围")
        if action in RESTORE_CHECK_ACTION_ALIASES:
            return self.check_snapshot(entry_id)
        if action in RESTORE_ACTION_ALIASES:
            return self.restore_snapshot(entry_id)
        if action == "提交创建":
            return self._submit_entry(entry)
        if action == "生效快照":
            return self._effect_entry(entry)
        if action == "标记过期":
            return self._expire_entry(entry)
        return Outcome(ok=False, message=f"动作「{action}」不属于版本快照可执行范围")

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """兼容旧调用方式：仅返回记录与消息。"""
        outcome = self.perform_action(entry_id, action)
        if not outcome.ok:
            return None, outcome.message
        return outcome.receipt or outcome.entry or store.find(MODULE, entry_id), outcome.message

    def _submit_entry(self, entry: dict[str, Any]) -> Outcome:
        current = str(entry.get("status") or "")
        if current == "待创建":
            return Outcome(ok=True, message="快照记录已提交创建", entry=entry)
        if not _next_status(current, "待创建"):
            return Outcome(
                ok=False,
                message=f"快照当前状态为「{current}」，只能从草稿提交创建，不允许回退或跳级",
            )
        entry["status"] = "待创建"
        entry["pending"] = True
        entry["abnormal"] = False
        return Outcome(ok=True, message="快照记录已提交创建", entry=entry)

    def _effect_entry(self, entry: dict[str, Any]) -> Outcome:
        current = str(entry.get("status") or "")
        if current == "已过期":
            return Outcome(ok=False, message="快照已过期，不能再参与生效")
        if current == "已生效":
            return Outcome(ok=True, message="快照已是已生效状态", entry=entry)

        if not _next_status(current, "已生效"):
            return Outcome(
                ok=False,
                message=f"快照当前状态为「{current}」，生效前必须先提交创建，不允许跳级",
                entry=None,
            )
        issues, missing = self._dependency_issues(entry)
        if issues:
            message = "依赖快照链不完整，缺失依赖：" + "、".join(missing)
            self._audit(entry, "生效快照", message, ok=False)
            return Outcome(
                ok=False,
                message=message,
                entry=None,
                missing_dependencies=missing,
            )
        if _canonical_restore_check(entry.get("恢复校验")) != RESTORE_CHECK_PASSED:
            self._audit(entry, "生效快照", "恢复校验尚未通过，禁止生效", ok=False)
            return Outcome(
                ok=False,
                message="快照恢复校验尚未通过，不能生效；请先执行校验恢复并在依赖链完整后重试",
                entry=None,
            )
        entry["status"] = "已生效"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["生效时间"] = _now()
        self._audit(entry, "生效快照", "快照已生效", ok=True)
        return Outcome(ok=True, message="快照记录已生效", entry=entry)

    def _expire_entry(self, entry: dict[str, Any]) -> Outcome:
        current = str(entry.get("status") or "")
        if current == "已过期":
            return Outcome(ok=True, message="快照已是已过期状态", entry=entry)
        if not _next_status(current, "已过期"):
            return Outcome(
                ok=False,
                message=f"快照当前状态为「{current}」，只有已生效快照才能标记过期，不允许跳级",
            )
        entry["status"] = "已过期"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["过期时间"] = _now()
        self._audit(entry, "标记过期", "快照已标记过期", ok=True)
        return Outcome(ok=True, message="快照记录已标记过期", entry=entry)

    def check_snapshot(self, entry_id: int) -> Outcome:
        """执行恢复校验；依赖链不完整时只报告缺失项，不改变校验状态。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return Outcome(ok=False, message=f"快照记录 {entry_id} 不存在或已归档")
        if str(entry.get("status") or "") == "已过期":
            self._audit(entry, "恢复校验", "过期快照不能执行恢复校验", ok=False)
            return Outcome(ok=False, message="快照已过期，不能执行恢复校验")

        current_check = _canonical_restore_check(entry.get("恢复校验"))
        if current_check == RESTORE_CHECK_PASSED:
            return Outcome(ok=True, message="快照恢复校验已通过", entry=entry)
        if current_check != RESTORE_CHECK_PENDING:
            message = f"恢复校验状态「{current_check}」不在 待校验 -> 校验通过 的单向序列里"
            self._audit(entry, "恢复校验", message, ok=False)
            return Outcome(ok=False, message=message)

        issues, missing = self._dependency_issues(entry)
        if issues:
            message = "依赖快照链不完整，缺失依赖：" + "、".join(missing)
            self._audit(entry, "恢复校验", message, ok=False)
            return Outcome(ok=False, message=message, entry=None, missing_dependencies=missing)

        entry["恢复校验"] = RESTORE_CHECK_PASSED
        entry["校验时间"] = _now()
        entry["pending"] = str(entry.get("status") or "") not in {"已生效", "已过期"}
        self._audit(entry, "恢复校验", "快照恢复校验通过", ok=True)
        return Outcome(ok=True, message="快照恢复校验通过", entry=entry)

    def _receipts(self) -> dict[int, dict[str, Any]]:
        receipts = getattr(store, "snapshot_receipts", None)
        if receipts is None:
            receipts = {}
            setattr(store, "snapshot_receipts", receipts)
        return receipts

    def get_receipt(self, entry_id: int) -> dict[str, Any] | None:
        return self._receipts().get(int(entry_id))

    def list_receipts(self) -> list[dict[str, Any]]:
        return list(self._receipts().values())

    def get_target_state(self, table_name: str) -> dict[str, Any] | None:
        state = getattr(store, "snapshot_target_versions", {})
        return state.get(table_name)

    def list_target_states(self) -> list[dict[str, Any]]:
        state = getattr(store, "snapshot_target_versions", {})
        return list(state.values())

    def restore_snapshot(self, entry_id: int) -> Outcome:
        """恢复快照：校验依赖、校验状态、目标表写入引用，成功后写入唯一回执。"""
        with _RESTORE_LOCK:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return Outcome(ok=False, message=f"快照记录 {entry_id} 不存在或已归档")

            if str(entry.get("status") or "") == "已过期":
                # 过期快照保留审计记录，但不再参与恢复；已有回执也只作为历史回执可读。
                self._audit(entry, "恢复快照", "过期快照不能参与恢复，仅保留审计记录", ok=False)
                return Outcome(ok=False, message="快照已过期，不能再参与恢复，仅保留审计记录")

            receipt = self.get_receipt(entry_id)
            if receipt is not None:
                self._audit(entry, "恢复快照", "重复恢复请求命中已有回执，目标表未重复变更", ok=True)
                return Outcome(
                    ok=True,
                    message="快照已恢复，返回同一恢复回执",
                    entry=entry,
                    receipt=receipt,
                    duplicate=True,
                    applied=False,
                )

            issues, missing = self._dependency_issues(entry)
            if issues:
                message = "依赖快照链不完整，缺失依赖：" + "、".join(missing)
                self._audit(entry, "恢复快照", message, ok=False)
                return Outcome(ok=False, message=message, entry=None, missing_dependencies=missing)

            if str(entry.get("status") or "") != "已生效":
                message = "快照尚未生效，不能恢复；请先按链生效后再恢复"
                self._audit(entry, "恢复快照", message, ok=False)
                return Outcome(ok=False, message=message)

            if _canonical_restore_check(entry.get("恢复校验")) != RESTORE_CHECK_PASSED:
                message = "快照恢复校验尚未通过，不能恢复"
                self._audit(entry, "恢复快照", message, ok=False)
                return Outcome(ok=False, message=message)

            blockers = self._blocking_references(entry)
            if blockers:
                message = "目标表存在正在写入的引用，恢复被阻止：" + "、".join(blockers)
                self._audit(entry, "恢复快照", message, ok=False)
                return Outcome(ok=False, message=message, blockers=blockers)

            target = str(entry.get("目标表名") or "")
            target_state = getattr(store, "snapshot_target_versions", None)
            if target_state is None:
                target_state = {}
                setattr(store, "snapshot_target_versions", target_state)
            version = int(target_state.get(target, {}).get("版本", 0)) + 1
            target_state[target] = {
                "目标表名": target,
                "当前快照编号": str(entry.get("快照编号") or ""),
                "版本": version,
                "恢复时间": _now(),
            }

            receipt = {
                "回执编号": f"RESTORE-{entry_id:04d}",
                "快照记录ID": entry_id,
                "快照编号": str(entry.get("快照编号") or ""),
                "目标表名": target,
                "恢复时间": _now(),
                "恢复校验": RESTORE_CHECK_PASSED,
                "依赖快照": _split_dependencies(entry.get("依赖快照")),
                "目标表版本": version,
                "操作结果": "已恢复",
                "审计日志": "",
            }
            entry["恢复回执"] = receipt["回执编号"]
            entry["恢复时间"] = receipt["恢复时间"]
            entry["恢复次数"] = int(entry.get("恢复次数", 0)) + 1
            audit = self._audit(entry, "恢复快照", f"快照已恢复，目标表 {target} 版本 {version}", ok=True)
            receipt["审计日志"] = str(audit.get("日志编号") or "")
            self._receipts()[int(entry_id)] = receipt
            return Outcome(ok=True, message="快照已恢复", entry=entry, receipt=receipt, applied=True)


service = SnapshotService()
