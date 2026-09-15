/**
 * 재생에너지 입지타당성 검토 시스템 — API 클라이언트
 *
 * 입지검토 화면은 자체 fetch 경로(features/windsite/api.ts)를 쓴다. 여기에는
 * 화면 공통으로 필요한 인증만 남긴다.
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

// ─── Auth API ───
export const authApi = {
  login: (data: { email: string; password: string }) =>
    request<{
      message: string;
      user: {
        id: string; email: string; name: string;
        department: string; role: string;
      };
    }>('/users/login/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
};
