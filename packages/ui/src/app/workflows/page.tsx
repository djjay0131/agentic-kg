'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, WorkflowStatus } from '@/lib/api';
import { Play, Eye, Trash2 } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';

export default function WorkflowsPage() {
  const queryClient = useQueryClient();
  const [domain, setDomain] = useState('');

  const { data: workflows, isLoading } = useQuery({
    queryKey: ['workflows'],
    queryFn: () => api.listWorkflows(),
    refetchInterval: 5000,
  });

  const startMutation = useMutation({
    mutationFn: () =>
      api.startWorkflow({
        domain_filter: domain || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] });
    },
  });

  const statusColor = (status: string) => {
    switch (status) {
      case 'completed': return 'bg-green-100 text-green-700';
      case 'running': return 'bg-blue-100 text-blue-700';
      case 'awaiting_human': return 'bg-yellow-100 text-yellow-700';
      case 'failed': return 'bg-red-100 text-red-700';
      case 'cancelled': return 'bg-gray-100 text-gray-600';
      default: return 'bg-gray-100 text-gray-600';
    }
  };

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Research Workflows</h1>
        <div className="flex items-center gap-3">
          <input
            type="text"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            placeholder="Domain filter (optional)"
            className="border rounded-lg px-3 py-2 text-sm w-48"
          />
          <button
            onClick={() => startMutation.mutate()}
            disabled={startMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 text-sm font-medium"
          >
            <Play size={16} />
            {startMutation.isPending ? 'Starting...' : 'New Workflow'}
          </button>
        </div>
      </div>

      {startMutation.isError && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          Failed to start workflow: {startMutation.error?.message}
        </div>
      )}

      {isLoading ? (
        <div className="text-center py-12 text-gray-500">Loading workflows...</div>
      ) : !workflows?.length ? (
        <div className="text-center py-12 text-gray-500">
          <p className="text-lg mb-2">No workflows yet</p>
          <p className="text-sm">Start a new workflow to begin researching problems.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {workflows.map((w) => (
            <div
              key={w.run_id}
              className="bg-white border rounded-lg p-4 flex items-center justify-between hover:shadow-sm transition-shadow"
            >
              <div className="flex-1">
                <div className="flex items-center gap-3 mb-1">
                  <code className="text-sm text-gray-500">{w.run_id.slice(0, 8)}...</code>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusColor(w.status)}`}>
                    {w.status}
                  </span>
                </div>
                <div className="text-sm text-gray-600">
                  Step: <span className="font-medium">{w.current_step || 'initializing'}</span>
                  {' | '}
                  {w.completed_steps}/{w.total_steps} steps
                </div>
                <div className="text-xs text-gray-400 mt-1">
                  Started: {new Date(w.created_at).toLocaleString()}
                </div>
              </div>
              <Link
                href={`/workflows/${w.run_id}`}
                className="flex items-center gap-1 px-3 py-2 text-sm text-primary-600 hover:bg-primary-50 rounded-lg"
              >
                <Eye size={16} />
                View
              </Link>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
