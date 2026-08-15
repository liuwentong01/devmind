// 统一 API client 的安全/错误契约回归：同源 URL、timeout、HTTP 错误与 JSON 解析。
// 运行：npm test -- client.test.ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiFetch, apiUrl, normalizeApiBaseUrl, requestJson } from './client';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api client', () => {
  it('keeps relative API routes on the current origin', () => {
    expect(apiUrl('/api/health/live')).toBe('/api/health/live');
    expect(() => apiUrl('https://attacker.example/api')).toThrow('API path 必须是相对路径');
    expect(normalizeApiBaseUrl('/gateway/')).toBe('/gateway');
    expect(() => normalizeApiBaseUrl('https://attacker.example')).toThrow(
      '只能是同源相对路径',
    );
    expect(() => normalizeApiBaseUrl('//attacker.example')).toThrow('只能是同源相对路径');
  });

  it('uses same-origin credentials and preserves request headers', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetchMock);

    await apiFetch('/api/example', { headers: { 'X-Request-ID': 'trace-1' } });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Headers;
    expect(init.credentials).toBe('same-origin');
    expect(headers.get('Accept')).toBe('application/json');
    expect(headers.get('X-Request-ID')).toBe('trace-1');
  });

  it('turns structured API failures into typed errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: '没有权限' }), {
          status: 403,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );

    await expect(requestJson('/api/admin/cost')).rejects.toEqual(
      expect.objectContaining<ApiError>({ name: 'ApiError', message: '没有权限', status: 403 }),
    );
  });

  it('applies a deadline to ordinary requests', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init: RequestInit) => {
        return new Promise((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => reject(init.signal?.reason), { once: true });
        });
      }),
    );

    await expect(apiFetch('/api/slow', {}, { timeoutMs: 1 })).rejects.toEqual(
      expect.objectContaining<ApiError>({
        name: 'ApiError',
        message: '请求超时，请稍后重试',
        status: 408,
      }),
    );
  });
});
