import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { ConfirmResult, TaskItem } from '../api/requirement';

interface RequirementState {
  threadId: string;
  tasks: TaskItem[];
  planning: boolean;
  confirming: boolean;
  result: ConfirmResult | null;
  error: string | null;
}

const initialState: RequirementState = {
  threadId: '',
  tasks: [],
  planning: false,
  confirming: false,
  result: null,
  error: null,
};

const requirementSlice = createSlice({
  name: 'requirement',
  initialState,
  reducers: {
    startPlan(state) {
      state.planning = true;
      state.error = null;
      state.result = null;
    },
    setPlan(state, action: PayloadAction<{ threadId: string; tasks: TaskItem[] }>) {
      state.threadId = action.payload.threadId;
      state.tasks = action.payload.tasks;
      state.planning = false;
    },
    // HITL：人工编辑提议任务
    updateTask(state, action: PayloadAction<{ index: number; patch: Partial<TaskItem> }>) {
      const { index, patch } = action.payload;
      if (state.tasks[index]) state.tasks[index] = { ...state.tasks[index], ...patch };
    },
    removeTask(state, action: PayloadAction<number>) {
      state.tasks.splice(action.payload, 1);
    },
    setConfirming(state, action: PayloadAction<boolean>) {
      state.confirming = action.payload;
    },
    setResult(state, action: PayloadAction<ConfirmResult>) {
      state.result = action.payload;
      state.confirming = false;
    },
    setError(state, action: PayloadAction<string>) {
      state.error = action.payload;
      state.planning = false;
      state.confirming = false;
    },
    reset() {
      return initialState;
    },
  },
});

export const {
  startPlan,
  setPlan,
  updateTask,
  removeTask,
  setConfirming,
  setResult,
  setError,
  reset,
} = requirementSlice.actions;

export default requirementSlice.reducer;
