'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Search, ChevronRight } from 'lucide-react';
import Link from 'next/link';
import { useState, useEffect, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';

const PAGE_SIZE = 50;

function ConfidenceBar({ value }: { value: number | null }) {
  if (value === null) return <span className="text-gray-400">-</span>;
  const pct = Math.round(value * 100);
  let colorClass = 'confidence-high';
  if (pct < 70) colorClass = 'confidence-medium';
  if (pct < 50) colorClass = 'confidence-low';

  return (
    <div className="flex items-center gap-2">
      <div className="confidence-bar w-16">
        <div className={`confidence-fill ${colorClass}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-500">{pct}%</span>
    </div>
  );
}

function AgentBadge({ origin }: { origin?: string | null }) {
  if (!origin?.startsWith('agent:')) return null;
  return (
    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-100 text-purple-700 align-middle ml-2">
      agent-derived
    </span>
  );
}

export default function ProblemsPage() {
  return (
    <Suspense fallback={<div className="text-center py-12 text-gray-500">Loading...</div>}>
      <ProblemsContent />
    </Suspense>
  );
}

function ProblemsContent() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const initialQuery = searchParams.get('q') || '';
  const [searchQuery, setSearchQuery] = useState(initialQuery);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [domainFilter, setDomainFilter] = useState<string>('');
  const [originFilter, setOriginFilter] = useState<string>('');
  const [offset, setOffset] = useState(0);

  // Update search input when URL changes
  useEffect(() => {
    setSearchQuery(searchParams.get('q') || '');
  }, [searchParams]);

  // A filter change starts a new list; page 2 of the old filter is meaningless.
  useEffect(() => {
    setOffset(0);
  }, [statusFilter, domainFilter, originFilter]);

  const isSearchMode = searchQuery.trim().length > 0;

  // Search query
  const { data: searchResults, isLoading: searchLoading } = useQuery({
    queryKey: ['search', searchQuery, statusFilter, domainFilter],
    queryFn: () => api.search(searchQuery, {
      status: statusFilter || undefined,
      top_k: 50,
    }),
    enabled: isSearchMode,
  });

  // List query (when not searching)
  const { data: listResults, isLoading: listLoading } = useQuery({
    queryKey: ['problems', 'list', statusFilter, domainFilter, originFilter, offset],
    queryFn: () => api.listProblems({
      status: statusFilter || undefined,
      domain: domainFilter || undefined,
      origin: originFilter || undefined,
      limit: PAGE_SIZE,
      offset,
    }),
    enabled: !isSearchMode,
  });

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setOffset(0);
    const params = new URLSearchParams();
    if (searchQuery.trim()) params.set('q', searchQuery);
    router.push(`/problems${params.toString() ? `?${params}` : ''}`);
  };

  const isLoading = isSearchMode ? searchLoading : listLoading;
  const problems = isSearchMode
    ? searchResults?.results.map((r) => ({ ...r.problem, score: r.score })) || []
    : listResults?.problems || [];
  const total = isSearchMode ? searchResults?.total ?? 0 : listResults?.total ?? 0;

  return (
    <div className="max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Problems</h1>
        <p className="text-gray-500 mt-1">
          Browse and search extracted research problems
        </p>
      </div>

      {/* Search & Filters */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 mb-6">
        <form onSubmit={handleSearch} className="flex gap-4 items-end flex-wrap">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Search
            </label>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
              <input
                type="text"
                placeholder="Search problems..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-10 pr-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
              />
            </div>
          </div>

          <div className="w-40">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Status
            </label>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            >
              <option value="">All statuses</option>
              <option value="open">Open</option>
              <option value="in_progress">In Progress</option>
              <option value="resolved">Resolved</option>
              <option value="deprecated">Deprecated</option>
            </select>
          </div>

          <div className="w-40">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Origin
            </label>
            <select
              value={originFilter}
              onChange={(e) => setOriginFilter(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            >
              <option value="">All origins</option>
              <option value="extracted">Extracted</option>
              <option value="agent:synthesis">Agent-derived</option>
            </select>
          </div>

          <button
            type="submit"
            className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
          >
            Search
          </button>
        </form>
      </div>

      {/* Results */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-gray-500">Loading...</div>
        ) : problems.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            No problems found.{' '}
            {isSearchMode && (
              <button
                onClick={() => {
                  setSearchQuery('');
                  router.push('/problems');
                }}
                className="text-primary-600 hover:underline"
              >
                Clear search
              </button>
            )}
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th className="w-1/2">Statement</th>
                <th>Domain</th>
                <th>Status</th>
                <th>Confidence</th>
                {isSearchMode && <th>Score</th>}
                <th className="w-8"></th>
              </tr>
            </thead>
            <tbody>
              {problems.map((problem: any) => (
                <tr key={problem.id}>
                  <td>
                    <Link
                      href={`/problems/${problem.id}`}
                      className="text-gray-900 hover:text-primary-600 line-clamp-2"
                    >
                      {problem.statement}
                    </Link>
                    <AgentBadge origin={problem.origin} />
                  </td>
                  <td className="text-gray-600">{problem.domain || '-'}</td>
                  <td>
                    <span className={`badge badge-${problem.status}`}>
                      {problem.status.replace('_', ' ')}
                    </span>
                  </td>
                  <td>
                    <ConfidenceBar value={problem.confidence} />
                  </td>
                  {isSearchMode && (
                    <td className="text-gray-500">{problem.score?.toFixed(2)}</td>
                  )}
                  <td>
                    <Link href={`/problems/${problem.id}`} className="text-gray-400 hover:text-gray-600">
                      <ChevronRight size={18} />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Count + pagination */}
      <div className="mt-4 flex items-center justify-between text-sm text-gray-500">
        <span>
          {isSearchMode
            ? `Showing ${problems.length} problem${problems.length !== 1 ? 's' : ''}${
                searchResults ? ` for "${searchResults.query}"` : ''
              }`
            : total === 0
              ? 'No problems'
              : `${offset + 1}–${Math.min(offset + problems.length, total)} of ${total}`}
        </span>
        {!isSearchMode && (
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
        )}
      </div>
    </div>
  );
}
