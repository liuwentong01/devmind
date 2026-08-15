// P5 代码理解状态：记录仓库、图谱查询上下文、流式回答和影响分析结果。
// Neo4j 图谱才是持久化事实，刷新页面后可重新查询。
import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { ImpactResult, IndexStats } from '../api/codebase';

interface CodebaseState {
  repo: string;
  stats: IndexStats | null;
  indexing: boolean;
  // 问答
  contextFiles: string[];
  answer: string;
  querying: boolean;
  // 影响分析
  impact: ImpactResult | null;
  impactLoading: boolean;
  error: string | null;
}

const initialState: CodebaseState = {
  repo: '',
  stats: null,
  indexing: false,
  contextFiles: [],
  answer: '',
  querying: false,
  impact: null,
  impactLoading: false,
  error: null,
};

const codebaseSlice = createSlice({
  name: 'codebase',
  initialState,
  reducers: {
    setRepo(state, action: PayloadAction<string>) {
      state.repo = action.payload;
    },
    setIndexing(state, action: PayloadAction<boolean>) {
      state.indexing = action.payload;
    },
    setStats(state, action: PayloadAction<IndexStats>) {
      state.stats = action.payload;
    },
    startQuery(state) {
      state.answer = '';
      state.contextFiles = [];
      state.querying = true;
      state.error = null;
    },
    setContextFiles(state, action: PayloadAction<string[]>) {
      state.contextFiles = action.payload;
    },
    appendAnswerToken(state, action: PayloadAction<string>) {
      state.answer += action.payload;
    },
    finishQuery(state) {
      state.querying = false;
    },
    setImpactLoading(state, action: PayloadAction<boolean>) {
      state.impactLoading = action.payload;
    },
    setImpact(state, action: PayloadAction<ImpactResult>) {
      state.impact = action.payload;
    },
    setError(state, action: PayloadAction<string>) {
      state.error = action.payload;
      state.querying = false;
      state.indexing = false;
      state.impactLoading = false;
    },
  },
});

export const {
  setRepo,
  setIndexing,
  setStats,
  startQuery,
  setContextFiles,
  appendAnswerToken,
  finishQuery,
  setImpactLoading,
  setImpact,
  setError,
} = codebaseSlice.actions;

export default codebaseSlice.reducer;
