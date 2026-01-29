'use client';

const STEPS = [
  { key: 'ranking', label: 'Ranking' },
  { key: 'select_problem', label: 'Select Problem' },
  { key: 'continuation', label: 'Continuation' },
  { key: 'approve_proposal', label: 'Approve Proposal' },
  { key: 'evaluation', label: 'Evaluation' },
  { key: 'review_evaluation', label: 'Review' },
  { key: 'synthesis', label: 'Synthesis' },
];

interface WorkflowStepperProps {
  currentStep: string;
  status: string;
}

export default function WorkflowStepper({ currentStep, status }: WorkflowStepperProps) {
  const currentIndex = STEPS.findIndex((s) => s.key === currentStep);

  return (
    <div className="flex items-center gap-1 overflow-x-auto py-4">
      {STEPS.map((step, i) => {
        let state: 'completed' | 'active' | 'pending' = 'pending';
        if (i < currentIndex || status === 'completed') state = 'completed';
        else if (i === currentIndex) state = 'active';

        return (
          <div key={step.key} className="flex items-center">
            <div className="flex flex-col items-center min-w-[80px]">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
                  state === 'completed'
                    ? 'bg-green-500 text-white'
                    : state === 'active'
                    ? 'bg-blue-500 text-white ring-4 ring-blue-100'
                    : 'bg-gray-200 text-gray-500'
                }`}
              >
                {state === 'completed' ? '\u2713' : i + 1}
              </div>
              <span
                className={`text-xs mt-1 text-center ${
                  state === 'active' ? 'font-semibold text-blue-700' : 'text-gray-500'
                }`}
              >
                {step.label}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <div
                className={`w-8 h-0.5 mt-[-16px] ${
                  i < currentIndex || status === 'completed' ? 'bg-green-400' : 'bg-gray-200'
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
