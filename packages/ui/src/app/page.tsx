'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Search } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import { useRouter } from 'next/navigation';

function StatCard({
  title,
  value,
  subtitle,
}: {
  title: string;
  value: number | string;
  subtitle?: string;
}) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6">
      <h3 className="text-sm font-medium text-gray-500">{title}</h3>
      <p className="mt-2 text-3xl font-semibold text-gray-900">{value}</p>
      {subtitle && <p className="mt-1 text-sm text-gray-500">{subtitle}</p>}
    </div>
  );
}

function StatusDistribution({
  data,
}: {
  data: Record<string, number>;
}) {
  const total = Object.values(data).reduce((a, b) => a + b, 0);
  if (total === 0) return null;

  const colors: Record<string, string> = {
    open: 'bg-blue-500',
    in_progress: 'bg-yellow-500',
    resolved: 'bg-green-500',
    deprecated: 'bg-gray-400',
  };

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6">
      <h3 className="text-sm font-medium text-gray-500 mb-4">Problem Status</h3>
      <div className="flex h-4 rounded-full overflow-hidden bg-gray-100">
        {Object.entries(data).map(([status, count]) => (
          <div
            key={status}
            className={`${colors[status] || 'bg-gray-400'}`}
            style={{ width: `${(count / total) * 100}%` }}
            title={`${status}: ${count}`}
          />
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-4">
        {Object.entries(data).map(([status, count]) => (
          <div key={status} className="flex items-center gap-2 text-sm">
            <span className={`w-3 h-3 rounded-full ${colors[status] || 'bg-gray-400'}`} />
            <span className="text-gray-600 capitalize">{status.replace('_', ' ')}</span>
            <span className="text-gray-900 font-medium">{count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const router = useRouter();
  const [searchQuery, setSearchQuery] = useState('');

  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['stats'],
    queryFn: api.stats,
  });

  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    refetchInterval: 30000,
  });

  const { data: recentProblems } = useQuery({
    queryKey: ['problems', 'recent'],
    queryFn: () => api.listProblems({ limit: 5 }),
  });

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      router.push(`/problems?q=${encodeURIComponent(searchQuery)}`);
    }
  };

  return (
    <div className="max-w-7xl mx-auto">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-gray-500 mt-1">
          Overview of your research knowledge graph
        </p>
      </div>

      {/* Search */}
      <form onSubmit={handleSearch} className="mb-8">
        <div className="relative max-w-2xl">
          <Search
            className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400"
            size={20}
          />
          <input
            type="text"
            placeholder="Search problems..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-12 pr-4 py-3 rounded-lg border border-gray-300 focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
          />
        </div>
      </form>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <StatCard
          title="Total Problems"
          value={statsLoading ? '...' : stats?.total_problems ?? 0}
          subtitle="Extracted research problems"
        />
        <StatCard
          title="Total Papers"
          value={statsLoading ? '...' : stats?.total_papers ?? 0}
          subtitle="Source papers processed"
        />
        <StatCard
          title="System Status"
          value={health?.neo4j_connected ? 'Online' : 'Offline'}
          subtitle={`v${health?.version || '...'}`}
        />
      </div>

      {/* Status Distribution */}
      {stats?.problems_by_status && Object.keys(stats.problems_by_status).length > 0 && (
        <div className="mb-8">
          <StatusDistribution data={stats.problems_by_status} />
        </div>
      )}

      {/* Recent Problems */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <div className="flex justify-between items-center mb-4">
          <h3 className="text-sm font-medium text-gray-500">Recent Problems</h3>
          <Link
            href="/problems"
            className="text-sm text-primary-600 hover:text-primary-700"
          >
            View all
          </Link>
        </div>
        <div className="divide-y divide-gray-100">
          {recentProblems?.problems.length === 0 && (
            <p className="text-gray-500 text-sm py-4">
              No problems yet. <Link href="/extract" className="text-primary-600">Extract some</Link> from papers.
            </p>
          )}
          {recentProblems?.problems.map((problem) => (
            <Link
              key={problem.id}
              href={`/problems/${problem.id}`}
              className="block py-3 hover:bg-gray-50 -mx-2 px-2 rounded"
            >
              <p className="text-sm text-gray-900 line-clamp-2">
                {problem.statement}
              </p>
              <div className="mt-1 flex items-center gap-3 text-xs text-gray-500">
                <span className={`badge badge-${problem.status}`}>
                  {problem.status.replace('_', ' ')}
                </span>
                {problem.domain && <span>{problem.domain}</span>}
                {problem.confidence && (
                  <span>{Math.round(problem.confidence * 100)}% conf.</span>
                )}
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
