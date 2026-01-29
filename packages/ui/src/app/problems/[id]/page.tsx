'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { ArrowLeft, ExternalLink, CheckCircle, AlertCircle } from 'lucide-react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useState } from 'react';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-6">
      <h3 className="text-sm font-medium text-gray-500 mb-2">{title}</h3>
      <div className="text-gray-900">{children}</div>
    </div>
  );
}

function Tag({ children, color = 'gray' }: { children: React.ReactNode; color?: string }) {
  const colorClasses: Record<string, string> = {
    gray: 'bg-gray-100 text-gray-700',
    blue: 'bg-blue-100 text-blue-700',
    green: 'bg-green-100 text-green-700',
    yellow: 'bg-yellow-100 text-yellow-700',
    red: 'bg-red-100 text-red-700',
  };
  return (
    <span className={`inline-flex items-center px-2 py-1 rounded text-xs font-medium ${colorClasses[color]}`}>
      {children}
    </span>
  );
}

export default function ProblemDetailPage() {
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [editingStatus, setEditingStatus] = useState(false);

  const problemId = params.id as string;

  const { data: problem, isLoading, error } = useQuery({
    queryKey: ['problem', problemId],
    queryFn: () => api.getProblem(problemId),
  });

  const updateMutation = useMutation({
    mutationFn: (status: string) => api.updateProblem(problemId, { status }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['problem', problemId] });
      queryClient.invalidateQueries({ queryKey: ['problems'] });
      setEditingStatus(false);
    },
  });

  if (isLoading) {
    return (
      <div className="max-w-4xl mx-auto p-8 text-center text-gray-500">
        Loading...
      </div>
    );
  }

  if (error || !problem) {
    return (
      <div className="max-w-4xl mx-auto p-8 text-center">
        <AlertCircle className="mx-auto mb-4 text-red-500" size={48} />
        <h2 className="text-xl font-semibold text-gray-900 mb-2">Problem not found</h2>
        <p className="text-gray-500 mb-4">The problem you&apos;re looking for doesn&apos;t exist.</p>
        <Link href="/problems" className="text-primary-600 hover:underline">
          Back to problems
        </Link>
      </div>
    );
  }

  const confidence = problem.extraction_metadata?.confidence_score;

  return (
    <div className="max-w-4xl mx-auto">
      {/* Back button */}
      <Link
        href="/problems"
        className="inline-flex items-center gap-2 text-gray-500 hover:text-gray-700 mb-6"
      >
        <ArrowLeft size={18} />
        Back to problems
      </Link>

      {/* Header */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
        <div className="flex items-start justify-between gap-4 mb-4">
          <h1 className="text-xl font-semibold text-gray-900 leading-relaxed">
            {problem.statement}
          </h1>
          <div className="flex items-center gap-2 flex-shrink-0">
            {editingStatus ? (
              <select
                defaultValue={problem.status}
                onChange={(e) => updateMutation.mutate(e.target.value)}
                className="px-3 py-1 rounded border border-gray-300 text-sm focus:ring-2 focus:ring-primary-500"
              >
                <option value="open">Open</option>
                <option value="in_progress">In Progress</option>
                <option value="resolved">Resolved</option>
                <option value="deprecated">Deprecated</option>
              </select>
            ) : (
              <button
                onClick={() => setEditingStatus(true)}
                className={`badge badge-${problem.status} cursor-pointer hover:opacity-80`}
              >
                {problem.status.replace('_', ' ')}
              </button>
            )}
          </div>
        </div>

        <div className="flex flex-wrap gap-3 text-sm">
          {problem.domain && <Tag color="blue">{problem.domain}</Tag>}
          {confidence !== null && confidence !== undefined && (
            <Tag color={confidence >= 0.7 ? 'green' : confidence >= 0.5 ? 'yellow' : 'red'}>
              {Math.round(confidence * 100)}% confidence
            </Tag>
          )}
          {problem.extraction_metadata?.human_reviewed && (
            <Tag color="green">
              <CheckCircle size={12} className="mr-1" />
              Reviewed
            </Tag>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Evidence */}
        {problem.evidence && (
          <div className="bg-white rounded-lg border border-gray-200 p-6">
            <h2 className="text-sm font-medium text-gray-500 mb-4">Evidence</h2>
            {problem.evidence.quoted_text && (
              <blockquote className="border-l-4 border-primary-300 pl-4 italic text-gray-700 mb-4">
                &ldquo;{problem.evidence.quoted_text}&rdquo;
              </blockquote>
            )}
            {problem.evidence.source_title && (
              <p className="text-sm text-gray-600 mb-1">
                <span className="font-medium">Source:</span> {problem.evidence.source_title}
              </p>
            )}
            {problem.evidence.source_doi && (
              <p className="text-sm text-gray-600 mb-1">
                <span className="font-medium">DOI:</span>{' '}
                <a
                  href={`https://doi.org/${problem.evidence.source_doi}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary-600 hover:underline inline-flex items-center gap-1"
                >
                  {problem.evidence.source_doi}
                  <ExternalLink size={12} />
                </a>
              </p>
            )}
            {problem.evidence.section && (
              <p className="text-sm text-gray-600">
                <span className="font-medium">Section:</span> {problem.evidence.section}
              </p>
            )}
          </div>
        )}

        {/* Extraction Metadata */}
        {problem.extraction_metadata && (
          <div className="bg-white rounded-lg border border-gray-200 p-6">
            <h2 className="text-sm font-medium text-gray-500 mb-4">Extraction Info</h2>
            <div className="space-y-2 text-sm">
              {problem.extraction_metadata.extraction_model && (
                <p className="text-gray-600">
                  <span className="font-medium">Model:</span> {problem.extraction_metadata.extraction_model}
                </p>
              )}
              {problem.extraction_metadata.extractor_version && (
                <p className="text-gray-600">
                  <span className="font-medium">Version:</span> {problem.extraction_metadata.extractor_version}
                </p>
              )}
              {problem.created_at && (
                <p className="text-gray-600">
                  <span className="font-medium">Extracted:</span>{' '}
                  {new Date(problem.created_at).toLocaleDateString()}
                </p>
              )}
            </div>
          </div>
        )}

        {/* Assumptions */}
        {problem.assumptions.length > 0 && (
          <div className="bg-white rounded-lg border border-gray-200 p-6">
            <h2 className="text-sm font-medium text-gray-500 mb-4">
              Assumptions ({problem.assumptions.length})
            </h2>
            <ul className="space-y-2">
              {problem.assumptions.map((a, i) => (
                <li key={i} className="text-sm text-gray-700 flex gap-2">
                  <span className="text-gray-400">&bull;</span>
                  <span>
                    {a.text}
                    {a.implicit && <span className="text-gray-400 ml-1">(implicit)</span>}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Constraints */}
        {problem.constraints.length > 0 && (
          <div className="bg-white rounded-lg border border-gray-200 p-6">
            <h2 className="text-sm font-medium text-gray-500 mb-4">
              Constraints ({problem.constraints.length})
            </h2>
            <ul className="space-y-2">
              {problem.constraints.map((c, i) => (
                <li key={i} className="text-sm text-gray-700 flex gap-2">
                  <Tag>{c.type}</Tag>
                  <span>{c.text}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Datasets */}
        {problem.datasets.length > 0 && (
          <div className="bg-white rounded-lg border border-gray-200 p-6">
            <h2 className="text-sm font-medium text-gray-500 mb-4">
              Datasets ({problem.datasets.length})
            </h2>
            <ul className="space-y-2">
              {problem.datasets.map((d, i) => (
                <li key={i} className="text-sm text-gray-700 flex items-center gap-2">
                  <span className="font-medium">{d.name}</span>
                  {d.available && <Tag color="green">Available</Tag>}
                  {d.url && (
                    <a href={d.url} target="_blank" rel="noopener noreferrer" className="text-primary-600">
                      <ExternalLink size={14} />
                    </a>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Metrics */}
        {problem.metrics.length > 0 && (
          <div className="bg-white rounded-lg border border-gray-200 p-6">
            <h2 className="text-sm font-medium text-gray-500 mb-4">
              Metrics ({problem.metrics.length})
            </h2>
            <ul className="space-y-2">
              {problem.metrics.map((m, i) => (
                <li key={i} className="text-sm text-gray-700">
                  <span className="font-medium">{m.name}</span>
                  {m.description && <span className="text-gray-500"> - {m.description}</span>}
                  {m.baseline_value !== null && (
                    <span className="text-gray-400 ml-1">(baseline: {m.baseline_value})</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
