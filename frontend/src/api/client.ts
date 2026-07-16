/**
 * 재생E AI Agent — API 클라이언트
 */

const API_BASE = import.meta.env.VITE_API_BASE || '/api';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers as Record<string, string>,
    },
    ...options,
  });

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.error || `API Error: ${res.status}`);
  }

  return res.json();
}

// ─── Types ───
export interface Conversation {
  id: string;
  project: string | null;
  title: string;
  use_internal_docs: boolean;
  is_shared: boolean;
  last_message_at: string | null;
  created_at: string;
  message_count: number;
  last_message_preview: string | null;
}

export interface MessageSource {
  id: string;
  document_id: string | null;
  document_chunk_id: string | null;
  display_title: string;
  short_label: string;
  page_number: number | null;
  location_label: string;
  score: number | null;
  rank: number | null;
  snippet: string;
  open_url: string | null;
}

export interface Message {
  id: string;
  conversation: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  used_internal_docs: boolean;
  model: string;
  status: string;
  created_at: string;
  sources: MessageSource[];
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface ContractTemplate {
  id: string;
  code: string;
  name_ko: string;
  name_en: string;
  category: string;
  description: string;
  key_term_schema: any[];
  version: string;
  is_active: boolean;
}

export interface ContractDraft {
  id: string;
  template: string;
  template_name: string;
  template_code: string;
  title: string;
  key_terms: Record<string, string>;
  generated_content: string;
  output_file_uri: string;
  status: string;
  created_at: string;
}

export interface ReviewFinding {
  id: string;
  clause_ref: string;
  severity: 'high' | 'mid' | 'low';
  category: string;
  finding: string;
  suggestion: string;
}

export interface ContractReview {
  id: string;
  title: string;
  template_name: string;
  review_instruction: string;
  summary: string;
  status: string;
  findings: ReviewFinding[];
  created_at: string;
}

// ─── Conversations API ───
export const conversationsApi = {
  list: () => request<{ results: Conversation[] }>('/conversations/'),

  get: (id: string) => request<ConversationDetail>(`/conversations/${id}/`),

  create: (data: { project?: string; title?: string }) =>
    request<Conversation>('/conversations/', { method: 'POST', body: JSON.stringify(data) }),

  update: (id: string, data: Partial<Conversation>) =>
    request<Conversation>(`/conversations/${id}/`, { method: 'PATCH', body: JSON.stringify(data) }),

  sendMessage: (id: string, data: { content: string; use_internal_docs?: boolean; stream?: boolean }) =>
    request<Message>(`/conversations/${id}/messages/`, { method: 'POST', body: JSON.stringify(data) }),

  sendMessageStream: async (
    id: string,
    data: { content: string; use_internal_docs?: boolean; stream: true },
    onChunk: (chunk: any) => void
  ) => {
    const response = await fetch(`${API_BASE}/conversations/${id}/messages/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    if (!response.body) throw new Error('ReadableStream not yet supported in this browser.');

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunkString = decoder.decode(value, { stream: true });
      const lines = chunkString.split('\n');
      for (const line of lines) {
        if (line.trim()) {
          try {
            const parsed = JSON.parse(line);
            onChunk(parsed);
          } catch (e) {
            console.error('Failed to parse chunk:', line);
          }
        }
      }
    }
  },

  share: (id: string, data: { project_id: string; share_type?: string }) =>
    request(`/conversations/${id}/share/`, { method: 'POST', body: JSON.stringify(data) }),
};

// ─── Contracts API ───
export const contractsApi = {
  getTemplates: () => request<{ results: ContractTemplate[] }>('/contract-templates/'),

  getTemplate: (code: string) => request<ContractTemplate>(`/contract-templates/${code}/`),

  createDraft: (data: { template_code: string; key_terms: Record<string, string>; title?: string }) =>
    request<ContractDraft>('/contracts/drafts/', { method: 'POST', body: JSON.stringify(data) }),

  getDraft: (id: string) => request<ContractDraft>(`/contracts/drafts/${id}/`),

  downloadDraft: (id: string) => `${API_BASE}/contracts/drafts/${id}/download/`,

  createReview: (formData: FormData) =>
    fetch(`${API_BASE}/contracts/reviews/`, { method: 'POST', body: formData })
      .then(res => res.json()) as Promise<ContractReview>,

  getReview: (id: string) => request<ContractReview>(`/contracts/reviews/${id}/`),

  downloadReview: (id: string) => `${API_BASE}/contracts/reviews/${id}/download/`,
};

// ─── Documents API ───
export const documentsApi = {
  upload: (formData: FormData) =>
    fetch(`${API_BASE}/documents/`, { method: 'POST', body: formData })
      .then(res => res.json()),

  getFileUrl: (id: string, page?: number) => {
    let url = `${API_BASE}/documents/${id}/file/`;
    if (page) url += `?page=${page}`;
    return url;
  },

  chunks: (id: string) =>
    request<any[]>(`/documents/${id}/chunks/`),
};

// ─── Auth API ───
export const authApi = {
  login: (data: { email: string; password: string }) =>
    request<{ message: string; user: { id: string; email: string; name: string; department: string; role: string } }>('/users/login/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
};
