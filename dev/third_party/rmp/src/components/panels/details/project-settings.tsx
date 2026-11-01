import { Button, Checkbox, HStack, Select, Text, VStack } from '@chakra-ui/react';
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Id } from '../../../constants/constants';
import { STATION_TYPE_VALUES, StationType } from '../../../constants/stations';
import { useRootDispatch, useRootSelector } from '../../../redux';
import { saveGraph } from '../../../redux/param/param-slice';
import { refreshNodesThunk, setSelected, showDetailsPanel } from '../../../redux/runtime/runtime-slice';
import { NamePosition } from './station-fields';

export default function ProjectSettings() {
    const { t } = useTranslation();
    const dispatch = useRootDispatch();
    const graph = useRootSelector(state => state.param.present);
    const [lineId, setLineId] = React.useState('');
    const settings = graph.attributes?.cmmNameDisplay ?? { showNames: true, showDefaultNames: true };
    const lines = new Map<number, string>();
    graph.edges.forEach(({ attributes: attrs }) => {
        if (attrs?.cmmSourceLineId !== undefined)
            lines.set(attrs.cmmSourceLineId, attrs.cmmSourceLineName || String(attrs.cmmSourceLineId));
    });
    const currentLine = lines.has(Number(lineId)) && lineId !== '' ? Number(lineId) : lines.keys().next().value;
    const commit = () => {
        dispatch(saveGraph(window.graph.export()));
        dispatch(refreshNodesThunk());
    };
    const choose = (stations: boolean) => {
        const edges = window.graph.filterEdges((_, attrs) => attrs.cmmSourceLineId === currentLine);
        const ids = stations
            ? [...new Set(edges.flatMap(id => window.graph.extremities(id)))].filter(id =>
                  STATION_TYPE_VALUES.has(window.graph.getNodeAttribute(id, 'type') as StationType)
              )
            : edges;
        dispatch(setSelected(new Set(ids as Id[])));
        dispatch(showDetailsPanel());
    };
    return (
        <VStack align="stretch" spacing={3}>
            {(['showNames', 'showDefaultNames'] as const).map(key => (
                <Checkbox
                    key={key}
                    isChecked={settings[key]}
                    isDisabled={key === 'showDefaultNames' && !settings.showNames}
                    onChange={e => {
                        window.graph.setAttribute('cmmNameDisplay', {
                            ...settings,
                            [key]: e.target.checked,
                        });
                        commit();
                    }}
                >
                    {t(`cmm.${key}`)}
                </Checkbox>
            ))}
            <Text fontSize="sm">{t('cmm.lines')}</Text>
            <Select
                aria-label={t('cmm.lines')}
                isDisabled={!lines.size}
                value={currentLine ?? ''}
                onChange={e => setLineId(e.target.value)}
            >
                {!lines.size && <option value="">{t('cmm.noSourceLines')}</option>}
                {[...lines].map(([id, name]) => (
                    <option key={id} value={id}>
                        {name}
                    </option>
                ))}
            </Select>
            <HStack>
                <Button size="sm" isDisabled={!lines.size} onClick={() => choose(false)}>
                    {t('cmm.selectLine')}
                </Button>
                <Button size="sm" isDisabled={!lines.size} onClick={() => choose(true)}>
                    {t('cmm.selectStations')}
                </Button>
            </HStack>
        </VStack>
    );
}

export function SelectedStationSettings() {
    const { t } = useTranslation();
    const dispatch = useRootDispatch();
    const { selected } = useRootSelector(state => state.runtime);
    useRootSelector(state => state.param.present);
    const commit = () => {
        dispatch(saveGraph(window.graph.export()));
        dispatch(refreshNodesThunk());
    };
    const ids = [...selected].filter(
        id =>
            window.graph.hasNode(id) &&
            STATION_TYPE_VALUES.has(window.graph.getNodeAttribute(id, 'type') as StationType)
    );
    const visibility = new Set(ids.map(id => window.graph.getNodeAttribute(id, 'cmmName')?.visibility ?? 'inherit'));
    return (
        <>
            {selected.size > 1 && ids.length > 0 && (
                <VStack align="stretch" spacing={2}>
                    <Text>{t('cmm.selectedStations', { count: ids.length })}</Text>
                    <Select
                        aria-label={t('cmm.nameVisibility')}
                        value={visibility.size === 1 ? [...visibility][0] : 'mixed'}
                        onChange={e => {
                            ids.forEach(id => {
                                const metadata = window.graph.getNodeAttribute(id, 'cmmName');
                                window.graph.setNodeAttribute(id, 'cmmName', {
                                    source: 'unknown',
                                    ...metadata,
                                    visibility: e.target.value as 'inherit' | 'show' | 'hide',
                                });
                            });
                            commit();
                        }}
                    >
                        <option value="mixed" disabled>
                            {t('cmm.mixed')}
                        </option>
                        <option value="inherit">{t('cmm.inherit')}</option>
                        <option value="show">{t('cmm.show')}</option>
                        <option value="hide">{t('cmm.hide')}</option>
                    </Select>
                    <NamePosition ids={ids} />
                </VStack>
            )}
        </>
    );
}
