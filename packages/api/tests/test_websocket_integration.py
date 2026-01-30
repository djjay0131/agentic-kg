"""
Integration tests for WebSocket infrastructure.

Tests connection management, message broadcast, and disconnect handling.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from agentic_kg_api.websocket import ConnectionManager


@pytest.fixture
def manager():
    return ConnectionManager()


@pytest.fixture
def mock_ws():
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


class TestConnectionManager:
    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self, manager, mock_ws):
        await manager.connect(mock_ws, "run-1")
        assert manager.has_connections("run-1")
        manager.disconnect(mock_ws, "run-1")
        assert not manager.has_connections("run-1")

    @pytest.mark.asyncio
    async def test_broadcast(self, manager, mock_ws):
        await manager.connect(mock_ws, "run-1")
        await manager.broadcast("run-1", {"type": "test"})
        mock_ws.send_json.assert_awaited_once_with({"type": "test"})

    @pytest.mark.asyncio
    async def test_broadcast_no_connections(self, manager):
        # Should not raise
        await manager.broadcast("no-such-run", {"type": "test"})

    @pytest.mark.asyncio
    async def test_broadcast_cleans_broken_connections(self, manager):
        ws_good = AsyncMock()
        ws_bad = AsyncMock()
        ws_bad.send_json.side_effect = Exception("closed")

        await manager.connect(ws_good, "run-1")
        await manager.connect(ws_bad, "run-1")
        await manager.broadcast("run-1", {"type": "test"})

        # ws_bad should be removed
        assert manager.has_connections("run-1")
        # Only ws_good should remain
        assert len(manager._connections["run-1"]) == 1

    @pytest.mark.asyncio
    async def test_multiple_runs(self, manager):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await manager.connect(ws1, "run-1")
        await manager.connect(ws2, "run-2")

        await manager.broadcast("run-1", {"msg": "a"})
        ws1.send_json.assert_awaited_once_with({"msg": "a"})
        ws2.send_json.assert_not_awaited()


class TestSendHelpers:
    @pytest.mark.asyncio
    async def test_send_step_update(self, manager, mock_ws):
        await manager.connect(mock_ws, "run-1")
        await manager.send_step_update("run-1", step="ranking", status="completed")
        mock_ws.send_json.assert_awaited_once()
        msg = mock_ws.send_json.call_args[0][0]
        assert msg["type"] == "step_update"
        assert msg["step"] == "ranking"

    @pytest.mark.asyncio
    async def test_send_checkpoint(self, manager, mock_ws):
        await manager.connect(mock_ws, "run-1")
        await manager.send_checkpoint("run-1", "select_problem", {"problems": []})
        msg = mock_ws.send_json.call_args[0][0]
        assert msg["type"] == "checkpoint"
        assert msg["checkpoint_type"] == "select_problem"

    @pytest.mark.asyncio
    async def test_send_error(self, manager, mock_ws):
        await manager.connect(mock_ws, "run-1")
        await manager.send_error("run-1", "something failed")
        msg = mock_ws.send_json.call_args[0][0]
        assert msg["type"] == "error"
        assert msg["error"] == "something failed"

    @pytest.mark.asyncio
    async def test_send_complete(self, manager, mock_ws):
        await manager.connect(mock_ws, "run-1")
        await manager.send_complete("run-1", {"total_steps": 7})
        msg = mock_ws.send_json.call_args[0][0]
        assert msg["type"] == "complete"


class TestEventBus:
    @pytest.mark.asyncio
    async def test_event_bus_emit(self):
        from agentic_kg.agents.events import (
            WorkflowEvent,
            WorkflowEventBus,
            WorkflowEventType,
        )

        bus = WorkflowEventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(handler)
        event = WorkflowEvent(
            event_type=WorkflowEventType.STEP_COMPLETED,
            run_id="run-1",
            step="ranking",
        )
        await bus.emit(event)
        assert len(received) == 1
        assert received[0].step == "ranking"

    @pytest.mark.asyncio
    async def test_event_bus_handler_error_doesnt_propagate(self):
        from agentic_kg.agents.events import (
            WorkflowEvent,
            WorkflowEventBus,
            WorkflowEventType,
        )

        bus = WorkflowEventBus()

        async def bad_handler(event):
            raise RuntimeError("oops")

        bus.subscribe(bad_handler)
        event = WorkflowEvent(
            event_type=WorkflowEventType.STEP_COMPLETED,
            run_id="run-1",
            step="ranking",
        )
        # Should not raise
        await bus.emit(event)

    @pytest.mark.asyncio
    async def test_event_bus_unsubscribe(self):
        from agentic_kg.agents.events import (
            WorkflowEvent,
            WorkflowEventBus,
            WorkflowEventType,
        )

        bus = WorkflowEventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(handler)
        bus.unsubscribe(handler)
        await bus.emit(
            WorkflowEvent(
                event_type=WorkflowEventType.STEP_COMPLETED,
                run_id="x",
                step="y",
            )
        )
        assert len(received) == 0
