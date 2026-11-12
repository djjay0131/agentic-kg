'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { AlertCircle, ArrowLeft, ChevronRight } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';

export default function TopicDetailPage() {
  const params = useParams();
  const topicId = params.id as string;

  const { data: topic, isLoading, error } = useQuery({
    queryKey: ['topic', topicId],
    queryFn: () => api.getTopic(topicId),
    enabled: Boolean(topicId),
  });

  const { data: problems } = useQuery({
    queryKey: ['topic', topicId, 'problems'],
    queryFn: () => api.getTopicProblems(topicId, { limit: 200 }),
    enabled: Boolean(topicId),
  });

  if (isLoading) {
    return <div className="max-w-4xl mx-auto p-8 text-center text-gray-500">Loading...</div>;
  }

  if (error || !topic) {
    return (
      <div className="max-w-4xl mx-auto p-8 text-center">
        <AlertCircle className="mx-auto mb-4 text-red-500" size={48} />
        <h2 className="text-xl font-semibold text-gray-900 mb-2">Topic not found</h2>
        <p className="text-gray-500 mb-4">The topic you&apos;re looking for doesn&apos;t exist.</p>
        <Link href="/graph" className="text-primary-600 hover:underline">
          Back to the graph
        </Link>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      <Link
        href="/graph"
        className="inline-flex items-center gap-2 text-gray-500 hover:text-gray-700 mb-6"
      >
        <ArrowLeft size={18} />
        Back to the graph
      </Link>

      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
        <div className="flex items-center gap-3 mb-2">
          <h1 className="text-xl font-semibold text-gray-900">{topic.name}</h1>
          <span className="text-xs text-gray-500 uppercase">{topic.level}</span>
        </div>
        {topic.description && (
          <p className="text-gray-600 mt-2">{topic.description}</p>
        )}
        <div className="mt-4 flex flex-wrap gap-4 text-sm text-gray-500">
          <span>{topic.problem_count} problems</span>
          <span>{topic.paper_count} papers</span>
          <span>source: {topic.source}</span>
        </div>
        {topic.parent && (
          <p className="mt-4 text-sm text-gray-600">
            Parent:{' '}
            <Link
              href={`/topics/${encodeURIComponent(topic.parent.id)}`}
              className="text-primary-600 hover:underline"
            >
              {topic.parent.name}
            </Link>
          </p>
        )}
      </div>

      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="p-6 border-b border-gray-200">
          <h2 className="text-sm font-medium text-gray-500">
            Problems in this topic ({problems?.total ?? 0})
          </h2>
        </div>
        {!problems || problems.problems.length === 0 ? (
          <div className="p-8 text-center text-gray-500">No problems in this topic.</div>
        ) : (
          <ul className="divide-y divide-gray-100">
            {problems.problems.map((problem) => (
              <li key={problem.id}>
                <Link
                  href={`/problems/${encodeURIComponent(problem.id)}`}
                  className="flex items-center justify-between gap-4 px-6 py-4 hover:bg-gray-50"
                >
                  <span className="text-gray-900 line-clamp-2">{problem.statement}</span>
                  <ChevronRight size={18} className="text-gray-400 flex-shrink-0" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
