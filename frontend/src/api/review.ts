// 代码审查 API：手动流式审查（SSE）+ 触发异步 PR 审查。
// trigger 返回只代表已入队；真正的 GitHub 结果由 worker 异步回写。
import { parseSSEStream } from './sse';
import { jsonRequest, requestJson } from './client';

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
  const data = await requestJson<{ job_id?: string }>(
    '/api/review/trigger',
    jsonRequest('POST', { owner, repo, number }),
    '触发失败',
  );
  return data.job_id ?? '';
}
