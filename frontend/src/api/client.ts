// 所有 REST/SSE 请求共享的协议层：强制同源相对 API、统一 timeout、错误消息与凭据策略。
// 示例：requestJson('/api/knowledge/documents')；共享 API Key 绝不能从这里写入浏览器。
export function normalizeApiBaseUrl(value: string | undefined): string {
  const configured = value?.trim() ?? '';
  if (!configured) return '';
  if (
    !configured.startsWith('/') ||
    configured.startsWith('//') ||
    configured.includes('\\') ||
    configured.includes('?') ||
    configured.includes('#')
  ) {
    throw new Error('VITE_API_BASE_URL 只能是同源相对路径前缀');
  }
  return configured.replace(/\/+$/, '');
}

const apiBaseUrl = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL);
const DEFAULT_TIMEOUT_MS = 30_000;

interface ApiFetchOptions {
  /** null disables the deadline (used by long-lived SSE requests). */
  timeoutMs?: number | null;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function apiUrl(path: string): string {
  if (/^[a-z][a-z\d+.-]*:/i.test(path) || path.startsWith('//')) {
    throw new Error('API path 必须是相对路径');
  }
  return `${apiBaseUrl}${path.startsWith('/') ? path : `/${path}`}`;
}

/** 把浏览器 timeout 归一化为可展示的 ApiError，其余网络异常保留原始语义。 */
function normalizeNetworkError(error: unknown): never {
  if ((error as Error).name === 'TimeoutError') {
    throw new ApiError('请求超时，请稍后重试', 408);
  }
  throw error;
}

/**
 * 所有浏览器请求统一经过此处，便于集中管理 cookie、追踪头和未来的用户级认证。
 * 共享 API key 绝不能放入 VITE_*：这些变量会成为公开的浏览器 JavaScript。
 */
export async function apiFetch(
  path: string,
  init: RequestInit = {},
  options: ApiFetchOptions = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set('Accept', headers.get('Accept') ?? 'application/json');
  const timeoutMs = options.timeoutMs === undefined ? DEFAULT_TIMEOUT_MS : options.timeoutMs;
  const deadline = timeoutMs === null ? null : AbortSignal.timeout(timeoutMs);
  const signal =
    deadline && init.signal ? AbortSignal.any([init.signal, deadline]) : (deadline ?? init.signal);

  try {
    return await fetch(apiUrl(path), {
      ...init,
      credentials: init.credentials ?? 'same-origin',
      headers,
      signal,
    });
  } catch (error) {
    normalizeNetworkError(error);
  }
}

function validationMessage(detail: unknown): string | null {
  if (typeof detail === 'string') return detail;
  if (!Array.isArray(detail)) return null;

  const messages = detail
    .map((item) => {
      if (!item || typeof item !== 'object') return null;
      const message = 'msg' in item ? item.msg : null;
      return typeof message === 'string' ? message : null;
    })
    .filter((item): item is string => item !== null);
  return messages.length > 0 ? messages.join('；') : null;
}

export async function responseErrorMessage(
  response: Response,
  fallback = '请求失败',
): Promise<string> {
  const text = await response.text().catch(() => '');
  if (text) {
    try {
      const payload = JSON.parse(text) as { detail?: unknown; message?: unknown };
      const detail = validationMessage(payload.detail);
      if (detail) return detail;
      if (typeof payload.message === 'string') return payload.message;
    } catch {
      if (!text.trimStart().startsWith('<')) return text.slice(0, 300);
    }
  }
  return `${fallback}：HTTP ${response.status}`;
}

export async function requestJson<T>(
  path: string,
  init: RequestInit = {},
  fallback?: string,
): Promise<T> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    throw new ApiError(await responseErrorMessage(response, fallback), response.status);
  }

  try {
    return (await response.json()) as T;
  } catch (error) {
    if ((error as Error).name === 'TimeoutError') normalizeNetworkError(error);
    throw new ApiError('服务端返回了无效的 JSON 响应', response.status);
  }
}

export async function requestVoid(
  path: string,
  init: RequestInit = {},
  fallback?: string,
): Promise<void> {
  const response = await apiFetch(path, init);
  if (!response.ok) {
    throw new ApiError(await responseErrorMessage(response, fallback), response.status);
  }
}

export function jsonRequest(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  };
}
