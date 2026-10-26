'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Check, Pencil, X } from 'lucide-react';

const GATE_LABELS: Record<string, string> = {
  select_problem: 'Select problem',
  approve_proposal: 'Approve proposal',
  review_evaluation: 'Validate result',
};

function StatusBadge({ status }: { status: string }) {
  const classes: Record<string, string> = {
    running: 'bg-blue-100 text-blue-800',
    awaiting_gate: 'bg-yellow-100 text-yellow-800',
    completed: 'bg-green-100 text-green-800',
    partial: 'bg-orange-100 text-orange-800',
    failed: 'bg-red-100 text-red-800',
    stopped_by_budget: 'bg-orange-100 text-orange-800',
  };
  return (
    <span className={`badge ${classes[status] || 'bg-gray-100 text-gray-800'}`}>
      {status}
    </span>
  );
}

export default function ResearchPage() {
  const queryClient = useQueryClient();
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const gatesQuery = useQuery({
    queryKey: ['research-gates'],
    queryFn: () => api.listPendingGates(),
  });
  const cyclesQuery = useQuery({
    queryKey: ['research-cycles'],
    queryFn: () => api.listResearchCycles(20),
  });

  const decide = useMutation({
    mutationFn: ({
      cycleId,
      workflowRunId,
      gate,
      decision,
    }: {
      cycleId: string;
      workflowRunId: string;
      gate: string;
      decision: 'approve' | 'reject' | 'edit';
    }) => {
      const key = `${cycleId}/${workflowRunId}`;
      const note = notes[key] || '';
      let edited_data: Record<string, unknown> | undefined;
      if (decision === 'edit' && edits[key]) {
        try {
          edited_data = JSON.parse(edits[key]);
        } catch {
          throw new Error('Edited data must be valid JSON');
        }
      }
      return api.decideGate(cycleId, workflowRunId, {
        gate,
        decision,
        note,
        edited_data,
      });
    },
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ['research-gates'] });
      queryClient.invalidateQueries({ queryKey: ['research-cycles'] });
    },
    onError: (err: Error) => setError(err.message),
  });

  const gates = gatesQuery.data?.gates ?? [];
  const cycles = cyclesQuery.data?.cycles ?? [];

  return (
    <div className="max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Research Loop</h1>
        <p className="text-gray-500 mt-1">
          Scheduled cycles re-rank the graph and stop at human gates. Every gate
          is human by policy (ADR-0010); approving is the only path that lets a
          workflow write.
        </p>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          {error}
        </div>
      )}

      <h2 className="text-lg font-semibold text-gray-900 mb-3">Pending gates</h2>
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden mb-8">
        {gatesQuery.isLoading ? (
          <div className="p-8 text-center text-gray-500">Loading gates...</div>
        ) : gates.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            No gates are waiting. Run the cycle job or wait for the schedule.
          </div>
        ) : (
          <ul className="divide-y divide-gray-200">
            {gates.map((gate) => {
              const key = `${gate.cycle_id}/${gate.workflow_run_id}`;
              return (
                <li key={key} className="p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="badge bg-purple-100 text-purple-800">
                      {GATE_LABELS[gate.gate] || gate.gate}
                    </span>
                    <span className="font-mono text-xs text-gray-500">
                      {gate.workflow_run_id}
                    </span>
                  </div>
                  <p className="mt-2 text-sm text-gray-900">
                    {gate.problem_statement || gate.problem_id}
                  </p>
                  <textarea
                    value={notes[key] || ''}
                    onChange={(event) =>
                      setNotes({ ...notes, [key]: event.target.value })
                    }
                    placeholder="Decision note (recorded in the audit trail)"
                    className="mt-2 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                    rows={2}
                  />
                  <textarea
                    value={edits[key] || ''}
                    onChange={(event) =>
                      setEdits({ ...edits, [key]: event.target.value })
                    }
                    placeholder='Optional JSON edit, e.g. {"problem_id": "..."}'
                    className="mt-2 w-full rounded-lg border border-gray-300 px-3 py-2 text-xs font-mono"
                    rows={2}
                  />
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={() =>
                        decide.mutate({
                          cycleId: gate.cycle_id,
                          workflowRunId: gate.workflow_run_id,
                          gate: gate.gate,
                          decision: 'approve',
                        })
                      }
                      className="inline-flex items-center gap-1 rounded-lg bg-green-600 px-3 py-1.5 text-sm text-white hover:bg-green-700"
                    >
                      <Check size={16} /> Approve
                    </button>
                    <button
                      onClick={() =>
                        decide.mutate({
                          cycleId: gate.cycle_id,
                          workflowRunId: gate.workflow_run_id,
                          gate: gate.gate,
                          decision: 'edit',
                        })
                      }
                      className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
                    >
                      <Pencil size={16} /> Edit + approve
                    </button>
                    <button
                      onClick={() =>
                        decide.mutate({
                          cycleId: gate.cycle_id,
                          workflowRunId: gate.workflow_run_id,
                          gate: gate.gate,
                          decision: 'reject',
                        })
                      }
                      className="inline-flex items-center gap-1 rounded-lg bg-gray-200 px-3 py-1.5 text-sm text-gray-800 hover:bg-gray-300"
                    >
                      <X size={16} /> Reject
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <h2 className="text-lg font-semibold text-gray-900 mb-3">Cycles</h2>
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        {cyclesQuery.isLoading ? (
          <div className="p-8 text-center text-gray-500">Loading cycles...</div>
        ) : cycles.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            No research cycles yet. The scheduled cycle runs after curate when
            research_cycle_enabled is true.
          </div>
        ) : (
          <table className="min-w-full">
            <thead>
              <tr>
                <th scope="col">Cycle</th>
                <th scope="col">Status</th>
                <th scope="col">Trigger</th>
                <th scope="col">Workflows</th>
                <th scope="col">Policy</th>
                <th scope="col">Budget</th>
                <th scope="col">Started</th>
              </tr>
            </thead>
            <tbody>
              {cycles.map((cycle) => (
                <tr key={cycle.cycle_id}>
                  <td className="font-mono text-xs">{cycle.cycle_id}</td>
                  <td>
                    <StatusBadge status={cycle.status} />
                  </td>
                  <td className="capitalize">{cycle.trigger}</td>
                  <td>
                    {cycle.workflow_runs.length}
                    {cycle.budget.stopped_by_budget ? ' (budget)' : ''}
                  </td>
                  <td className="font-mono text-xs">{cycle.policy_version}</td>
                  <td className="text-xs text-gray-600">
                    {cycle.budget.experiments}/{cycle.budget.max_experiments} exp
                  </td>
                  <td className="text-gray-600">
                    {new Date(cycle.started_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
