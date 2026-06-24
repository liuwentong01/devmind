// 代码理解 API：索引仓库 + 代码问答(SSE) + 影响分析。
import { parseSSEStream } from './sse';

export interface IndexStats {
  repo: string;
  files: number;
  functions: number;
  calls: number;
}

export interface CalleeRef {
  name: string;
  path: string;
}

export interface ImpactResult {
  callers: CalleeRef[];
  callees: CalleeRef[];
  summary: string;
}

export interface CodeQaCallbacks {
  onContext: (files: string[]) => void;
  onToken: (token: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

export async function indexRepo(repo: string, path: string): Promise<IndexStats> {
  const resp = await fetch('/api/codebase/index', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo, path }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `索引失败：HTTP ${resp.status}`);
  return data as IndexStats;
}

export async function queryCodebase(
  repo: string,
  question: string,
  cb: CodeQaCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  await parseSSEStream(
    '/api/codebase/query/stream',
    { repo, question },
    {
      context: (data) => cb.onContext((JSON.parse(data) as { files: string[] }).files),
      token: (data) => cb.onToken(data),
      done: () => cb.onDone(),
      error: (data) => cb.onError(data),
    },
    signal,
  );
}

export async function analyzeImpact(
  repo: string,
  func: string,
  depth = 3,
): Promise<ImpactResult> {
  const resp = await fetch('/api/codebase/impact', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo, function: func, depth }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `分析失败：HTTP ${resp.status}`);
  return data as ImpactResult;
}
