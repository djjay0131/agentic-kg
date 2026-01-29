"""
WebSocket infrastructure for real-time workflow updates.

Manages WebSocket connections per workflow run_id and broadcasts
step completions and checkpoint arrivals to connected clients.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections grouped by workflow run_id."""

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, run_id: str) -> None:
        """Accept and register a WebSocket connection for a workflow."""
        await websocket.accept()
        if run_id not in self._connections:
            self._connections[run_id] = []
        self._connections[run_id].append(websocket)
        logger.info(f"WebSocket connected for workflow {run_id}")

    def disconnect(self, websocket: WebSocket, run_id: str) -> None:
        """Remove a WebSocket connection."""
        if run_id in self._connections:
            self._connections[run_id] = [
                ws for ws in self._connections[run_id] if ws is not websocket
            ]
            if not self._connections[run_id]:
                del self._connections[run_id]
        logger.info(f"WebSocket disconnected for workflow {run_id}")

    async def broadcast(self, run_id: str, message: dict[str, Any]) -> None:
        """Send a message to all clients connected to a workflow."""
        connections = self._connections.get(run_id, [])
        disconnected = []
        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)

        # Clean up broken connections
        for ws in disconnected:
            self.disconnect(ws, run_id)

    async def send_step_update(
        self, run_id: str, step: str, status: str, data: dict[str, Any] | None = None
    ) -> None:
        """Broadcast a workflow step update."""
        await self.broadcast(
            run_id,
            {
                "type": "step_update",
                "step": step,
                "status": status,
                "data": data or {},
            },
        )

    async def send_checkpoint(
        self, run_id: str, checkpoint_type: str, data: dict[str, Any]
    ) -> None:
        """Broadcast a checkpoint arrival requiring human decision."""
        await self.broadcast(
            run_id,
            {
                "type": "checkpoint",
                "checkpoint_type": checkpoint_type,
                "data": data,
            },
        )

    async def send_error(self, run_id: str, error: str) -> None:
        """Broadcast an error."""
        await self.broadcast(
            run_id,
            {"type": "error", "error": error},
        )

    async def send_complete(self, run_id: str, summary: dict[str, Any]) -> None:
        """Broadcast workflow completion."""
        await self.broadcast(
            run_id,
            {"type": "complete", "summary": summary},
        )

    def has_connections(self, run_id: str) -> bool:
        """Check if any clients are connected for a workflow."""
        return bool(self._connections.get(run_id))


# Singleton instance
ws_manager = ConnectionManager()
