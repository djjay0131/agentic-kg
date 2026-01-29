'use client';

import { useQuery } from '@tanstack/react-query';
import { api, GraphNode } from '@/lib/api';
import { Settings2, ZoomIn, ZoomOut, Maximize2, Filter, Loader2 } from 'lucide-react';
import { useState, Suspense } from 'react';
import dynamic from 'next/dynamic';
import { useRouter } from 'next/navigation';

// Dynamic import for the graph component (requires client-side only)
const GraphView = dynamic(() => import('@/components/GraphView'), {
  ssr: false,
  loading: () => (
    <div className="w-full h-full flex items-center justify-center bg-gray-900 rounded-lg">
      <Loader2 className="animate-spin text-gray-400" size={32} />
    </div>
  ),
});

const DOMAINS = [
  'machine_learning',
  'nlp',
  'computer_vision',
  'reinforcement_learning',
  'optimization',
  'robotics',
  'systems',
];

export default function GraphPage() {
  const router = useRouter();
  const [nodeLimit, setNodeLimit] = useState(100);
  const [domainFilter, setDomainFilter] = useState<string | undefined>();
  const [includePapers, setIncludePapers] = useState(true);
  const [showFilters, setShowFilters] = useState(false);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  const { data: graphData, isLoading, error, refetch } = useQuery({
    queryKey: ['graph', nodeLimit, domainFilter, includePapers],
    queryFn: () =>
      api.getGraph({
        limit: nodeLimit,
        domain: domainFilter,
        include_papers: includePapers,
      }),
  });

  const handleNodeClick = (node: GraphNode) => {
    setSelectedNode(node);

    // Navigate based on node type
    if (node.type === 'problem') {
      const problemId = node.id.replace('problem:', '');
      // Open in new tab or navigate
      window.open(`/problems/${encodeURIComponent(problemId)}`, '_blank');
    } else if (node.type === 'paper' && node.properties.doi) {
      window.open(`/papers/${encodeURIComponent(node.properties.doi as string)}`, '_blank');
    }
  };

  return (
    <div className="h-[calc(100vh-8rem)] flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Knowledge Graph</h1>
          <p className="text-gray-500 mt-1">
            Explore research problems and their relationships
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              showFilters
                ? 'bg-primary-100 text-primary-700'
                : 'text-gray-600 hover:bg-gray-100'
            }`}
          >
            <Filter size={16} />
            Filters
          </button>
          <button
            onClick={() => refetch()}
            className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors text-sm font-medium"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Filter panel */}
      {showFilters && (
        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-4">
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Node Limit
              </label>
              <select
                value={nodeLimit}
                onChange={(e) => setNodeLimit(Number(e.target.value))}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none text-sm"
              >
                <option value={50}>50 nodes</option>
                <option value={100}>100 nodes</option>
                <option value={200}>200 nodes</option>
                <option value={500}>500 nodes</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Filter by Domain
              </label>
              <select
                value={domainFilter || ''}
                onChange={(e) => setDomainFilter(e.target.value || undefined)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none text-sm"
              >
                <option value="">All domains</option>
                {DOMAINS.map((d) => (
                  <option key={d} value={d}>
                    {d.replace(/_/g, ' ')}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Include Papers
              </label>
              <label className="flex items-center gap-2 mt-2">
                <input
                  type="checkbox"
                  checked={includePapers}
                  onChange={(e) => setIncludePapers(e.target.checked)}
                  className="w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
                />
                <span className="text-sm text-gray-600">Show paper nodes</span>
              </label>
            </div>
          </div>
        </div>
      )}

      {/* Stats bar */}
      {graphData && (
        <div className="flex items-center gap-6 mb-4 text-sm text-gray-500">
          <span>
            <strong className="text-gray-900">{graphData.nodes.length}</strong> nodes
          </span>
          <span>
            <strong className="text-gray-900">{graphData.links.length}</strong> links
          </span>
          <span className="text-gray-400">|</span>
          <span>
            <span className="inline-block w-2 h-2 rounded-full bg-blue-500 mr-1" />
            {graphData.nodes.filter((n) => n.type === 'problem').length} problems
          </span>
          <span>
            <span className="inline-block w-2 h-2 rounded-full bg-green-500 mr-1" />
            {graphData.nodes.filter((n) => n.type === 'paper').length} papers
          </span>
          <span>
            <span className="inline-block w-2 h-2 rounded-full bg-purple-500 mr-1" />
            {graphData.nodes.filter((n) => n.type === 'domain').length} domains
          </span>
        </div>
      )}

      {/* Graph area */}
      <div className="flex-1 min-h-0">
        {isLoading ? (
          <div className="w-full h-full flex items-center justify-center bg-gray-100 rounded-lg">
            <div className="flex items-center gap-3">
              <Loader2 className="animate-spin text-gray-400" size={24} />
              <span className="text-gray-500">Loading graph data...</span>
            </div>
          </div>
        ) : error ? (
          <div className="w-full h-full flex items-center justify-center bg-red-50 rounded-lg">
            <div className="text-center">
              <p className="text-red-600 font-medium">Failed to load graph</p>
              <p className="text-red-500 text-sm mt-1">{(error as Error).message}</p>
              <button
                onClick={() => refetch()}
                className="mt-3 px-4 py-2 bg-red-100 text-red-700 rounded-lg hover:bg-red-200 transition-colors text-sm"
              >
                Retry
              </button>
            </div>
          </div>
        ) : graphData ? (
          <GraphView
            nodes={graphData.nodes}
            links={graphData.links}
            onNodeClick={handleNodeClick}
          />
        ) : null}
      </div>

      {/* Selected node info panel */}
      {selectedNode && (
        <div className="mt-4 bg-white rounded-lg border border-gray-200 p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div
                className="w-4 h-4 rounded-full"
                style={{
                  backgroundColor:
                    selectedNode.type === 'problem'
                      ? '#3b82f6'
                      : selectedNode.type === 'paper'
                      ? '#10b981'
                      : '#8b5cf6',
                }}
              />
              <span className="font-medium text-gray-900">{selectedNode.label}</span>
              <span className="text-xs text-gray-500 uppercase">{selectedNode.type}</span>
            </div>
            <button
              onClick={() => setSelectedNode(null)}
              className="text-gray-400 hover:text-gray-600"
            >
              &times;
            </button>
          </div>
          {selectedNode.properties && Object.keys(selectedNode.properties).length > 0 && (
            <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
              {Object.entries(selectedNode.properties).map(([key, value]) => (
                <div key={key} className="flex gap-2">
                  <span className="text-gray-500">{key}:</span>
                  <span className="text-gray-900 truncate">
                    {typeof value === 'string' || typeof value === 'number'
                      ? String(value)
                      : JSON.stringify(value)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
