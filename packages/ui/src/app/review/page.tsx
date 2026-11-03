'use client';

import { Suspense, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { ChevronRight, Inbox } from 'lucide-react';
import Link from 'next/link';

const PAGE_SIZE = 25;

const TYPES = [
  '',
  'Problem',
  'ResearchConcept',
  'Method',
  'Model',
  'Topic',
  'relation',
  'attribute_assertion',
];

function VerdictBadge({ verdict, abstained }: { verdict: string; abstained: boolean }) {
  const classes = verdict === 'admit'
    ? 'bg-green-100 text-green-800'
    : verdict.startsWith('merge-into:')
      ? 'bg-purple-100 text-purple-800'
      : 'bg-gray-100 text-gray-700';
  return (
    <span className={`badge ${classes}`} title={abstained ? 'adviser abstained' : undefined}>
      {verdict}{abstained ? ' (abstained)' : ''}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const classes: Record<string, string> = {
    pending: 'bg-yellow-100 text-yellow-800',
    approved: 'bg-green-100 text-green-800',
    rejected: 'bg-gray-200 text-gray-700',
  };
  return <span className={`badge ${classes[status] || 'bg-gray-100 text-gray-800'}`}>{status}</span>;
}

function formatTime(value: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function ReviewQueue() {
  const params = useSearchParams();
  const [status, setStatus] = useState('pending');
  const [type, setType] = useState('');
  const [runId, setRunId] = useState(params.get('run_id') || '');
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    setRunId(params.get('run_id') || '');
  }, [params]);

  useEffect(() => {
    setOffset(0);
  }, [status, type, runId]);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['review', status, type, runId, offset],
    queryFn: () =>
      api.listReview({
        status: status || undefined,
        type: type || undefined,
        run_id: runId || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Review Queue</h1>
        <p className="text-gray-500 mt-1">
          Deferred candidates graded by the nightly adviser. Approving an entity
          commits it through KGCS; rejecting records the decision without writing
          the graph.
        </p>
      </div>

      <div className="flex flex-wrap gap-3 mb-4">
        <label className="text-sm text-gray-600">
          Status
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            className="ml-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900"
          >
            <option value="">all</option>
            <option value="pending">pending</option>
            <option value="approved">approved</option>
            <option value="rejected">rejected</option>
          </select>
        </label>
        <label className="text-sm text-gray-600">
          Type
          <select
            value={type}
            onChange={(event) => setType(event.target.value)}
            className="ml-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900"
          >
            {TYPES.map((value) => (
              <option key={value || 'all'} value={value}>
                {value || 'all'}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm text-gray-600 flex-1 min-w-[280px]">
          Run id
          <input
            value={runId}
            onChange={(event) => setRunId(event.target.value)}
            placeholder="nightly-YYYYMMDDTHHMMSSZ"
            className="ml-2 w-72 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-mono text-gray-900"
          />
        </label>
      </div>

      {isError && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          Failed to load the review queue: {(error as Error)?.message}
        </div>
      )}

      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-gray-500">Loading review items...</div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            <Inbox className="mx-auto mb-3 text-gray-400" size={40} />
            No review items match these filters.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full">
              <thead>
                <tr>
                  <th scope="col">Type</th>
                  <th scope="col">Candidate</th>
                  <th scope="col">Adviser verdict</th>
                  <th scope="col">Confidence</th>
                  <th scope="col">Run</th>
                  <th scope="col">Created</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="w-8"></th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td className="text-gray-700">
                      {item.entity_type || item.candidate_kind}
                    </td>
                    <td className="max-w-md">
                      <Link
                        href={`/review/${encodeURIComponent(item.id)}`}
                        className="text-gray-900 hover:text-primary-600"
                      >
                        <span className="line-clamp-2">{item.candidate_summary}</span>
                      </Link>
                      {!item.committable && (
                        <span className="ml-2 badge bg-orange-100 text-orange-800">
                          not committable
                        </span>
                      )}
                    </td>
                    <td>
                      <VerdictBadge
                        verdict={item.adviser_verdict}
                        abstained={item.adviser_abstained}
                      />
                    </td>
                    <td className="text-gray-700">
                      {item.confidence === null ? '—' : item.confidence.toFixed(2)}
                    </td>
                    <td className="font-mono text-xs text-gray-600">
                      <Link
                        href={`/runs/${encodeURIComponent(item.run_id)}`}
                        className="hover:text-primary-600"
                      >
                        {item.run_id}
                      </Link>
                    </td>
                    <td className="text-gray-600">{formatTime(item.created_at)}</td>
                    <td>
                      <StatusBadge status={item.status} />
                    </td>
                    <td>
                      <Link
                        href={`/review/${encodeURIComponent(item.id)}`}
                        className="text-gray-400 hover:text-gray-600"
                        aria-label={`Open review item ${item.id}`}
                      >
                        <ChevronRight size={18} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="mt-4 flex items-center justify-between text-sm text-gray-500">
        <span>
          {total === 0
            ? 'No items'
            : `${offset + 1}–${Math.min(offset + items.length, total)} of ${total}`}
        </span>
        <div className="flex gap-2">
          <button
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            disabled={offset === 0}
            className="px-4 py-2 bg-white border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            Previous
          </button>
          <button
            onClick={() => setOffset(offset + PAGE_SIZE)}
            disabled={offset + PAGE_SIZE >= total}
            className="px-4 py-2 bg-white border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}

export default function ReviewPage() {
  return (
    <Suspense fallback={<div className="max-w-7xl mx-auto p-8 text-center text-gray-500">Loading...</div>}>
      <ReviewQueue />
    </Suspense>
  );
}
