'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { AlertCircle, ArrowLeft, ExternalLink } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';

function StatusBadge({ status }: { status: string }) {
  const classes: Record<string, string> = {
    running: 'bg-blue-100 text-blue-800',
    succeeded: 'bg-green-100 text-green-800',
    partial: 'bg-yellow-100 text-yellow-800',
    failed: 'bg-red-100 text-red-800',
  };
  return (
    <span className={`badge ${classes[status] || 'bg-gray-100 text-gray-800'}`}>
      {status}
    </span>
  );
}

function formatTime(value: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="text-xs font-medium text-gray-500 uppercase tracking-wider">
        {label}
      </div>
      <div className="mt-1 text-xl font-semibold text-gray-900">{value}</div>
    </div>
  );
}

export default function RunDetailPage() {
  const params = useParams();
  const runId = params.run_id as string;

  const { data: run, isLoading, error } = useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.getRun(runId),
  });

  if (isLoading) {
    return <div className="max-w-5xl mx-auto p-8 text-center text-gray-500">Loading...</div>;
  }

  if (error || !run) {
    return (
      <div className="max-w-5xl mx-auto p-8 text-center">
        <AlertCircle className="mx-auto mb-4 text-red-500" size={48} />
        <h2 className="text-xl font-semibold text-gray-900 mb-2">Run not found</h2>
        <p className="text-gray-500 mb-4">The nightly run you&apos;re looking for doesn&apos;t exist.</p>
        <Link href="/runs" className="text-primary-600 hover:underline">
          Back to runs
        </Link>
      </div>
    );
  }

  const deferralReasons = Object.entries(run.deferral_reasons ?? {});

  return (
    <div className="max-w-5xl mx-auto">
      <Link
        href="/runs"
        className="inline-flex items-center gap-2 text-gray-500 hover:text-gray-700 mb-6"
      >
        <ArrowLeft size={18} />
        Back to runs
      </Link>

      {/* Header */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
        <div className="flex items-start justify-between gap-4 flex-wrap mb-4">
          <h1 className="text-xl font-semibold font-mono text-gray-900 break-all">
            {run.run_id}
          </h1>
          <StatusBadge status={run.status} />
        </div>
        <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 text-sm">
          <div>
            <dt className="text-gray-500">Trigger</dt>
            <dd className="text-gray-900 capitalize">{run.trigger}</dd>
          </div>
          <div>
            <dt className="text-gray-500">Namespace</dt>
            <dd className="text-gray-900">{run.namespace}</dd>
          </div>
          <div>
            <dt className="text-gray-500">Started</dt>
            <dd className="text-gray-900">{formatTime(run.started_at)}</dd>
          </div>
          <div>
            <dt className="text-gray-500">Finished</dt>
            <dd className="text-gray-900">{formatTime(run.finished_at)}</dd>
          </div>
        </dl>
        <div className="mt-4 pt-4 border-t border-gray-100 flex flex-wrap gap-x-6 gap-y-1 text-xs text-gray-500">
          <span>
            <span className="font-medium">git:</span> {run.git_sha || '—'}
          </span>
          <span className="break-all">
            <span className="font-medium">image:</span> {run.image || '—'}
          </span>
        </div>
      </div>

      {/* Totals */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-6">
        <Stat label="Papers seen" value={run.totals.papers_seen} />
        <Stat label="Papers new" value={run.totals.papers_new} />
        <Stat label="Committed ops" value={run.totals.committed_operations} />
        <Stat label="Deferred" value={run.totals.deferred_candidates} />
        <Stat label="Honest nulls" value={run.totals.honest_nulls} />
      </div>

      {/* Budget & queue */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-sm font-medium text-gray-500 mb-4">Budget</h2>
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-gray-600">Max papers</dt>
              <dd className="text-gray-900">{run.budget.max_papers}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-600">Max LLM (USD)</dt>
              <dd className="text-gray-900">${run.budget.max_llm_usd.toFixed(2)}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-600">Estimated LLM (USD)</dt>
              <dd className="text-gray-900">${run.budget.est_llm_usd.toFixed(2)}</dd>
            </div>
            <div className="flex justify-between items-center">
              <dt className="text-gray-600">Stopped by budget</dt>
              <dd>
                {run.budget.stopped_by_budget ? (
                  <span className="badge bg-orange-100 text-orange-800">yes</span>
                ) : (
                  <span className="text-gray-400">no</span>
                )}
              </dd>
            </div>
          </dl>
        </div>

        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-sm font-medium text-gray-500 mb-4">Review queue</h2>
          <p className="text-3xl font-semibold text-gray-900">
            {run.review_queue_size === null ? '—' : run.review_queue_size}
          </p>
          <p className="text-xs text-gray-500 mt-1">
            {run.review_queue_size === null
              ? 'Not available until the review-queue stage ships.'
              : 'Items pending human review.'}
          </p>
        </div>
      </div>

      {/* Queries */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden mb-6">
        <div className="px-6 py-4 border-b border-gray-100">
          <h2 className="text-sm font-medium text-gray-500">
            Queries ({run.queries.length})
          </h2>
        </div>
        {run.queries.length === 0 ? (
          <div className="p-6 text-sm text-gray-500">No queries recorded.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full">
              <thead>
                <tr>
                  <th scope="col">Query</th>
                  <th scope="col">Topic</th>
                  <th scope="col">Limit</th>
                  <th scope="col">Seen</th>
                  <th scope="col">New</th>
                  <th scope="col">Status</th>
                  <th scope="col">Error</th>
                </tr>
              </thead>
              <tbody>
                {run.queries.map((q) => (
                  <tr key={q.query_id}>
                    <td className="text-gray-900">{q.query}</td>
                    <td className="text-gray-600">{q.topic}</td>
                    <td className="text-gray-600">{q.limit}</td>
                    <td className="text-gray-700">{q.papers_seen}</td>
                    <td className="text-gray-700">{q.papers_new}</td>
                    <td>
                      <span className="badge bg-gray-100 text-gray-700">{q.status}</span>
                    </td>
                    <td className="text-red-600">{q.error || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Deferral reasons */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
        <h2 className="text-sm font-medium text-gray-500 mb-3">Deferral reasons</h2>
        {deferralReasons.length === 0 ? (
          <p className="text-sm text-gray-500">No deferred candidates.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {deferralReasons.map(([reason, count]) => (
              <li key={reason} className="flex justify-between max-w-md">
                <span className="text-gray-700">{reason}</span>
                <span className="text-gray-900 font-medium">{count}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Failures */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
        <h2 className="text-sm font-medium text-gray-500 mb-3">
          Failures ({run.failures.length})
        </h2>
        {run.failures.length === 0 ? (
          <p className="text-sm text-gray-500">No failures recorded.</p>
        ) : (
          <ul className="space-y-3">
            {run.failures.map((failure, index) => (
              <li key={index} className="text-sm">
                <span className="font-medium text-gray-900">{failure.step}</span>
                <span className="text-gray-700"> — {failure.message}</span>
                {failure.log_url && (
                  <a
                    href={failure.log_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-2 inline-flex items-center gap-1 text-primary-600 hover:underline"
                  >
                    logs
                    <ExternalLink size={12} />
                  </a>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Proposed queries (hidden until P3 produces any) */}
      {run.proposed_queries.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
          <h2 className="text-sm font-medium text-gray-500 mb-3">
            Proposed queries ({run.proposed_queries.length})
          </h2>
          <ul className="space-y-2 text-sm">
            {run.proposed_queries.map((query, index) => (
              <li key={index} className="text-gray-700 font-mono break-all">
                {JSON.stringify(query)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
