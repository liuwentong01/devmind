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
