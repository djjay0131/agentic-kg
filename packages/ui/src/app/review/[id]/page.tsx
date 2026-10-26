'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { AlertCircle, ArrowLeft, Check, X } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';

function VerdictBadge({ verdict, abstained }: { verdict: string; abstained: boolean }) {
  const classes = verdict === 'admit'
    ? 'bg-green-100 text-green-800'
    : verdict.startsWith('merge-into:')
      ? 'bg-purple-100 text-purple-800'
      : 'bg-gray-100 text-gray-700';
  return (
    <span className={`badge ${classes}`}>
      {verdict}{abstained ? ' (abstained)' : ''}
    </span>
  );
}

function formatTime(value: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export default function ReviewDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const queryClient = useQueryClient();
  const [note, setNote] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  const { data: item, isLoading, error } = useQuery({
    queryKey: ['review-item', id],
    queryFn: () => api.getReview(id),
  });

  const decide = useMutation({
    mutationFn: (decision: 'approve' | 'reject') => api.decideReview(id, decision, note),
    onSuccess: (updated) => {
      setMessage(`Decision recorded: ${updated.status}.`);
      queryClient.setQueryData(['review-item', id], updated);
      queryClient.invalidateQueries({ queryKey: ['review'] });
    },
    onError: (err: Error) => {
      setMessage(`Decision failed: ${err.message}`);
    },
  });

  if (isLoading) {
    return <div className="max-w-4xl mx-auto p-8 text-center text-gray-500">Loading...</div>;
  }

  if (error || !item) {
    return (
      <div className="max-w-4xl mx-auto p-8 text-center">
        <AlertCircle className="mx-auto mb-4 text-red-500" size={48} />
        <h2 className="text-xl font-semibold text-gray-900 mb-2">Review item not found</h2>
        <p className="text-gray-500 mb-4">This item may belong to another namespace.</p>
        <Link href="/review" className="text-primary-600 hover:underline">
          Back to the review queue
        </Link>
      </div>
    );
  }

  const pending = item.status === 'pending';

  return (
    <div className="max-w-4xl mx-auto">
      <Link
        href="/review"
        className="inline-flex items-center gap-2 text-gray-500 hover:text-gray-700 mb-6"
      >
        <ArrowLeft size={18} />
        Back to review queue
      </Link>

      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <p className="text-xs uppercase tracking-wider text-gray-500">
              {item.entity_type || item.candidate_kind}
            </p>
            <h1 className="text-xl font-semibold text-gray-900 mt-1 break-words">
              {item.candidate_summary}
            </h1>
            <p className="text-xs font-mono text-gray-400 mt-2 break-all">
              {item.candidate_id}
            </p>
          </div>
          <span className={`badge ${item.status === 'pending' ? 'bg-yellow-100 text-yellow-800' : item.status === 'approved' ? 'bg-green-100 text-green-800' : 'bg-gray-200 text-gray-700'}`}>
            {item.status}
          </span>
        </div>

        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm mt-6">
          <div>
            <dt className="text-gray-500">Run</dt>
            <dd className="font-mono text-xs text-gray-800 break-all">
              <Link href={`/runs/${encodeURIComponent(item.run_id)}`} className="hover:text-primary-600">
                {item.run_id}
              </Link>
            </dd>
          </div>
          <div>
            <dt className="text-gray-500">Created</dt>
            <dd className="text-gray-900">{formatTime(item.created_at)}</dd>
          </div>
          <div>
            <dt className="text-gray-500">Adviser verdict</dt>
            <dd className="mt-1">
              <VerdictBadge verdict={item.adviser_verdict} abstained={item.adviser_abstained} />
            </dd>
          </div>
          <div>
            <dt className="text-gray-500">Confidence</dt>
            <dd className="text-gray-900">
              {item.confidence === null ? '— (abstained / no score)' : item.confidence.toFixed(3)}
            </dd>
          </div>
          <div>
            <dt className="text-gray-500">Adviser</dt>
            <dd className="text-gray-900 break-all">
              {item.adviser_type} {item.adviser_version} · {item.adviser_model_id}{' '}
              {item.adviser_model_version} · prompt {item.prompt_version}
            </dd>
          </div>
          <div>
            <dt className="text-gray-500">Namespace</dt>
            <dd className="text-gray-900">{item.namespace}</dd>
          </div>
        </dl>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
        <h2 className="text-sm font-medium text-gray-500 mb-3">Adviser rationale</h2>
        <p className="text-sm text-gray-800 whitespace-pre-wrap">
          {item.rationale || 'No rationale was returned.'}
        </p>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
        <h2 className="text-sm font-medium text-gray-500 mb-3">
          Evidence ({item.evidence_refs.length})
        </h2>
        {item.evidence_refs.length === 0 ? (
          <p className="text-sm text-gray-500">No evidence references were recorded.</p>
        ) : (
          <ul className="space-y-4">
            {item.evidence_refs.map((ref) => (
              <li key={ref.evidence_id} className="border-l-2 border-gray-200 pl-4">
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span className="font-mono break-all">{ref.evidence_id}</span>
                  {ref.relationship && (
                    <span className="badge bg-gray-100 text-gray-700">{ref.relationship}</span>
                  )}
                </div>
                <blockquote className="mt-2 text-sm text-gray-800 whitespace-pre-wrap">
                  {ref.content || 'Evidence text was not available in the ledger.'}
                </blockquote>
              </li>
            ))}
          </ul>
        )}
      </div>

      {item.status !== 'pending' && (
        <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
          <h2 className="text-sm font-medium text-gray-500 mb-3">Decision</h2>
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-gray-500">Decided by</dt>
              <dd className="text-gray-900">{item.decided_by || '—'}</dd>
            </div>
            <div>
              <dt className="text-gray-500">Decided at</dt>
              <dd className="text-gray-900">{formatTime(item.decided_at)}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-gray-500">Note</dt>
              <dd className="text-gray-900 whitespace-pre-wrap">
                {item.decision_note || '—'}
              </dd>
            </div>
            {item.committed_identity_ids.length > 0 && (
              <div className="sm:col-span-2">
                <dt className="text-gray-500">Committed identities</dt>
                <dd className="font-mono text-xs text-gray-800 break-all">
                  {item.committed_identity_ids.join(', ')}
                </dd>
              </div>
            )}
          </dl>
        </div>
      )}

      {pending && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h2 className="text-sm font-medium text-gray-500 mb-3">Decision</h2>
          {!item.committable && (
            <p className="mb-4 rounded-lg bg-orange-50 border border-orange-200 p-3 text-sm text-orange-800">
              This item cannot be approved: {item.commit_blocker}. You can still
              reject it to record the decision.
            </p>
          )}
          <label className="block text-sm text-gray-600 mb-2">
            Note (stored on the append-only decision record)
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={3}
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900"
              placeholder="Why are you approving or rejecting this candidate?"
            />
          </label>
          {message && (
            <p className="mb-3 text-sm text-gray-700">{message}</p>
          )}
          <div className="flex gap-3">
            <button
              onClick={() => decide.mutate('approve')}
              disabled={decide.isPending || !item.committable}
              className="inline-flex items-center gap-2 rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
            >
              <Check size={16} />
              {decide.isPending ? 'Saving...' : 'Approve and commit'}
            </button>
            <button
              onClick={() => decide.mutate('reject')}
              disabled={decide.isPending}
              className="inline-flex items-center gap-2 rounded-lg bg-gray-700 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
            >
              <X size={16} />
              Reject
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
