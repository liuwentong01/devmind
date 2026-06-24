// P0 通用对话客户端（复用共享 SSE 解析）。
import { parseSSEStream } from './sse';

export interface ApiChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface StreamCallbacks {
  onToken: (token: string) => void;
  onTool: (tool: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

export async function streamChat(
  messages: ApiChatMessage[],
  cb: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  await parseSSEStream(
    '/api/chat/stream',
    { messages },
    {
      token: (data) => cb.onToken(data),
      tool: (data) => cb.onTool(data),
      done: () => cb.onDone(),
      error: (data) => cb.onError(data),
    },
    signal,
  );
}
