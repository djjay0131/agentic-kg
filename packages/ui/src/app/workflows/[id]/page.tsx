'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useWorkflowWebSocket } from '@/lib/websocket';
import WorkflowStepper from '@/components/WorkflowStepper';
import CheckpointForm from '@/components/CheckpointForm';
import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect } from 'react';

const CHECKPOINT_STEPS = ['select_problem', 'approve_proposal', 'review_evaluation'];

export default function WorkflowDetailPage() {
  const params = useParams();
  const runId = params.id as string;
  const queryClient = useQueryClient();

  const { data: workflow, isLoading } = useQuery({
    queryKey: ['workflow', runId],
    queryFn: () => api.getWorkflow(runId),
    refetchInterval: 3000,
  });

  const { lastMessage } = useWorkflowWebSocket(runId);

  // Refetch on WebSocket updates
  useEffect(() => {
    if (lastMessage) {
      queryClient.invalidateQueries({ queryKey: ['workflow', runId] });
    }
  }, [lastMessage, queryClient, runId]);

  const checkpointMutation = useMutation({
    mutationFn: ({
      checkpointType,
      decision,
      feedback,
      editedData,
    }: {
      checkpointType: string;
      decision: string;
      feedback: string;
      editedData?: Record<string, unknown>;
    }) => api.submitCheckpoint(runId, checkpointType, decision, feedback, editedData),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflow', runId] });
    },
    onError: () => {
      queryClient.invalidateQueries({ queryKey: ['workflow', runId] });
    },
  });

  const retryMutation = useMutation({
    mutationFn: () => api.retryEvaluation(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflow', runId] });
    },
    onError: () => {
      queryClient.invalidateQueries({ queryKey: ['workflow', runId] });
    },
  });

  if (isLoading) {
    return <div className="text-center py-12 text-gray-500">Loading workflow...</div>;
  }

  if (!workflow) {
    return <div className="text-center py-12 text-red-500">Workflow not found</div>;
  }

  const isAtCheckpoint =
    workflow.status === 'waiting_checkpoint' &&
    CHECKPOINT_STEPS.includes(workflow.current_step);

  const checkpointData = (() => {
    if (workflow.current_step === 'select_problem') {
      return { ranked_problems: workflow.ranked_problems };
    }
    if (workflow.current_step === 'approve_proposal') {
      return { proposal: workflow.proposal };
    }
    if (workflow.current_step === 'review_evaluation') {
      return {
        evaluation_result: workflow.evaluation_result,
        recommendation: workflow.gate_recommendation,
      };
    }
    return {};
  })();

  return (
    <div className="max-w-4xl mx-auto">
      <Link
        href="/workflows"
        className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-4"
      >
        <ArrowLeft size={16} />
        Back to Workflows
      </Link>

      <div className="flex items-center gap-3 mb-6">
        <h1 className="text-2xl font-bold">Workflow</h1>
        <code className="text-sm bg-gray-100 px-2 py-1 rounded">{runId.slice(0, 12)}...</code>
        <span
          className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            workflow.status === 'completed'
              ? 'bg-green-100 text-green-700'
              : workflow.status === 'failed'
              ? 'bg-red-100 text-red-700'
              : 'bg-blue-100 text-blue-700'
          }`}
        >
          {workflow.status}
        </span>
        {workflow.status === 'running' && workflow.step_started_at ? (
          <span className="text-xs text-blue-600">
            {workflow.current_step || 'working'} for{' '}
            {elapsedSince(workflow.step_started_at)}
          </span>
        ) : null}
      </div>

      <WorkflowStepper currentStep={workflow.current_step} status={workflow.status} />

      {checkpointMutation.isError && (
        <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          Decision not applied: {(checkpointMutation.error as Error).message}. The
          page has been refreshed to show the gate the run is actually waiting at.
        </div>
      )}

      {retryMutation.isError && (
        <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          Evaluation retry not started: {(retryMutation.error as Error).message}.
        </div>
      )}

      {/* Checkpoint form */}
      {isAtCheckpoint && (
        <div className="mt-6">
          <CheckpointForm
            checkpointType={workflow.current_step}
            data={checkpointData}
            loading={checkpointMutation.isPending}
            onRetry={() => retryMutation.mutate()}
            retrying={retryMutation.isPending}
            onSubmit={(decision, feedback, editedData) =>
              checkpointMutation.mutate({
                checkpointType: workflow.current_step,
                decision,
                feedback,
                editedData,
              })
            }
          />
        </div>
      )}

      {/* Results sections */}
      <div className="mt-6 space-y-4">
        {workflow.proposal && (
          <Section title="Continuation Proposal">
            <pre className="text-xs overflow-auto">{JSON.stringify(workflow.proposal, null, 2)}</pre>
          </Section>
        )}

        {workflow.evaluation_result && (
          <Section title="Evaluation Result">
            <pre className="text-xs overflow-auto">
              {JSON.stringify(workflow.evaluation_result, null, 2)}
            </pre>
          </Section>
        )}

        {workflow.synthesis_report && (
          <Section title="Synthesis Report">
            <SynthesisSummary report={workflow.synthesis_report} />
          </Section>
        )}
      </div>

      {/* Audit log */}
      {workflow.messages.length > 0 && (
        <div className="mt-6">
          <h2 className="text-lg font-semibold mb-3">Activity Log</h2>
          <div className="space-y-2 max-h-60 overflow-y-auto">
            {workflow.messages.map((m, i) => (
              <div key={i} className="flex gap-3 text-sm">
                <span className="text-xs text-gray-400 w-20 shrink-0">
                  {new Date(m.timestamp).toLocaleTimeString()}
                </span>
                <span className="font-medium text-primary-600 w-24 shrink-0">[{m.agent}]</span>
                <span className="text-gray-700">{m.content}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Errors */}
      {workflow.errors.length > 0 && (
        <div className="mt-6 p-4 bg-red-50 border border-red-200 rounded-lg">
          <h3 className="text-sm font-semibold text-red-700 mb-2">Errors</h3>
          {workflow.errors.map((e, i) => (
            <p key={i} className="text-sm text-red-600">{e}</p>
          ))}
        </div>
      )}
    </div>
  );
}

function elapsedSince(start: string): string {
  const seconds = Math.max(
    0,
    Math.floor((Date.now() - new Date(start).getTime()) / 1000),
  );
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

function SynthesisSummary({ report }: { report: Record<string, unknown> }) {
  const summary = typeof report.summary === 'string' ? report.summary : '';
  const newProblems = Array.isArray(report.new_problems)
    ? (report.new_problems as unknown[]).map(String)
    : [];
  const recommendations = Array.isArray(report.recommendations)
    ? (report.recommendations as unknown[]).map(String)
    : [];
  const updates = Array.isArray(report.graph_updates)
    ? (report.graph_updates as Record<string, unknown>[])
    : [];

  return (
    <div className="space-y-3 text-sm">
      {summary && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Summary
          </p>
          <p className="text-gray-800 whitespace-pre-wrap">{summary}</p>
        </div>
      )}
      {newProblems.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            New problems
          </p>
          <ul className="list-disc list-inside text-gray-800">
            {newProblems.map((problem, index) => (
              <li key={index}>{problem}</li>
            ))}
          </ul>
        </div>
      )}
      {recommendations.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Recommendations
          </p>
          <ul className="list-disc list-inside text-gray-800">
            {recommendations.map((recommendation, index) => (
              <li key={index}>{recommendation}</li>
            ))}
          </ul>
        </div>
      )}
      {updates.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Graph updates
          </p>
          <ul className="list-disc list-inside text-gray-800">
            {updates.map((update, index) => (
              <li key={index}>
                {String(update.operation || update.type || 'update')}
                {update.target_id ? ` — ${String(update.target_id)}` : ''}
                {update.details ? `: ${String(update.details)}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      <details className="mt-2">
        <summary className="cursor-pointer text-xs text-gray-500 hover:text-gray-700">
          Raw JSON
        </summary>
        <pre className="bg-white rounded p-3 text-xs overflow-auto max-h-64 mt-2 border">
          {JSON.stringify(report, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <details className="bg-white border rounded-lg">
      <summary className="px-4 py-3 font-medium cursor-pointer hover:bg-gray-50">{title}</summary>
      <div className="px-4 pb-4">{children}</div>
    </details>
  );
}
