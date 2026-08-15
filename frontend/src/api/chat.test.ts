// 对话历史裁剪回归：验证数量、单条和总字符边界，以及优先保留最近上下文。
// 运行：npm test -- chat.test.ts
import { describe, expect, it } from 'vitest';
import { buildChatHistory, type ApiChatMessage } from './chat';

describe('buildChatHistory', () => {
  it('keeps at most 50 non-empty messages with the current user message last', () => {
    const previous: ApiChatMessage[] = Array.from({ length: 70 }, (_, index) => ({
      role: index % 2 === 0 ? 'user' : 'assistant',
      content: `message-${index}`,
    }));

    const result = buildChatHistory(previous, 'new question');

    expect(result.history).toHaveLength(50);
    expect(result.history[result.history.length - 1]).toEqual({
      role: 'user',
      content: 'new question',
    });
    expect(result.truncated).toBe(true);
  });

  it('stays within per-message and total character limits', () => {
    const previous: ApiChatMessage[] = Array.from({ length: 6 }, (_, index) => ({
      role: index % 2 === 0 ? 'user' : 'assistant',
      content: String(index).repeat(20_000),
    }));

    const result = buildChatHistory(previous, 'x'.repeat(20_000));
    const total = result.history.reduce((sum, item) => sum + item.content.length, 0);

    expect(result.history.every((item) => item.content.length <= 16_000)).toBe(true);
    expect(total).toBeLessThanOrEqual(64_000);
    expect(result.history[result.history.length - 1]?.content).toHaveLength(16_000);
  });
});
