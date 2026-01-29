'use client';

import { useState } from 'react';

interface CheckpointFormProps {
  checkpointType: string;
  data: Record<string, unknown>;
  onSubmit: (decision: string, feedback: string, editedData?: Record<string, unknown>) => void;
  loading?: boolean;
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
  const rankedProblems = (data.ranked_problems as Record<string, unknown>[]) || [];

  return (
    <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
      <h3 className="text-lg font-semibold text-yellow-800 mb-3">
        Checkpoint: {checkpointType.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
      </h3>

      {isSelectProblem && rankedProblems.length > 0 && (
        <div className="mb-4 space-y-2 max-h-60 overflow-y-auto">
          {rankedProblems.map((p, i) => (
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
              {' - '}
              <span className="text-sm">{String(p.rationale || p.problem_id || '')}</span>
              {p.score != null && (
                <span className="ml-2 text-xs bg-gray-100 px-2 py-0.5 rounded">
                  Score: {Number(p.score).toFixed(2)}
                </span>
              )}
            </label>
          ))}
        </div>
      )}

      {!isSelectProblem && (
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
