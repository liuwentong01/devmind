// P1 知识问答交互状态：保存问题、流式答案、引用来源和 loading/error。
// 文档列表本身由页面请求后写入；后端仍是数据事实来源。
import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { DocumentItem, Source } from '../api/knowledge';

interface KnowledgeState {
  documents: DocumentItem[];
  docsLoading: boolean;
  // 问答区
  question: string;
  answer: string;
  sources: Source[];
  querying: boolean;
  error: string | null;
}

const initialState: KnowledgeState = {
  documents: [],
  docsLoading: false,
  question: '',
  answer: '',
  sources: [],
  querying: false,
  error: null,
};

const knowledgeSlice = createSlice({
  name: 'knowledge',
  initialState,
  reducers: {
    setDocuments(state, action: PayloadAction<DocumentItem[]>) {
      state.documents = action.payload;
    },
    setDocsLoading(state, action: PayloadAction<boolean>) {
      state.docsLoading = action.payload;
    },
    // 发起一次问答：清空上次结果
    startQuery(state, action: PayloadAction<string>) {
      state.question = action.payload;
      state.answer = '';
      state.sources = [];
      state.querying = true;
      state.error = null;
    },
    setSources(state, action: PayloadAction<Source[]>) {
      state.sources = action.payload;
    },
    appendAnswerToken(state, action: PayloadAction<string>) {
      state.answer += action.payload;
    },
    finishQuery(state) {
      state.querying = false;
    },
    setQueryError(state, action: PayloadAction<string>) {
      state.error = action.payload;
      state.querying = false;
    },
  },
});

export const {
  setDocuments,
  setDocsLoading,
  startQuery,
  setSources,
  appendAnswerToken,
  finishQuery,
  setQueryError,
} = knowledgeSlice.actions;

export default knowledgeSlice.reducer;
