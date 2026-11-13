"""版本快照接口：维护快照记录，覆盖提交创建、恢复校验、生效、恢复、标记过期等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.snapshot import CORE_ACTIONS, SnapshotService

router = APIRouter(prefix="/api/snapshot", tags=["版本快照"])

service = SnapshotService()

LIST_FIELDS = ["快照编号", "目标表名", "快照时间", "快照大小", "存储介质", "过期策略", "恢复校验", "依赖快照"]
STATUSES = ["草稿", "待创建", "已生效", "已过期"]
ACTIONS = sorted(CORE_ACTIONS)


def _result(outcome) -> ActionResult:
    return ActionResult(
        ok=outcome.ok,
        message=outcome.message,
        entry=outcome.entry,
        missing_dependencies=outcome.missing_dependencies,
        blockers=outcome.blockers,
        receipt=outcome.receipt,
        duplicate=outcome.duplicate,
        applied=outcome.applied,
    )


@router.get("/meta", response_model=dict)
def get_meta() -> dict[str, Any]:
    """返回版本快照的状态序列与可用动作，供前端渲染依赖链与恢复入口。"""
    return {
        "statuses": STATUSES,
        "actions": ["提交创建", "校验恢复", "生效快照", "恢复快照", "标记过期"],
        "action_aliases": ACTIONS,
        "restore_actions": ["校验恢复", "恢复快照"],
        "dependency_rule": "依赖快照必须全部为已生效，链上任一缺失或未生效都不能生效与恢复",
        "restore_rule": "先校验恢复，再恢复快照；恢复前检查目标表的聚合视图与回填作业写入引用",
    }


@router.get("/receipts", response_model=PageResult[dict])
def list_receipts(page: int = 1, size: int = 20) -> PageResult[dict]:
    """读取恢复回执清单；重复恢复请求命中的是同一张回执。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items = service.list_receipts()
    total = len(items)
    start = max(page - 1, 0) * size
    return PageResult(items=items[start:start + size], total=total, page=page, size=size)


@router.get("/receipts/{entry_id}", response_model=dict)
def get_receipt(entry_id: int) -> dict[str, Any]:
    """读取指定快照的恢复回执；未恢复过的快照返回 404 说明。"""
    receipt = service.get_receipt(entry_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail=f"快照记录 {entry_id} 尚无恢复回执")
    return receipt


@router.get("/targets", response_model=PageResult[dict])
def list_target_states(page: int = 1, size: int = 20) -> PageResult[dict]:
    """读取所有已恢复的目标表版本；同一张表只保留最后一次恢复结果。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items = service.list_target_states()
    total = len(items)
    start = max(page - 1, 0) * size
    return PageResult(items=items[start:start + size], total=total, page=page, size=size)


@router.get("/targets/{table_name}", response_model=dict)
def get_target_state(table_name: str) -> dict[str, Any]:
    """读取目标表当前生效的快照版本，用于确认恢复结果没有重复变更。"""
    record = service.get_target_state(table_name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"目标表 {table_name} 尚无恢复记录")
    return record


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按快照编号检索"),
    status: str | None = Query(default=None, description="草稿、待创建、已生效、已过期"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按快照编号与状态过滤版本快照列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出版本快照清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "snapshot", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条快照记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"快照记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条快照记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="快照记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条快照记录执行状态动作与恢复动作；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    return _result(service.perform_action(entry_id, action))


@router.post("/{entry_id}/restore", response_model=ActionResult)
def restore_entry(entry_id: int, payload: EntryPayload | None = None) -> ActionResult:
    """恢复快照：依赖链完整、校验通过、目标表无写入引用时才成功，重复调用返回同一回执。"""
    return _result(service.restore_snapshot(entry_id))


@router.post("/{entry_id}/verify", response_model=ActionResult)
def verify_entry(entry_id: int, payload: EntryPayload | None = None) -> ActionResult:
    """执行恢复校验：依赖链任一缺失都只列出缺失项，不推进校验状态。"""
    return _result(service.check_snapshot(entry_id))
