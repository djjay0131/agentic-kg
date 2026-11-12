'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { ArrowLeft, ExternalLink, AlertCircle, FileText } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';

function Tag({ children, color = 'gray' }: { children: React.ReactNode; color?: string }) {
  const colorClasses: Record<string, string> = {
    gray: 'bg-gray-100 text-gray-700',
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

export default function PaperDetailPage() {
  const params = useParams();
  // The route is a catch-all so DOIs containing "/" (e.g. 10.1234/foo) work.
  const rawDoi = params.doi;
  const doi = Array.isArray(rawDoi) ? rawDoi.join('/') : rawDoi;

  const { data: paper, isLoading, error } = useQuery({
    queryKey: ['paper', doi],
    queryFn: () => api.getPaper(doi),
    enabled: Boolean(doi),
  });

  const { data: problems, isLoading: problemsLoading } = useQuery({
    queryKey: ['paper', doi, 'problems'],
    queryFn: () => api.getPaperProblems(doi),
    enabled: Boolean(doi),
  });

  if (isLoading) {
    return (
      <div className="max-w-4xl mx-auto p-8 text-center text-gray-500">
        Loading...
      </div>
    );
  }

  if (error || !paper) {
    return (
      <div className="max-w-4xl mx-auto p-8 text-center">
        <AlertCircle className="mx-auto mb-4 text-red-500" size={48} />
        <h2 className="text-xl font-semibold text-gray-900 mb-2">Paper not found</h2>
        <p className="text-gray-500 mb-4">
          The paper you&apos;re looking for doesn&apos;t exist.
        </p>
        <Link href="/papers" className="text-primary-600 hover:underline">
          Back to papers
        </Link>
      </div>
    );
  }

  const paperProblems = problems?.problems ?? [];

  return (
    <div className="max-w-4xl mx-auto">
      <Link
        href="/papers"
        className="inline-flex items-center gap-2 text-gray-500 hover:text-gray-700 mb-6"
      >
        <ArrowLeft size={18} />
        Back to papers
      </Link>

      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
        <h1 className="text-xl font-semibold text-gray-900 leading-relaxed mb-4">
          {paper.title}
        </h1>

        <div className="space-y-2 text-sm text-gray-600">
          <p>
            <span className="font-medium">Authors:</span>{' '}
            {paper.authors.length > 0 ? paper.authors.join(', ') : '-'}
          </p>
          <p>
            <span className="font-medium">Year:</span> {paper.year ?? '-'}
          </p>
          <p>
            <span className="font-medium">Venue:</span> {paper.venue || '-'}
          </p>
          <p>
            <span className="font-medium">DOI:</span>{' '}
            <a
              href={`https://doi.org/${paper.doi}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary-600 hover:underline inline-flex items-center gap-1"
            >
              {paper.doi}
              <ExternalLink size={12} />
            </a>
          </p>
          {paper.arxiv_id && (
            <p>
              <span className="font-medium">arXiv:</span>{' '}
              <a
                href={`https://arxiv.org/abs/${paper.arxiv_id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary-600 hover:underline inline-flex items-center gap-1"
              >
                {paper.arxiv_id}
                <ExternalLink size={12} />
              </a>
            </p>
          )}
        </div>
      </div>

      {paper.abstract && (
        <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
          <h2 className="text-sm font-medium text-gray-500 mb-3">Abstract</h2>
          <p className="text-gray-700 leading-relaxed whitespace-pre-line">
            {paper.abstract}
          </p>
        </div>
      )}

      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="p-6 border-b border-gray-200">
          <h2 className="text-sm font-medium text-gray-500">
            Extracted problems ({problems?.total ?? paperProblems.length})
          </h2>
        </div>
        {problemsLoading ? (
          <div className="p-8 text-center text-gray-500">Loading problems...</div>
        ) : paperProblems.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            <FileText className="mx-auto mb-3 text-gray-300" size={40} />
            No problems have been extracted from this paper.
          </div>
        ) : (
          <ul className="divide-y divide-gray-100">
            {paperProblems.map((problem) => (
              <li key={problem.id}>
                <Link
                  href={`/problems/${encodeURIComponent(problem.id)}`}
                  className="block px-6 py-4 hover:bg-gray-50"
                >
                  <p className="text-gray-900">{problem.statement}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Tag>{problem.status.replace('_', ' ')}</Tag>
                    {problem.confidence !== null && problem.confidence !== undefined && (
                      <Tag
                        color={
                          problem.confidence >= 0.7
                            ? 'green'
                            : problem.confidence >= 0.5
                              ? 'yellow'
                              : 'red'
                        }
                      >
                        {Math.round(problem.confidence * 100)}% confidence
                      </Tag>
                    )}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
