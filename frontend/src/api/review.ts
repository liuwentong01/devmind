// 代码审查 API：手动流式审查（SSE）+ 触发异步 PR 审查。
import { parseSSEStream } from './sse';

export interface ReviewCallbacks {
  onRules: (count: number) => void;
  onToken: (token: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

export async function reviewDiff(
  diff: string,
  cb: ReviewCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  await parseSSEStream(
    '/api/review/stream',
    { diff },
    {
      rules: (data) => cb.onRules((JSON.parse(data) as { count: number }).count),
      token: (data) => cb.onToken(data),
      done: () => cb.onDone(),
      error: (data) => cb.onError(data),
    },
    signal,
  );
}

export async function triggerPrReview(
  owner: string,
  repo: string,
  number: number,
): Promise<string> {
  const resp = await fetch('/api/review/trigger', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ owner, repo, number }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `触发失败：HTTP ${resp.status}`);
  return data.job_id ?? '';
}
