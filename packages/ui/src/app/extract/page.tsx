'use client';

import { useMutation } from '@tanstack/react-query';
import { api, ExtractResponse } from '@/lib/api';
import { Upload, Link as LinkIcon, FileText, Check, X, Loader2 } from 'lucide-react';
import { useState } from 'react';

function ResultDisplay({ result }: { result: ExtractResponse }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6">
      <div className="flex items-center gap-3 mb-4">
        {result.success ? (
          <div className="flex items-center gap-2 text-green-600">
            <Check className="p-1 bg-green-100 rounded-full" size={24} />
            <span className="font-medium">Extraction Successful</span>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-red-600">
            <X className="p-1 bg-red-100 rounded-full" size={24} />
            <span className="font-medium">Extraction Failed</span>
          </div>
        )}
      </div>

      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="text-center p-4 bg-gray-50 rounded-lg">
          <div className="text-2xl font-bold text-gray-900">{result.problems_extracted}</div>
          <div className="text-sm text-gray-500">Problems</div>
        </div>
        <div className="text-center p-4 bg-gray-50 rounded-lg">
          <div className="text-2xl font-bold text-gray-900">{result.relations_found}</div>
          <div className="text-sm text-gray-500">Relations</div>
        </div>
        <div className="text-center p-4 bg-gray-50 rounded-lg">
          <div className="text-2xl font-bold text-gray-900">{Math.round(result.duration_ms / 1000)}s</div>
          <div className="text-sm text-gray-500">Duration</div>
        </div>
      </div>

      {/* Pipeline stages */}
      <div className="mb-6">
        <h3 className="text-sm font-medium text-gray-700 mb-2">Pipeline Stages</h3>
        <div className="space-y-2">
          {result.stages.map((stage, i) => (
            <div key={i} className="flex items-center gap-3 text-sm">
              {stage.success ? (
                <Check className="text-green-500" size={16} />
              ) : (
                <X className="text-red-500" size={16} />
              )}
              <span className="text-gray-700 capitalize">{stage.stage.replace('_', ' ')}</span>
              <span className="text-gray-400">{stage.duration_ms}ms</span>
              {stage.error && <span className="text-red-500">{stage.error}</span>}
            </div>
          ))}
        </div>
      </div>

      {/* Extracted problems */}
      {result.problems.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-2">
            Extracted Problems ({result.problems.length})
          </h3>
          <div className="space-y-3">
            {result.problems.map((p, i) => (
              <div key={i} className="p-4 bg-gray-50 rounded-lg">
                <p className="text-gray-900 mb-2">{p.statement}</p>
                <div className="flex items-center gap-3 text-xs">
                  {p.domain && (
                    <span className="bg-blue-100 text-blue-700 px-2 py-0.5 rounded">{p.domain}</span>
                  )}
                  <span className={`px-2 py-0.5 rounded ${
                    p.confidence >= 0.7 ? 'bg-green-100 text-green-700' :
                    p.confidence >= 0.5 ? 'bg-yellow-100 text-yellow-700' :
                    'bg-red-100 text-red-700'
                  }`}>
                    {Math.round(p.confidence * 100)}% confidence
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function ExtractPage() {
  const [mode, setMode] = useState<'url' | 'text'>('url');
  const [url, setUrl] = useState('');
  const [text, setText] = useState('');
  const [title, setTitle] = useState('');

  const extractMutation = useMutation({
    mutationFn: (data: { url?: string; text?: string; title?: string }) => api.extract(data),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (mode === 'url' && url) {
      extractMutation.mutate({ url, title: title || undefined });
    } else if (mode === 'text' && text) {
      extractMutation.mutate({ text, title: title || undefined });
    }
  };

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Extract Problems</h1>
        <p className="text-gray-500 mt-1">
          Extract research problems from papers or text
        </p>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
        {/* Mode tabs */}
        <div className="flex gap-2 mb-6">
          <button
            onClick={() => setMode('url')}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              mode === 'url'
                ? 'bg-primary-100 text-primary-700'
                : 'text-gray-600 hover:bg-gray-100'
            }`}
          >
            <LinkIcon size={16} />
            From URL
          </button>
          <button
            onClick={() => setMode('text')}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              mode === 'text'
                ? 'bg-primary-100 text-primary-700'
                : 'text-gray-600 hover:bg-gray-100'
            }`}
          >
            <FileText size={16} />
            From Text
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          {/* Title (optional) */}
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Paper Title (optional)
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Enter paper title..."
              className="w-full px-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
            />
          </div>

          {mode === 'url' ? (
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-1">
                PDF URL
              </label>
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://arxiv.org/pdf/2401.12345.pdf"
                className="w-full px-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none"
                required
              />
              <p className="mt-1 text-xs text-gray-500">
                Enter a direct URL to a PDF file (e.g., arXiv, OpenReview)
              </p>
            </div>
          ) : (
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Paper Text
              </label>
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Paste the paper text here..."
                rows={10}
                className="w-full px-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-primary-500 focus:border-primary-500 outline-none resize-y"
                required
              />
              <p className="mt-1 text-xs text-gray-500">
                Paste the full text of the paper for extraction
              </p>
            </div>
          )}

          <button
            type="submit"
            disabled={extractMutation.isPending}
            className="flex items-center gap-2 px-6 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {extractMutation.isPending ? (
              <>
                <Loader2 className="animate-spin" size={18} />
                Extracting...
              </>
            ) : (
              <>
                <Upload size={18} />
                Extract Problems
              </>
            )}
          </button>
        </form>
      </div>

      {/* Results */}
      {extractMutation.data && <ResultDisplay result={extractMutation.data} />}

      {extractMutation.error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700">
          <strong>Error:</strong> {(extractMutation.error as Error).message}
        </div>
      )}
    </div>
  );
}
