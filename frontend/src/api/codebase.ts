// 代码理解 API：索引仓库 + 代码问答(SSE) + 影响分析。
// 索引 path 只作为请求值；服务端仍会解析真实路径并检查允许根目录。
import { parseSSEStream } from './sse';
import { jsonRequest, requestJson } from './client';

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
  return requestJson<IndexStats>(
    '/api/codebase/index',
    jsonRequest('POST', { repo, path }),
    '索引失败',
  );
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
  return requestJson<ImpactResult>(
    '/api/codebase/impact',
    jsonRequest('POST', { repo, function: func, depth }),
    '分析失败',
  );
}
