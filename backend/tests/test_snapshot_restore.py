"""版本快照恢复能力测试：依赖链、单向流转、写入引用与并发幂等。"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.seed import SEED_ROWS
from app.services.snapshot import SnapshotService
from app.store import store

A = 1  # SNAP-0001 依赖 SNAP-0002，目标表 dws_order_agg
B = 2  # SNAP-0002 依赖 SNAP-0003
C = 3  # SNAP-0003 已生效
EXPIRED = 4  # SNAP-0004 已过期


@pytest.fixture(autouse=True)
def fresh_store() -> None:
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    for attribute in ("snapshot_receipts", "snapshot_target_versions"):
        if hasattr(store, attribute):
            setattr(store, attribute, {})


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def service() -> SnapshotService:
    return SnapshotService()


def action(client: TestClient, entry_id: int, name: str):
    return client.post(f"/api/snapshot/{entry_id}/actions", json={"values": {"action": name}})


def test_generated_chain_blocks_direct_effect_and_lists_missing_dependency(client: TestClient):
    response = action(client, A, "生效快照")
    payload = response.json()
    assert payload["ok"] is False
    assert any("SNAP-0002" in item for item in payload["missing_dependencies"])
    assert payload["missing_dependencies"]
    assert client.get(f"/api/snapshot/targets/dws_order_agg").status_code == 404
    assert client.get(f"/api/snapshot/{A}").json()["status"] == "待创建"


def test_check_chain_rejects_until_dependency_effective(client: TestClient):
    assert action(client, A, "校验恢复").json()["ok"] is False
    assert action(client, B, "校验恢复").json()["ok"] is True
    assert action(client, B, "生效快照").json()["ok"] is True
    assert action(client, A, "校验恢复").json()["ok"] is True
    assert action(client, A, "生效快照").json()["ok"] is True


def test_restore_blocked_by_backfill_then_succeeds_after_sync(client: TestClient):
    assert action(client, B, "校验恢复").json()["ok"] is True
    assert action(client, B, "生效快照").json()["ok"] is True
    assert action(client, A, "校验恢复").json()["ok"] is True
    assert action(client, A, "生效快照").json()["ok"] is True

    blocked = client.post(f"/api/snapshot/{A}/restore").json()
    assert blocked["ok"] is False
    assert blocked["blockers"]
    assert client.get(f"/api/snapshot/targets/dws_order_agg").status_code == 404

    backfill_id = next(
        row["id"] for row in store.rows("backfill") if row.get("作业编号") == "BACK-0004"
    )
    client.post(f"/api/backfill/{backfill_id}/actions", json={"values": {"action": "完成同步"}})

    restored = client.post(f"/api/snapshot/{A}/restore").json()
    assert restored["ok"] is True
    assert restored["receipt"]["快照编号"] == "SNAP-0001"
    target = client.get("/api/snapshot/targets/dws_order_agg").json()
    assert target["当前快照编号"] == "SNAP-0001"
    assert target["版本"] == 1


def test_concurrent_restore_only_one_changes_target(service: SnapshotService):
    service.check_snapshot(B)
    service.perform_action(B, "生效快照")
    service.check_snapshot(A)
    service.perform_action(A, "生效快照")
    backfill_id = next(row["id"] for row in store.rows("backfill") if row.get("作业编号") == "BACK-0004")
    for row in store.rows("backfill"):
        if row["id"] == backfill_id:
            row["status"] = "已完成"

    outcomes = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        barrier.wait()
        outcomes.append(SnapshotService().restore_snapshot(A))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(outcomes) == 2
    assert sum(1 for outcome in outcomes if outcome.ok and not outcome.duplicate) == 1
    assert sum(1 for outcome in outcomes if outcome.duplicate) == 1
    assert all(outcome.receipt and outcome.receipt["回执编号"] == "RESTORE-0001" for outcome in outcomes)
    target = getattr(store, "snapshot_target_versions")["dws_order_agg"]
    assert target["版本"] == 1


def test_repeated_restore_returns_same_receipt(client: TestClient):
    action(client, B, "校验恢复")
    action(client, B, "生效快照")
    action(client, A, "校验恢复")
    action(client, A, "生效快照")
    for row in store.rows("backfill"):
        if row.get("作业编号") == "BACK-0004":
            row["status"] = "已完成"

    first = client.post(f"/api/snapshot/{A}/restore").json()
    second = client.post(f"/api/snapshot/{A}/restore").json()
    assert first["ok"] and second["ok"]
    assert first["receipt"] == second["receipt"]
    assert second["duplicate"] is True
    assert len(service_receipts()) == 1


def service_receipts():
    return SnapshotService().list_receipts()


def test_expired_snapshot_cannot_restore_but_keeps_audit(client: TestClient):
    before = len(store.rows("audit"))
    response = client.post(f"/api/snapshot/{EXPIRED}/restore").json()
    assert response["ok"] is False
    assert client.get(f"/api/snapshot/{EXPIRED}").json()["status"] == "已过期"
    assert len(store.rows("audit")) == before + 1
    assert client.get(f"/api/snapshot/{EXPIRED}/receipts").status_code == 404


def test_historical_snapshot_keeps_records_after_expire(service: SnapshotService):
    """恢复后标记过期：历史快照与回执、审计都保留，过期后不能再参与恢复。"""
    service.check_snapshot(B)
    service.perform_action(B, "生效快照")
    service.check_snapshot(A)
    service.perform_action(A, "生效快照")
    for row in store.rows("backfill"):
        if row.get("作业编号") == "BACK-0004":
            row["status"] = "已完成"

    restored = service.restore_snapshot(A)
    assert restored.ok and restored.applied is True
    receipt = restored.receipt
    audit_count = len(store.rows("audit"))

    assert service.perform_action(A, "标记过期").ok is True
    assert service.get_receipt(A) == receipt
    assert service.get_entry(A)["恢复回执"] == receipt["回执编号"]
    assert len(store.rows("audit")) == audit_count + 1
    assert service.restore_snapshot(A).ok is False
    assert service.get_target_state("dws_order_agg")["版本"] == 1


def test_active_gold_view_blocks_restore(client: TestClient):
    service = SnapshotService()
    entry, _ = service.create_entry(
        {"快照编号": "SNAP-0006", "目标表名": "gold_active_target", "快照时间": "2026-10-01"}
    )
    service.perform_action(entry["id"], "提交创建")
    service.check_snapshot(entry["id"])
    service.perform_action(entry["id"], "生效快照")

    store.rows("gold").append(
        {
            "id": 99,
            "status": "已启用",
            "pending": False,
            "abnormal": False,
            "视图编号": "GOLD-0099",
            "上游清洗表": "gold_active_target",
        }
    )
    blocked = client.post(f"/api/snapshot/{entry['id']}/restore").json()
    assert blocked["ok"] is False
    assert any("GOLD-0099" in item for item in blocked["blockers"])

    for row in store.rows("gold"):
        if row.get("id") == 99:
            row["status"] = "已停用"
    restored = client.post(f"/api/snapshot/{entry['id']}/restore").json()
    assert restored["ok"] is True
    assert restored["applied"] is True


def test_check_rejects_unknown_restore_state(client: TestClient):
    created = client.post(
        "/api/snapshot",
        json={"values": {"快照编号": "SNAP-0007", "目标表名": "dws_new_table", "快照时间": "2026-10-01"}},
    ).json()["entry"]
    store.find("snapshot", created["id"])["恢复校验"] = "版本快照样例7"
    result = client.post(f"/api/snapshot/{created['id']}/verify").json()
    assert result["ok"] is False
    assert "单向序列" in result["message"]
    assert store.find("snapshot", created["id"])["恢复校验"] == "版本快照样例7"


def test_http_concurrent_restore_only_one_applies(client: TestClient):
    for snapshot_id, step in ((B, "校验恢复"), (B, "生效快照"), (A, "校验恢复"), (A, "生效快照")):
        assert action(client, snapshot_id, step).json()["ok"] is True
    for row in store.rows("backfill"):
        if row.get("作业编号") == "BACK-0004":
            row["status"] = "已完成"

    responses = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        barrier.wait()
        responses.append(client.post(f"/api/snapshot/{A}/restore").json())

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(response["ok"] for response in responses)
    assert sum(1 for response in responses if response["applied"]) == 1
    assert sum(1 for response in responses if response["duplicate"]) == 1
    assert {response["receipt"]["回执编号"] for response in responses} == {"RESTORE-0001"}
    assert client.get("/api/snapshot/targets/dws_order_agg").json()["版本"] == 1


def test_status_flow_is_one_way_and_no_skipping(client: TestClient):
    created = client.post(
        "/api/snapshot",
        json={
            "values": {
                "快照编号": "SNAP-0008",
                "目标表名": "dws_new_table",
                "快照时间": "2026-10-01",
                "依赖快照": "SNAP-0003",
            }
        },
    ).json()["entry"]
    new_id = created["id"]

    assert action(client, new_id, "生效快照").json()["ok"] is False
    assert action(client, new_id, "标记过期").json()["ok"] is False
    assert action(client, new_id, "提交创建").json()["ok"] is True
    assert action(client, new_id, "校验恢复").json()["ok"] is True
    assert action(client, new_id, "生效快照").json()["ok"] is True
    assert action(client, new_id, "提交创建").json()["ok"] is False
    assert action(client, new_id, "生效快照").json()["ok"] is True
    assert client.get(f"/api/snapshot/{new_id}").json()["status"] == "已生效"
    assert action(client, new_id, "标记过期").json()["ok"] is True
    assert action(client, new_id, "标记过期").json()["ok"] is True
    assert client.get(f"/api/snapshot/{new_id}").json()["status"] == "已过期"
    assert action(client, new_id, "生效快照").json()["ok"] is False
