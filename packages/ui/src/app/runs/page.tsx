'use client';

import { useInfiniteQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { ChevronRight } from 'lucide-react';
import Link from 'next/link';

const EMPTY_STATE =
  'No nightly runs yet — the pipeline runs at 02:30 ET; trigger one from GitHub Actions → Run nightly pipeline now.';

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

export default function RunsPage() {
  const {
    data,
    isLoading,
    isError,
    error,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ['runs'],
    queryFn: ({ pageParam }) =>
      api.listRuns({ limit: 20, cursor: pageParam ?? undefined }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const runs = data?.pages.flatMap((page) => page.runs) ?? [];

  return (
    <div className="max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Nightly Runs</h1>
        <p className="text-gray-500 mt-1">
          Reports from each nightly research pipeline execution.
        </p>
      </div>

      {isError && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
          Failed to load runs: {(error as Error)?.message}
        </div>
      )}

      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-gray-500">Loading runs...</div>
        ) : runs.length === 0 ? (
          <div className="p-8 text-center text-gray-500">{EMPTY_STATE}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full">
              <thead>
                <tr>
                  <th scope="col">Run</th>
                  <th scope="col">Status</th>
                  <th scope="col">Started</th>
                  <th scope="col">Finished</th>
                  <th scope="col">Trigger</th>
                  <th scope="col">Papers (new / seen)</th>
                  <th scope="col">Committed</th>
                  <th scope="col">Deferred</th>
                  <th scope="col">Budget</th>
                  <th scope="col">Failures</th>
                  <th scope="col" className="w-8"></th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.run_id}>
                    <td>
                      <Link
                        href={`/runs/${encodeURIComponent(run.run_id)}`}
                        className="font-mono text-sm text-gray-900 hover:text-primary-600"
                      >
                        {run.run_id}
                      </Link>
                    </td>
                    <td>
                      <StatusBadge status={run.status} />
                    </td>
                    <td className="text-gray-600">{formatTime(run.started_at)}</td>
                    <td className="text-gray-600">{formatTime(run.finished_at)}</td>
                    <td className="text-gray-600 capitalize">{run.trigger}</td>
                    <td className="text-gray-700">
                      <span className="font-medium">{run.totals.papers_new}</span>
                      {' / '}
                      {run.totals.papers_seen}
                    </td>
                    <td className="text-gray-700">{run.totals.committed_operations}</td>
                    <td className="text-gray-700">{run.totals.deferred_candidates}</td>
                    <td>
                      {run.budget_stopped ? (
                        <span className="badge bg-orange-100 text-orange-800">
                          stopped
                        </span>
                      ) : (
                        <span className="text-gray-400">—</span>
                      )}
                    </td>
                    <td
                      className={
                        run.failures_count > 0
                          ? 'text-red-600 font-medium'
                          : 'text-gray-500'
                      }
                    >
                      {run.failures_count}
                    </td>
                    <td>
                      <Link
                        href={`/runs/${encodeURIComponent(run.run_id)}`}
                        className="text-gray-400 hover:text-gray-600"
                        aria-label={`View run ${run.run_id}`}
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

      {hasNextPage && (
        <div className="mt-4 text-center">
          <button
            onClick={() => fetchNextPage()}
            disabled={isFetchingNextPage}
            className="px-4 py-2 bg-white border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {isFetchingNextPage ? 'Loading...' : 'Load more'}
          </button>
        </div>
      )}

      {runs.length > 0 && (
        <div className="mt-4 text-sm text-gray-500">
          Showing {runs.length} run{runs.length !== 1 ? 's' : ''}
        </div>
      )}
    </div>
  );
}
