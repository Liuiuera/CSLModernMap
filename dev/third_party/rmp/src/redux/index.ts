import { combineReducers, configureStore } from '@reduxjs/toolkit';
import { enableMapSet } from 'immer';
import { TypedUseSelectorHook, useDispatch, useSelector, useStore } from 'react-redux';
import appReducer from './app/app-slice';
import fontsReducer from './fonts/fonts-slice';
import paramReducer from './param/param-slice';
import runtimeReducer from './runtime/runtime-slice';
import viewportReducer from './viewport/viewport-slice';

enableMapSet();

const rootReducer = combineReducers({
    app: appReducer,
    param: paramReducer,
    runtime: runtimeReducer,
    fonts: fontsReducer,
    viewport: viewportReducer,
});
export type RootState = ReturnType<typeof rootReducer>;

export const createStore = (preloadedState: Partial<RootState> = {}) =>
    configureStore({
        reducer: rootReducer,
        middleware: getDefaultMiddleware =>
            // undo slice contains MultiDirectedGraph instance, it is not meant to be serialized nor persisted
            getDefaultMiddleware({ serializableCheck: false }),
        preloadedState,
    });
const store = createStore();
export type RootStore = typeof store;

export type RootDispatch = typeof store.dispatch;
export const useRootDispatch = () => useDispatch<RootDispatch>();
export const useRootSelector: TypedUseSelectorHook<RootState> = useSelector;
export const useRootStore = () => useStore() as RootStore;

export default store;
