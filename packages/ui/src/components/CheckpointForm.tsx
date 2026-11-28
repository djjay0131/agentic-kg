'use client';

import { useState } from 'react';

interface CheckpointFormProps {
  checkpointType: string;
  data: Record<string, unknown>;
  onSubmit: (decision: string, feedback: string, editedData?: Record<string, unknown>) => void;
  loading?: boolean;
}

interface ProposalStep {
  step_number?: number;
  description?: string;
  expected_output?: string;
}

function RawJson({ value }: { value: unknown }) {
  return (
    <details className="mt-3">
      <summary className="cursor-pointer text-xs text-gray-500 hover:text-gray-700">
        Raw JSON
      </summary>
      <pre className="bg-white rounded p-3 text-xs overflow-auto max-h-48 mt-2 border">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

function ProposalSummary({ proposal }: { proposal: Record<string, unknown> }) {
  const steps = Array.isArray(proposal.experimental_steps)
    ? (proposal.experimental_steps as ProposalStep[])
    : [];
  const metrics = Array.isArray(proposal.metrics_to_evaluate)
    ? (proposal.metrics_to_evaluate as unknown[]).map(String)
    : [];
  const resources = Array.isArray(proposal.required_resources)
    ? (proposal.required_resources as unknown[]).map(String)
    : [];
  const confidence =
    typeof proposal.confidence === 'number' ? proposal.confidence : null;

  return (
    <div className="bg-white rounded border p-4 mb-4 space-y-3 text-sm">
      <div>
        <h4 className="text-base font-semibold text-gray-900">
          {String(proposal.title || 'Untitled proposal')}
        </h4>
        {confidence !== null && (
          <span className="inline-block mt-1 text-xs bg-gray-100 px-2 py-0.5 rounded">
            Confidence: {confidence.toFixed(2)}
          </span>
        )}
      </div>

      {proposal.methodology ? (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Methodology
          </p>
          <p className="text-gray-800 whitespace-pre-wrap">
            {String(proposal.methodology)}
          </p>
        </div>
      ) : null}

      {proposal.expected_outcome ? (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Expected outcome
          </p>
          <p className="text-gray-800 whitespace-pre-wrap">
            {String(proposal.expected_outcome)}
          </p>
        </div>
      ) : null}

      {steps.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Steps
          </p>
          <ol className="list-decimal list-inside text-gray-800 space-y-1">
            {steps.map((step, index) => (
              <li key={index}>
                {step.description || `Step ${step.step_number ?? index + 1}`}
                {step.expected_output ? (
                  <span className="text-gray-500"> — {step.expected_output}</span>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      )}

      {metrics.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Metrics
          </p>
          <ul className="list-disc list-inside text-gray-800">
            {metrics.map((metric, index) => (
              <li key={index}>{metric}</li>
            ))}
          </ul>
        </div>
      )}

      {resources.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            Required resources
          </p>
          <p className="text-gray-800">{resources.join(', ')}</p>
        </div>
      )}

      <RawJson value={proposal} />
    </div>
  );
}

export default function CheckpointForm({
  checkpointType,
  data,
  onSubmit,
  loading = false,
}: CheckpointFormProps) {
  const [feedback, setFeedback] = useState('');
  const [selectedProblemId, setSelectedProblemId] = useState<string>('');

  const isSelectProblem = checkpointType === 'select_problem';
  const isApproveProposal = checkpointType === 'approve_proposal';
  const rankedProblems = (data.ranked_problems as Record<string, unknown>[]) || [];
  const proposal = (data.proposal as Record<string, unknown> | null) || null;

  return (
    <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
      <h3 className="text-lg font-semibold text-yellow-800 mb-3">
        Checkpoint: {checkpointType.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
      </h3>

      {isSelectProblem && rankedProblems.length > 0 && (
        <div className="mb-4 space-y-2 max-h-80 overflow-y-auto">
          {rankedProblems.map((p, i) => {
            const score = typeof p.score === 'number' ? p.score : null;
            const domain = p.domain ? String(p.domain) : null;
            const rationale = p.rationale ? String(p.rationale) : null;
            return (
              <label
                key={String(p.problem_id || i)}
                className={`block p-3 border rounded-lg cursor-pointer transition-colors ${
                  selectedProblemId === String(p.problem_id)
                    ? 'border-blue-500 bg-blue-50'
                    : 'border-gray-200 hover:bg-gray-50'
                }`}
              >
                <input
                  type="radio"
                  name="problem"
                  value={String(p.problem_id)}
                  onChange={(e) => setSelectedProblemId(e.target.value)}
                  className="mr-2"
                />
                <span className="font-medium">#{i + 1}</span>
                {' — '}
                <span className="text-sm font-medium text-gray-900">
                  {String(p.statement || p.problem_id || '')}
                </span>
                <div className="mt-1 ml-5 flex flex-wrap items-center gap-2 text-xs text-gray-600">
                  {domain && (
                    <span className="bg-gray-100 px-2 py-0.5 rounded">
                      Domain: {domain}
                    </span>
                  )}
                  {score !== null && (
                    <span className="bg-gray-100 px-2 py-0.5 rounded">
                      Score: {score.toFixed(2)}
                    </span>
                  )}
                </div>
                {rationale && (
                  <p className="mt-1 ml-5 text-xs text-gray-500">{rationale}</p>
                )}
              </label>
            );
          })}
        </div>
      )}

      {isApproveProposal && proposal && <ProposalSummary proposal={proposal} />}

      {!isSelectProblem && !isApproveProposal && (
        <pre className="bg-white rounded p-3 text-xs overflow-auto max-h-48 mb-4 border">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}

      <div className="mb-4">
        <label className="block text-sm font-medium text-gray-700 mb-1">
          Feedback (optional)
        </label>
        <textarea
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          rows={2}
          className="w-full border rounded-lg p-2 text-sm"
          placeholder="Add notes or feedback..."
        />
      </div>

      <div className="flex gap-3">
        <button
          onClick={() => {
            const editedData = isSelectProblem && selectedProblemId
              ? { problem_id: selectedProblemId }
              : undefined;
            onSubmit('approve', feedback, editedData);
          }}
          disabled={loading || (isSelectProblem && !selectedProblemId)}
          className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 text-sm font-medium"
        >
          {loading ? 'Submitting...' : 'Approve'}
        </button>
        <button
          onClick={() => onSubmit('reject', feedback)}
          disabled={loading}
          className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 text-sm font-medium"
        >
          Reject
        </button>
      </div>
    </div>
  );
}
