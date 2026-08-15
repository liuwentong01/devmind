// 知识库 API：文档管理（REST）+ RAG 问答（SSE）。KnowledgePage 使用 CRUD 函数，
// queryKnowledge 则把 sources/token/error/done 回调交给页面。
import { parseSSEStream } from './sse';
import { jsonRequest, requestJson, requestVoid } from './client';

export interface DocumentItem {
  id: number;
  title: string;
  source: string;
  chunk_count: number;
  created_at: string;
}

export interface Source {
  index: number;
  document_id: number;
  title: string;
  score: number;
  snippet: string;
}

export interface QueryCallbacks {
  onSources: (sources: Source[]) => void;
  onToken: (token: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

export async function listDocuments(): Promise<DocumentItem[]> {
  return requestJson<DocumentItem[]>('/api/knowledge/documents', {}, '加载文档失败');
}

export async function ingestDocument(
  title: string,
  content: string,
  source: string,
): Promise<void> {
  await requestVoid(
    '/api/knowledge/documents',
    jsonRequest('POST', { title, content, source }),
    '入库失败',
  );
}

export async function deleteDocument(id: number): Promise<void> {
  await requestVoid(`/api/knowledge/documents/${id}`, { method: 'DELETE' }, '删除失败');
}

export async function queryKnowledge(
  question: string,
  cb: QueryCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  await parseSSEStream(
    '/api/knowledge/query/stream',
    { question },
    {
      sources: (data) => cb.onSources(JSON.parse(data) as Source[]),
      token: (data) => cb.onToken(data),
      done: () => cb.onDone(),
      error: (data) => cb.onError(data),
    },
    signal,
  );
}
