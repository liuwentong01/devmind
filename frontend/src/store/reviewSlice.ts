// P2 手动审查状态：保存 diff、命中的团队规则数量、流式 Review 与错误。
// GitHub Webhook 的后台任务不在浏览器 Redux 中持久化。
import { createSlice, type PayloadAction } from '@reduxjs/toolkit';

interface ReviewState {
  diff: string;
  review: string;
  rulesUsed: number;
  reviewing: boolean;
  error: string | null;
}

const initialState: ReviewState = {
  diff: '',
  review: '',
  rulesUsed: 0,
  reviewing: false,
  error: null,
};

const reviewSlice = createSlice({
  name: 'review',
  initialState,
  reducers: {
    setDiff(state, action: PayloadAction<string>) {
      state.diff = action.payload;
    },
    startReview(state) {
      state.review = '';
      state.rulesUsed = 0;
      state.reviewing = true;
      state.error = null;
    },
    setRulesUsed(state, action: PayloadAction<number>) {
      state.rulesUsed = action.payload;
    },
    appendReviewToken(state, action: PayloadAction<string>) {
      state.review += action.payload;
    },
    finishReview(state) {
      state.reviewing = false;
    },
    setReviewError(state, action: PayloadAction<string>) {
      state.error = action.payload;
      state.reviewing = false;
    },
  },
});

export const {
  setDiff,
  startReview,
  setRulesUsed,
  appendReviewToken,
  finishReview,
  setReviewError,
} = reviewSlice.actions;

export default reviewSlice.reducer;
