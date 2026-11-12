'use client';

import { useQuery } from '@tanstack/react-query';
import { api, paperPath } from '@/lib/api';
import { FileText, ExternalLink, ChevronLeft, ChevronRight } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';

const PAGE_SIZE = 50;

export default function PapersPage() {
  const [page, setPage] = useState(0);

  const { data, isLoading } = useQuery({
    queryKey: ['papers', page],
    queryFn: () => api.listPapers({ limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
  });

  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const start = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const end = page * PAGE_SIZE + (data?.papers.length ?? 0);

  return (
    <div className="max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Papers</h1>
        <p className="text-gray-500 mt-1">
          Source papers that have been processed
        </p>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-gray-500">Loading...</div>
        ) : data?.papers.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            <FileText className="mx-auto mb-4 text-gray-300" size={48} />
            <p>No papers yet.</p>
            <p className="mt-2">
              <Link href="/extract" className="text-primary-600 hover:underline">
                Extract problems from papers
              </Link>
            </p>
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th className="w-1/2">Title</th>
                <th>Authors</th>
                <th>Year</th>
                <th>Venue</th>
                <th className="w-8"></th>
              </tr>
            </thead>
            <tbody>
              {data?.papers.map((paper) => (
                <tr key={paper.doi}>
                  <td>
                    <Link
                      href={paperPath(paper.doi)}
                      className="text-gray-900 hover:text-primary-600 line-clamp-2"
                    >
                      {paper.title}
                    </Link>
                  </td>
                  <td className="text-gray-600">
                    {paper.authors.slice(0, 2).join(', ')}
                    {paper.authors.length > 2 && ` +${paper.authors.length - 2}`}
                  </td>
                  <td className="text-gray-600">{paper.year ?? '-'}</td>
                  <td className="text-gray-600">{paper.venue || '-'}</td>
                  <td>
                    <a
                      href={`https://doi.org/${paper.doi}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-gray-400 hover:text-primary-600"
                      title="Open DOI"
                    >
                      <ExternalLink size={16} />
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {data && data.papers.length > 0 && (
        <div className="mt-4 flex items-center justify-between text-sm text-gray-500">
          <span>
            Showing {start}–{end} of {total} paper{total !== 1 ? 's' : ''}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-300 bg-white hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <ChevronLeft size={16} />
              Previous
            </button>
            <span className="px-2">
              Page {page + 1} of {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-300 bg-white hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Next
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
