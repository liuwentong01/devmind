// P3 RCA 状态：把后端 step/token/error 事件转换为用户可见的进度与报告；
// 证据降级 warning 是报告的 token 前缀，不是独立 SSE 事件。
// 页面离开后该状态只存在于浏览器内，不是后台任务的可靠状态存储。
import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { RcaStep } from '../api/incident';

interface IncidentState {
  steps: RcaStep[];
  report: string;
  running: boolean;
  error: string | null;
}

const initialState: IncidentState = {
  steps: [],
  report: '',
  running: false,
  error: null,
};

const incidentSlice = createSlice({
  name: 'incident',
  initialState,
  reducers: {
    startAnalysis(state) {
      state.steps = [];
      state.report = '';
      state.running = true;
      state.error = null;
    },
    addStep(state, action: PayloadAction<RcaStep>) {
      state.steps.push(action.payload);
    },
    appendReportToken(state, action: PayloadAction<string>) {
      state.report += action.payload;
    },
    finishAnalysis(state) {
      state.running = false;
    },
    setAnalysisError(state, action: PayloadAction<string>) {
      state.error = action.payload;
      state.running = false;
    },
  },
});

export const {
  startAnalysis,
  addStep,
  appendReportToken,
  finishAnalysis,
  setAnalysisError,
} = incidentSlice.actions;

export default incidentSlice.reducer;
