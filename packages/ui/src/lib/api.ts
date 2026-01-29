/**
 * API client for the Agentic KG backend.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || process.env.API_URL || 'http://localhost:8000';

export interface ProblemSummary {
  id: string;
  statement: string;
  domain: string | null;
  status: string;
  confidence: number | null;
  created_at: string | null;
}

export interface ProblemDetail extends ProblemSummary {
  assumptions: { text: string; implicit: boolean; confidence: number }[];
  constraints: { text: string; type: string; confidence: number }[];
  datasets: { name: string; url: string | null; available: boolean }[];
  metrics: { name: string; description: string | null; baseline_value: number | null }[];
  baselines: { name: string; paper_doi: string | null }[];
  evidence: {
    source_doi: string | null;
    source_title: string | null;
    section: string | null;
    quoted_text: string | null;
  } | null;
  extraction_metadata: {
    extraction_model: string | null;
    confidence_score: number | null;
    extractor_version: string | null;
    human_reviewed: boolean;
  } | null;
  updated_at: string | null;
}

export interface ProblemListResponse {
  problems: ProblemSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface PaperSummary {
  doi: string;
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
}

export interface PaperDetail extends PaperSummary {
  abstract: string | null;
  arxiv_id: string | null;
  pdf_url: string | null;
  citation_count: number | null;
}

export interface PaperListResponse {
  papers: PaperSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface SearchResult {
  problem: ProblemSummary;
  score: number;
  match_type: string;
}

export interface SearchResponse {
  results: SearchResult[];
  query: string;
  total: number;
}

export interface ExtractedProblem {
  statement: string;
  domain: string | null;
  confidence: number;
  quoted_text: string;
}

export interface ExtractResponse {
  success: boolean;
  paper_title: string | null;
  problems_extracted: number;
  relations_found: number;
  duration_ms: number;
  problems: ExtractedProblem[];
  stages: { stage: string; success: boolean; duration_ms: number; error: string | null }[];
}

export interface Stats {
  total_problems: number;
  total_papers: number;
  problems_by_status: Record<string, number>;
  problems_by_domain: Record<string, number>;
}

export interface Health {
  status: string;
  version: string;
  neo4j_connected: boolean;
}

export interface GraphNode {
  id: string;
  label: string;
  type: 'problem' | 'paper' | 'domain';
  properties: Record<string, unknown>;
}

export interface GraphLink {
  source: string;
  target: string;
  type: string;
  properties?: Record<string, unknown>;
}

export interface GraphResponse {
  nodes: GraphNode[];
  links: GraphLink[];
}

// Workflow types
export interface WorkflowStatus {
  run_id: string;
  status: string;
  current_step: string;
  created_at: string;
  updated_at: string;
  total_steps: number;
  completed_steps: number;
}

export interface WorkflowState {
  run_id: string;
  status: string;
  current_step: string;
  ranked_problems: Record<string, unknown>[];
  selected_problem_id: string | null;
  proposal: Record<string, unknown> | null;
  evaluation_result: Record<string, unknown> | null;
  synthesis_report: Record<string, unknown> | null;
  messages: { agent: string; content: string; timestamp: string }[];
  errors: string[];
}

export interface StartWorkflowResponse {
  run_id: string;
  status: string;
  websocket_url: string;
}

async function fetchAPI<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${endpoint}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || 'API request failed');
  }

  return res.json();
}

export const api = {
  // Health & Stats
  health: () => fetchAPI<Health>('/health'),
  stats: () => fetchAPI<Stats>('/api/stats'),

  // Problems
  listProblems: (params?: { status?: string; domain?: string; limit?: number; offset?: number }) => {
    const searchParams = new URLSearchParams();
    if (params?.status) searchParams.set('status', params.status);
    if (params?.domain) searchParams.set('domain', params.domain);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const query = searchParams.toString();
    return fetchAPI<ProblemListResponse>(`/api/problems${query ? `?${query}` : ''}`);
  },

  getProblem: (id: string) => fetchAPI<ProblemDetail>(`/api/problems/${id}`),

  updateProblem: (id: string, data: { status?: string; domain?: string; statement?: string }) =>
    fetchAPI<ProblemDetail>(`/api/problems/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  deleteProblem: (id: string) =>
    fetchAPI<{ deleted: boolean; id: string }>(`/api/problems/${id}`, { method: 'DELETE' }),

  // Papers
  listPapers: (params?: { limit?: number; offset?: number }) => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    const query = searchParams.toString();
    return fetchAPI<PaperListResponse>(`/api/papers${query ? `?${query}` : ''}`);
  },

  getPaper: (doi: string) => fetchAPI<PaperDetail>(`/api/papers/${encodeURIComponent(doi)}`),

  // Search
  search: (query: string, options?: { domain?: string; status?: string; top_k?: number }) =>
    fetchAPI<SearchResponse>('/api/search', {
      method: 'POST',
      body: JSON.stringify({
        query,
        domain: options?.domain,
        status: options?.status,
        top_k: options?.top_k || 20,
      }),
    }),

  // Extraction
  extract: (data: { url?: string; text?: string; title?: string; doi?: string; authors?: string[] }) =>
    fetchAPI<ExtractResponse>('/api/extract', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Graph
  getGraph: (params?: { limit?: number; domain?: string; include_papers?: boolean }) => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.domain) searchParams.set('domain', params.domain);
    if (params?.include_papers !== undefined) searchParams.set('include_papers', String(params.include_papers));
    const query = searchParams.toString();
    return fetchAPI<GraphResponse>(`/api/graph${query ? `?${query}` : ''}`);
  },

  getNeighbors: (nodeId: string, depth?: number) => {
    const searchParams = new URLSearchParams();
    if (depth) searchParams.set('depth', String(depth));
    const query = searchParams.toString();
    return fetchAPI<GraphResponse>(`/api/graph/neighbors/${encodeURIComponent(nodeId)}${query ? `?${query}` : ''}`);
  },

  // Workflows
  startWorkflow: (params?: { domain_filter?: string; status_filter?: string; max_problems?: number }) =>
    fetchAPI<StartWorkflowResponse>('/api/agents/workflows', {
      method: 'POST',
      body: JSON.stringify(params || {}),
    }),

  listWorkflows: () => fetchAPI<WorkflowStatus[]>('/api/agents/workflows'),

  getWorkflow: (runId: string) => fetchAPI<WorkflowState>(`/api/agents/workflows/${runId}`),

  submitCheckpoint: (runId: string, checkpointType: string, decision: string, feedback?: string, editedData?: Record<string, unknown>) =>
    fetchAPI<WorkflowState>(`/api/agents/workflows/${runId}/checkpoints/${checkpointType}`, {
      method: 'POST',
      body: JSON.stringify({ decision, feedback: feedback || '', edited_data: editedData }),
    }),

  cancelWorkflow: (runId: string) =>
    fetchAPI<{ status: string; run_id: string }>(`/api/agents/workflows/${runId}`, { method: 'DELETE' }),
};
