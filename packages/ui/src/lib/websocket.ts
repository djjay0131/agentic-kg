/**
 * WebSocket hook for real-time workflow updates.
 */

import { useEffect, useRef, useState, useCallback } from 'react';

const WS_URL = (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000')
  .replace(/^http/, 'ws');

export interface WorkflowMessage {
  type: 'step_update' | 'checkpoint' | 'error' | 'complete' | 'pong';
  step?: string;
  status?: string;
  checkpoint_type?: string;
  data?: Record<string, unknown>;
  error?: string;
  summary?: Record<string, unknown>;
}

export function useWorkflowWebSocket(runId: string | null) {
  const wsRef = useRef<WebSocket | null>(null);
  const [messages, setMessages] = useState<WorkflowMessage[]>([]);
  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<WorkflowMessage | null>(null);

  const connect = useCallback(() => {
    if (!runId) return;

    const ws = new WebSocket(`${WS_URL}/api/agents/ws/workflows/${runId}`);

    ws.onopen = () => {
      setConnected(true);
    };

    ws.onmessage = (event) => {
      try {
        const msg: WorkflowMessage = JSON.parse(event.data);
        setLastMessage(msg);
        if (msg.type !== 'pong') {
          setMessages((prev) => [...prev, msg]);
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      setConnected(false);
    };

    ws.onerror = () => {
      setConnected(false);
    };

    wsRef.current = ws;
  }, [runId]);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  const sendPing = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send('ping');
    }
  }, []);

  return { messages, lastMessage, connected, sendPing };
}
