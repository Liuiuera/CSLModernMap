import React from 'react';
import stations from '../components/svgs/stations/stations';
import { StationType } from '../constants/stations';
import { useRootSelector } from '../redux';

export const useMakeStationName = () => {
    const mode = useRootSelector(state => state.app.preference.randomStationsNames);
    return React.useCallback(async (type: StationType) => {
        const names = stations[type].defaultAttrs.names;
        return mode === 'empty' ? names.map(() => '') as typeof names : structuredClone(names);
    }, [mode]);
};
