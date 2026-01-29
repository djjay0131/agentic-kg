'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { FileText, ExternalLink } from 'lucide-react';
import Link from 'next/link';

export default function PapersPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['papers'],
    queryFn: () => api.listPapers({ limit: 100 }),
  });

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
                    <span className="text-gray-900 line-clamp-2">{paper.title}</span>
                  </td>
                  <td className="text-gray-600">
                    {paper.authors.slice(0, 2).join(', ')}
                    {paper.authors.length > 2 && ` +${paper.authors.length - 2}`}
                  </td>
                  <td className="text-gray-600">{paper.year || '-'}</td>
                  <td className="text-gray-600">{paper.venue || '-'}</td>
                  <td>
                    <a
                      href={`https://doi.org/${paper.doi}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-gray-400 hover:text-primary-600"
                      title="View paper"
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

      {data && (
        <div className="mt-4 text-sm text-gray-500">
          Showing {data.papers.length} paper{data.papers.length !== 1 ? 's' : ''}
        </div>
      )}
    </div>
  );
}
