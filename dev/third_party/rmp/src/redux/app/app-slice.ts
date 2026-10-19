import { createSlice, PayloadAction } from '@reduxjs/toolkit';

export type RandomStationsNamesValue = 'default' | 'empty';
export type StationNameTranslationMode = 'pinyin-spaced' | 'pinyin-compact' | 'pinyin-uppercase';

/** Local editing preferences; project files contain graph data only. */
export interface AppState {
    preference: {
        /**
         * Whether to enable parallel for new lines.
         */
        autoParallel: boolean;
        randomStationsNames: RandomStationsNamesValue;
        stationNameTranslationMode: StationNameTranslationMode;
        gridLines: boolean;
        snapLines: boolean;
        predictNextNode: boolean;
        autoChangeStationType: boolean;
        /**
         * Whether to disable warnings.
         */
        disableWarning: {
            /**
             * Whether to disable the change type warning dialog when changing station/line types.
             */
            changeType: boolean;
        };
    };
}

export const initialState: AppState = {
    preference: {
        autoParallel: true,
        randomStationsNames: 'default',
        stationNameTranslationMode: 'pinyin-spaced',
        gridLines: false,
        snapLines: true,
        predictNextNode: true,
        autoChangeStationType: true,
        disableWarning: {
            changeType: false,
        },
    },
};

const appSlice = createSlice({
    name: 'app',
    initialState,
    reducers: {
        setAutoParallel: (state, action: PayloadAction<boolean>) => {
            state.preference.autoParallel = action.payload;
        },
        setRandomStationsNames: (state, action: PayloadAction<RandomStationsNamesValue>) => {
            state.preference.randomStationsNames = action.payload;
        },
        setStationNameTranslationMode: (state, action: PayloadAction<StationNameTranslationMode>) => {
            state.preference.stationNameTranslationMode = action.payload;
        },
        setGridLines: (state, action: PayloadAction<boolean>) => {
            state.preference.gridLines = action.payload;
        },
        setSnapLines: (state, action: PayloadAction<boolean>) => {
            state.preference.snapLines = action.payload;
        },
        setPredictNextNode: (state, action: PayloadAction<boolean>) => {
            state.preference.predictNextNode = action.payload;
        },
        setAutoChangeStationType: (state, action: PayloadAction<boolean>) => {
            state.preference.autoChangeStationType = action.payload;
        },
        setDisableWarningChangeType: (state, action: PayloadAction<boolean>) => {
            state.preference.disableWarning.changeType = action.payload;
        },
    },
});

export const {
    setAutoParallel,
    setRandomStationsNames,
    setStationNameTranslationMode,
    setGridLines,
    setSnapLines,
    setPredictNextNode,
    setAutoChangeStationType,
    setDisableWarningChangeType,
} = appSlice.actions;
export default appSlice.reducer;
