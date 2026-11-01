import { StationType } from '../constants/stations';
import i18n from '../i18n/config';
import { stationTypeLosses } from './change-types';

export function confirmStationTypeChange(ids: string[], target: StationType): boolean {
    const losses = [...new Set(ids.flatMap(id => stationTypeLosses(window.graph, id, target)))];
    if (!losses.length) return true;
    const fields = losses.map(field => i18n.t(`cmm.loss.${field}`, { defaultValue: field })).join('、');
    return window.confirm(i18n.t('cmm.changeLoss', { fields }));
}
