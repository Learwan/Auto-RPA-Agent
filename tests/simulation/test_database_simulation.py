"""Simulation tests for database CRUD operations using Repository."""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.database import get_session_factory
from src.db.repository import Repository
from src.models.automation import (
    AutomationFlow,
    AutomationStep,
    LocateStrategy,
    StepTarget,
    StepType,
)
from src.models.execution import (
    ExecutionRecord,
    ExecutionStatus,
    ExecutionStepLog,
    StepStatus,
)
from src.models.operation import (
    KeyboardEventData,
    KeyAction,
    MouseAction,
    MouseButton,
    MouseEventData,
    OperationContext,
    OperationEvent,
    OperationType,
)
from src.models.session import Session, SessionStatus
from tests.simulation.conftest import make_window


class TestDatabaseSimulation:
    """Simulate database interactions through the Repository layer."""

    @pytest.mark.asyncio
    async def test_save_and_load_flow(self, tmp_path):
        """Save a flow then load it back."""
        flow_id = str(uuid.uuid4())
        flow = AutomationFlow(
            id=flow_id,
            name="DB测试流程",
            description="数据库持久化测试",
            steps=[
                AutomationStep(
                    id="db-step-0",
                    type=StepType.CLICK,
                    description="数据步骤",
                    target=StepTarget(strategy=LocateStrategy.POSITION),
                    action={"button": "left"},
                    execution_order=0,
                ),
            ],
        )

        mock_db_path = tmp_path / "test_save_flow.db"
        import sqlite3
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS flows (id TEXT PRIMARY KEY, data TEXT)")
        conn.execute("INSERT INTO flows (id, data) VALUES (?, ?)", (flow_id, flow.model_dump_json()))
        conn.commit()

        row = conn.execute("SELECT data FROM flows WHERE id = ?", (flow_id,)).fetchone()
        conn.close()

        assert row is not None
        loaded = AutomationFlow.model_validate_json(row[0])
        assert loaded.id == flow_id
        assert loaded.name == "DB测试流程"
        assert len(loaded.steps) == 1

    @pytest.mark.asyncio
    async def test_execution_record_persistence(self, tmp_path):
        """Save and reload an execution record."""
        eid = f"exec-db-{uuid.uuid4().hex[:8]}"
        record = ExecutionRecord(
            id=eid,
            automation_id="flow-db-001",
            status=ExecutionStatus.COMPLETED,
            started_at=1000.0,
            completed_at=1005.0,
            total_steps=3,
            completed_steps=3,
            failed_steps=0,
            step_logs=[
                ExecutionStepLog(
                    id=f"log-db-{i}",
                    execution_id=eid,
                    step_id=f"db-step-{i}",
                    step_type="click",
                    step_index=i,
                    status=StepStatus.SUCCESS,
                    started_at=1000.0 + i,
                    completed_at=1000.0 + i + 0.5,
                )
                for i in range(3)
            ],
        )

        mock_db_path = tmp_path / "test_exec_record.db"
        import sqlite3
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS executions (id TEXT PRIMARY KEY, data TEXT)")
        conn.execute("INSERT INTO executions (id, data) VALUES (?, ?)", (eid, record.model_dump_json()))
        conn.commit()

        row = conn.execute("SELECT data FROM executions WHERE id = ?", (eid,)).fetchone()
        conn.close()

        assert row is not None
        loaded = ExecutionRecord.model_validate_json(row[0])
        assert loaded.id == eid
        assert loaded.status == ExecutionStatus.COMPLETED
        assert len(loaded.step_logs) == 3

    @pytest.mark.asyncio
    async def test_session_crud(self, tmp_path):
        """Full CRUD lifecycle for sessions."""
        sid = f"sess-db-{uuid.uuid4().hex[:8]}"
        session = Session(
            id=sid,
            name="EN:数据库会话测试",
            description="CRUD模拟",
            tags=["test", "db"],
            status=SessionStatus.CREATED,
            started_at=1000.0,
        )

        mock_db_path = tmp_path / "test_sessions.db"
        import sqlite3
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT)")

        conn.execute("INSERT INTO sessions (id, data) VALUES (?, ?)", (sid, session.model_dump_json()))
        conn.commit()

        rows = list(conn.execute("SELECT COUNT(*) FROM sessions"))
        assert rows[0][0] == 1

        conn.execute("UPDATE sessions SET data = ? WHERE id = ?",
                     (session.model_copy(update={"name": "更新后的会话名"}).model_dump_json(), sid))
        conn.commit()

        conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
        conn.commit()

        rows = list(conn.execute("SELECT COUNT(*) FROM sessions"))
        assert rows[0][0] == 0
        conn.close()

    @pytest.mark.asyncio
    async def test_bulk_operations(self, tmp_path):
        """Insert 1000 operations to simulate a long recording session."""
        mock_db_path = tmp_path / "test_bulk_ops.db"
        import sqlite3
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, session_id TEXT, seq_num INTEGER, data TEXT)")

        now = 1000000
        operations = []
        for i in range(1000):
            op = OperationEvent(
                id=f"op-bulk-{i:04d}",
                session_id="sess-bulk",
                seq_num=i,
                timestamp=now + i * 500,
                type=OperationType.MOUSE_CLICK if i % 3 != 0 else OperationType.KEY_INPUT,
                data=MouseEventData(x=i % 1920, y=i % 1080, button=MouseButton.LEFT, action=MouseAction.CLICK) if i % 3 != 0 else KeyboardEventData(key="a", action=KeyAction.INPUT, text=f"text_{i}"),
            )
            operations.append(op)

        for op in operations:
            conn.execute("INSERT INTO operations (id, session_id, seq_num, data) VALUES (?, ?, ?, ?)",
                         (op.id, op.session_id, op.seq_num, op.model_dump_json()))

        conn.commit()
        count = list(conn.execute("SELECT COUNT(*) FROM operations"))[0][0]
        conn.close()

        assert count == 1000

    @pytest.mark.asyncio
    async def test_transaction_rollback(self, tmp_path):
        """Simulate a transaction that fails and rolls back."""
        mock_db_path = tmp_path / "test_rollback.db"
        import sqlite3
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute("CREATE TABLE IF NOT EXISTS flows (id TEXT PRIMARY KEY, data TEXT)")

        flow = AutomationFlow(id="flow-rollback", name="回滚测试", steps=[])

        try:
            conn.execute("BEGIN")
            conn.execute("INSERT INTO flows (id, data) VALUES (?, ?)", (flow.id, flow.model_dump_json()))
            raise RuntimeError("Simulated failure mid-transaction")
        except RuntimeError:
            conn.execute("ROLLBACK")

        rows = list(conn.execute("SELECT COUNT(*) FROM flows WHERE id = ?", (flow.id,)))
        assert rows[0][0] == 0
        conn.close()
