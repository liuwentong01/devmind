// 带 RootState/AppDispatch 类型的 Redux hooks；页面统一使用它们，避免重复类型断言。
// 示例：const streaming = useAppSelector((state) => state.chat.streaming)。
import { useDispatch, useSelector } from 'react-redux';
import type { AppDispatch, RootState } from './index';

// 带类型的 Redux hooks，避免每个组件重复声明类型
export const useAppDispatch = useDispatch.withTypes<AppDispatch>();
export const useAppSelector = useSelector.withTypes<RootState>();
