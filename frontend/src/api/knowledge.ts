// 知识库 API：文档管理（REST）+ RAG 问答（SSE）。
import { parseSSEStream } from './sse';

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
  const resp = await fetch('/api/knowledge/documents');
  if (!resp.ok) throw new Error(`加载文档失败：HTTP ${resp.status}`);
  return resp.json();
}

export async function ingestDocument(title: string, content: string): Promise<void> {
  const resp = await fetch('/api/knowledge/documents', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, content }),
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => ({}));
    throw new Error(detail.detail || `入库失败：HTTP ${resp.status}`);
  }
}

export async function deleteDocument(id: number): Promise<void> {
  const resp = await fetch(`/api/knowledge/documents/${id}`, { method: 'DELETE' });
  if (!resp.ok && resp.status !== 204) throw new Error(`删除失败：HTTP ${resp.status}`);
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
