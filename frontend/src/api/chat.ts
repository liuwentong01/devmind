// P0 通用对话客户端（复用共享 SSE 解析）。ChatPage 传入历史和事件回调，
// 本模块负责对齐后端消息容量并将 tool/token/done/error 分发给 UI。
import { parseSSEStream } from './sse';

export interface ApiChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface StreamCallbacks {
  onToken: (token: string) => void;
  onTool: (tool: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

const MAX_HISTORY_MESSAGES = 50;
const MAX_MESSAGE_CHARS = 16_000;
const MAX_HISTORY_CHARS = 64_000;

export function buildChatHistory(
  previous: ApiChatMessage[],
  currentUserMessage: string,
): { history: ApiChatMessage[]; truncated: boolean } {
  const current = currentUserMessage.trim().slice(0, MAX_MESSAGE_CHARS);
  const selected: ApiChatMessage[] = [];
  let remainingChars = MAX_HISTORY_CHARS - current.length;
  let considered = 0;

  for (let index = previous.length - 1; index >= 0; index -= 1) {
    const original = previous[index].content.trim();
    if (!original) continue;
    considered += 1;
    if (selected.length >= MAX_HISTORY_MESSAGES - 1 || remainingChars <= 0) break;

    const bounded = original.slice(0, MAX_MESSAGE_CHARS);
    const content =
      bounded.length <= remainingChars ? bounded : bounded.slice(bounded.length - remainingChars);
    if (content) {
      selected.unshift({ role: previous[index].role, content });
      remainingChars -= content.length;
    }
    if (content.length < bounded.length) break;
  }

  const validPreviousCount = previous.filter((item) => item.content.trim()).length;
  return {
    history: [...selected, { role: 'user', content: current }],
    truncated:
      current !== currentUserMessage.trim() ||
      selected.length < validPreviousCount ||
      considered < validPreviousCount,
  };
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
