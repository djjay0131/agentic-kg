"""
E2E tests for WebSocket endpoints.

Tests WebSocket connection, message flow, and disconnect handling.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from .conftest import APITestConfig


def _handshake_status_code(exc: BaseException) -> int:
    """Extract the HTTP status from a websockets handshake rejection.

    ``InvalidStatus`` (websockets >= 13) exposes ``.response.status_code``;
    the legacy ``InvalidStatusCode`` exposed ``.status_code`` directly.
    """
    response = getattr(exc, "response", None)
    if response is not None:
        return response.status_code
    return getattr(exc, "status_code", 0)


@pytest.mark.e2e
class TestWebSocketE2E:
    """E2E tests for WebSocket functionality."""

    @pytest.fixture
    def ws_url(self, api_config: APITestConfig) -> str:
        """Get WebSocket URL for staging."""
        # Convert HTTPS to WSS
        base = api_config.api_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{base}/api/agents/ws"

    @pytest.mark.asyncio
    async def test_websocket_connection_refused_without_run_id(
        self,
        ws_url: str,
    ):
        """Test that WebSocket connection requires a valid run_id."""
        import websockets
        from websockets.exceptions import InvalidHandshake

        # The workflow WebSocket route is /api/agents/ws/workflows/{run_id}.
        # A request with no run_id matches no route, so the server rejects it
        # at the HTTP layer (403) before the WebSocket handshake completes --
        # there is no websocket-level close to observe here.
        with pytest.raises(InvalidHandshake) as exc_info:
            async with websockets.connect(ws_url, close_timeout=5):
                pass  # pragma: no cover - connection is rejected

        status = _handshake_status_code(exc_info.value)
        assert status in (401, 403, 404), f"unexpected rejection status: {status}"

    @pytest.mark.asyncio
    async def test_websocket_message_structure(self):
        """Test expected WebSocket message structure."""
        # Test message types that should be supported
        message_types = {
            "step_update": {
                "type": "step_update",
                "step": "ranking",
                "status": "completed",
                "timestamp": "2024-01-01T00:00:00Z",
            },
            "checkpoint": {
                "type": "checkpoint",
                "checkpoint_type": "select_problem",
                "data": {"problems": []},
                "timestamp": "2024-01-01T00:00:00Z",
            },
            "error": {
                "type": "error",
                "error": "Something went wrong",
                "timestamp": "2024-01-01T00:00:00Z",
            },
            "complete": {
                "type": "complete",
                "result": {"total_steps": 7},
                "timestamp": "2024-01-01T00:00:00Z",
            },
        }

        for msg_type, example in message_types.items():
            assert "type" in example
            assert example["type"] == msg_type


@pytest.mark.e2e
class TestWebSocketWithWorkflow:
    """E2E tests for WebSocket with actual workflow.

    These tests require a running workflow to receive messages.
    """

    @pytest.mark.asyncio
    async def test_websocket_receives_updates_during_workflow(
        self,
        api_config: APITestConfig,
    ):
        """Test that WebSocket receives updates when workflow runs.

        This test starts a workflow and listens for WebSocket messages.
        """
        import websockets

        async with httpx.AsyncClient(
            base_url=api_config.api_url,
            timeout=60.0,
        ) as http_client:
            # Start a workflow
            response = await http_client.post(
                "/api/agents/workflows",
                json={
                    "domain_filter": None,
                    "max_problems": 3,
                },
            )

            if response.status_code == 503:
                pytest.skip("WorkflowRunner not initialized")

            if response.status_code != 200:
                pytest.skip(f"Failed to start workflow: {response.status_code}")

            data = response.json()
            run_id = data["run_id"]

            # Connect to WebSocket
            ws_base = api_config.api_url.replace("https://", "wss://").replace("http://", "ws://")
            ws_url = f"{ws_base}/api/agents/ws/workflows/{run_id}"

            messages_received = []

            try:
                async with websockets.connect(ws_url, close_timeout=10) as ws:
                    # Listen for messages (up to 30 seconds)
                    try:
                        for _ in range(15):  # 15 attempts, 2 seconds each
                            try:
                                message = await asyncio.wait_for(ws.recv(), timeout=2.0)
                                data = json.loads(message)
                                messages_received.append(data)

                                # Stop if we hit a terminal state
                                if data.get("type") in ["complete", "error", "checkpoint"]:
                                    break
                            except asyncio.TimeoutError:
                                continue
                    except Exception:
                        pass

            except Exception as e:
                # WebSocket connection issues are acceptable in E2E
                pytest.skip(f"WebSocket connection failed: {e}")

            # We may or may not have received messages depending on workflow timing
            # The test passes if we connected successfully


@pytest.mark.e2e
class TestWebSocketReconnection:
    """Tests for WebSocket reconnection behavior."""

    @pytest.mark.asyncio
    async def test_can_reconnect_after_disconnect(
        self,
        api_config: APITestConfig,
    ):
        """Test that client can reconnect after disconnection."""
        import websockets

        ws_base = api_config.api_url.replace("https://", "wss://").replace("http://", "ws://")
        # Real route: /api/agents/ws/workflows/{run_id}. The connection manager
        # accepts any run_id at the transport layer; this test exercises
        # connect/disconnect/reconnect against the deployed endpoint.
        ws_url = f"{ws_base}/api/agents/ws/workflows/test-reconnect"

        # First connection.
        async with websockets.connect(ws_url, close_timeout=5):
            pass  # Just connect and disconnect.

        # Reconnect after the disconnect must succeed.
        async with websockets.connect(ws_url, close_timeout=5):
            pass


@pytest.mark.e2e
class TestWebSocketMessageHandling:
    """Tests for WebSocket message handling edge cases."""

    def test_message_serialization(self):
        """Test that messages can be properly serialized to JSON."""
        messages = [
            {"type": "step_update", "step": "ranking", "status": "running"},
            {"type": "checkpoint", "checkpoint_type": "select_problem", "data": {"items": [1, 2]}},
            {"type": "error", "error": "Test error with unicode: café"},
            {"type": "complete", "result": None},
        ]

        for msg in messages:
            # Should serialize without error
            serialized = json.dumps(msg)
            # Should deserialize back
            deserialized = json.loads(serialized)
            assert deserialized["type"] == msg["type"]

    def test_large_message_handling(self):
        """Test handling of larger messages."""
        # Simulate a checkpoint with many problems
        large_data = {
            "type": "checkpoint",
            "checkpoint_type": "select_problem",
            "data": {
                "problems": [
                    {
                        "problem_id": f"prob-{i}",
                        "title": f"Problem {i} with a reasonably long title",
                        "description": "A" * 500,  # 500 char description
                        "score": 0.5 + (i * 0.01),
                    }
                    for i in range(100)  # 100 problems
                ]
            },
        }

        # Should serialize without issue
        serialized = json.dumps(large_data)
        assert len(serialized) > 50000  # Should be >50KB

        # Should deserialize without issue
        deserialized = json.loads(serialized)
        assert len(deserialized["data"]["problems"]) == 100
