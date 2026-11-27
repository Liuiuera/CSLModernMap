import { StationType } from '../constants/stations';
import i18n from '../i18n/config';
import { stationTypeLosses } from './change-types';

export type StationStyleConfirmation = { message: string; resolve: (accepted: boolean) => void };

export async function confirmStationTypeChange(ids: string[], target: StationType): Promise<boolean> {
    const losses = [...new Set(ids.flatMap(id => stationTypeLosses(window.graph, id, target)))];
    if (!losses.length) return true;
    const fields = losses.map(field => i18n.t(`cmm.loss.${field}`, { defaultValue: field })).join('、');
    const message = i18n.t('cmm.changeLoss', { fields });
    if (!window.webkit?.messageHandlers?.cslRmp) return window.confirm(message);
    return new Promise(resolve => document.dispatchEvent(new CustomEvent<StationStyleConfirmation>('rmpConfirmStationStyle', { detail: { message, resolve } })));
}
