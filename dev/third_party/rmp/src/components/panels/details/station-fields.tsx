import InfoSection from './info-section';
import NodePositionSection from './node-position-section';
import {
    LuArrowUpLeft,
    LuArrowUp,
    LuArrowUpRight,
    LuArrowLeft,
    LuArrowRight,
    LuArrowDownLeft,
    LuArrowDown,
    LuArrowDownRight,
} from 'react-icons/lu';
import StationTypeSection from './station-type-section';
import stations from '../../svgs/stations/stations';
import {
    Accordion,
    AccordionButton,
    AccordionIcon,
    AccordionItem,
    AccordionPanel,
    Box,
    Button,
    Heading,
    HStack,
    Input,
    Select,
    SimpleGrid,
    Text,
    VStack,
} from '@chakra-ui/react';
import { RmgFields, RmgFieldsField } from '@railmapgen/rmg-components';
import React from 'react';
import { useTranslation } from 'react-i18next';
import { StationAttributes, StationType } from '../../../constants/stations';
import { useRootDispatch, useRootSelector } from '../../../redux';
import { saveGraph } from '../../../redux/param/param-slice';
import { refreshNodesThunk } from '../../../redux/runtime/runtime-slice';
import { positionStationName } from '../../../util/station-position';

export type StationField = RmgFieldsField & { cmmSection?: 'name' | 'position' | 'appearance' | 'transfer' };
export const StationEditorContext = React.createContext<{ id: string } | null>(null);

export function NamePosition({ ids }: { ids: string[] }) {
    const { t } = useTranslation();
    const dispatch = useRootDispatch();
    const commit = (dx?: number, dy?: number) => {
        // Calculate every result before changing the graph so a failed layout cannot partially apply a batch.
        const updates = ids.map(id => {
            const node = structuredClone(window.graph.getNodeAttributes(id));
            if (dx === undefined) {
                const attrs = node[node.type as StationType]!;
                delete attrs.preciseNameOffsets;
                const defaults = stations[node.type as StationType].defaultAttrs;
                for (const key of ['nameOffsetX', 'nameOffsetY'] as const)
                    if (key in defaults)
                        (attrs as unknown as Record<string, unknown>)[key] = (
                            defaults as unknown as Record<string, unknown>
                        )[key];
            } else positionStationName(id, node, dx, dy!);
            return { id, node };
        });
        updates.forEach(({ id, node }) => window.graph.replaceNodeAttributes(id, node));
        dispatch(saveGraph(window.graph.export()));
        dispatch(refreshNodesThunk());
    };
    return (
        <VStack align="stretch" spacing={2}>
            <Text fontSize="sm">{t('cmm.namePosition')}</Text>
            <SimpleGrid columns={3} gap={1} width="120px" aria-label={t('cmm.namePosition')}>
                {[
                    LuArrowUpLeft,
                    LuArrowUp,
                    LuArrowUpRight,
                    LuArrowLeft,
                    null,
                    LuArrowRight,
                    LuArrowDownLeft,
                    LuArrowDown,
                    LuArrowDownRight,
                ].map((Arrow, index) =>
                    index === 4 ? (
                        <Box key={index} textAlign="center" aria-hidden="true">
                            •
                        </Box>
                    ) : (
                        <Button
                            key={index}
                            size="sm"
                            aria-label={t(`cmm.direction${index}`)}
                            onClick={() => commit((index % 3) - 1, Math.floor(index / 3) - 1)}
                        >
                            {Arrow && <Arrow aria-hidden="true" />}
                        </Button>
                    )
                )}
            </SimpleGrid>
            <Button size="sm" variant="outline" onClick={() => commit()}>
                {t('cmm.resetPosition')}
            </Button>
        </VStack>
    );
}

export default function StationFields(
    props: Omit<Parameters<typeof RmgFields>[0], 'fields'> & { fields: StationField[] }
) {
    const context = React.useContext(StationEditorContext);
    const { t } = useTranslation();
    const dispatch = useRootDispatch();
    useRootSelector(state => state.runtime.refresh.nodes);
    if (import.meta.env.MODE !== 'embedded' || !context) return <RmgFields {...props} />;
    const { id } = context;
    const node = window.graph.getNodeAttributes(id);
    const attrs = node[node.type as StationType] as StationAttributes;
    const metadata = node.cmmName;
    const commit = () => {
        dispatch(saveGraph(window.graph.export()));
        dispatch(refreshNodesThunk());
    };
    const group = (section: StationField['cmmSection']) => props.fields.filter(field => field.cmmSection === section);
    const precise = attrs.preciseNameOffsets;
    const setOffset = (axis: 'x' | 'y', value: string) => {
        if (value.trim() === '' || !Number.isFinite(Number(value))) return;
        const label = document.querySelector<SVGGraphicsElement>(`[data-cmm-name="${CSS.escape(id)}"]`);
        const current = precise ?? {
            x: Number(label?.dataset.cmmNameX ?? 0),
            y: Number(label?.dataset.cmmNameY ?? 0),
            anchor: (label?.dataset.cmmNameAnchor ?? 'middle') as 'start' | 'middle' | 'end',
        };
        attrs.preciseNameOffsets = { ...current, [axis]: Number(value) };
        commit();
    };
    return (
        <VStack align="stretch" spacing={3}>
            <Heading as="h5" size="sm">
                {t('cmm.stationNames')}
            </Heading>
            <RmgFields fields={group('name').slice(0, 1)} />
            {group('name').length > 1 && (
                <Accordion allowToggle>
                    <AccordionItem>
                        <AccordionButton>
                            {t('cmm.otherNames')}
                            <AccordionIcon />
                        </AccordionButton>
                        <AccordionPanel px={0}>
                            <RmgFields fields={group('name').slice(1)} />
                        </AccordionPanel>
                    </AccordionItem>
                </Accordion>
            )}
            <Select
                aria-label={t('cmm.nameVisibility')}
                value={metadata?.visibility ?? 'inherit'}
                onChange={e => {
                    node.cmmName = {
                        source: metadata?.source ?? 'unknown',
                        ...metadata,
                        visibility: e.target.value as 'inherit' | 'show' | 'hide',
                    };
                    commit();
                }}
            >
                <option value="inherit">{t('cmm.inherit')}</option>
                <option value="show">{t('cmm.show')}</option>
                <option value="hide">{t('cmm.hide')}</option>
            </Select>
            <Button
                size="sm"
                variant="outline"
                isDisabled={!metadata?.importedNames}
                title={!metadata?.importedNames ? t('cmm.noImportedName') : undefined}
                onClick={() => {
                    if (!metadata?.importedNames) return;
                    attrs.names = structuredClone(metadata.importedNames);
                    while (attrs.names.length < stations[node.type as StationType].defaultAttrs.names.length)
                        attrs.names.push('');
                    node.cmmName = { ...metadata, source: metadata.importedSource ?? 'unknown', edited: false };
                    commit();
                }}
            >
                {t('cmm.restoreName')}
            </Button>
            <StationTypeSection />
            <NamePosition ids={[id]} />
            {(group('appearance').length > 0 || group('transfer').length > 0) && (
                <Accordion allowToggle>
                    <AccordionItem>
                        <AccordionButton>
                            {t('cmm.appearanceAndTransfer')}
                            <AccordionIcon />
                        </AccordionButton>
                        <AccordionPanel px={0}>
                            {group('appearance').length > 0 && <RmgFields fields={group('appearance')} />}
                            {group('transfer').length > 0 && (
                                <>
                                    <Heading as="h5" size="sm">
                                        {t('cmm.transfer')}
                                    </Heading>
                                    <RmgFields fields={group('transfer')} />
                                </>
                            )}
                        </AccordionPanel>
                    </AccordionItem>
                </Accordion>
            )}
            <Accordion allowToggle>
                <AccordionItem>
                    <AccordionButton>
                        {t('cmm.otherProperties')}
                        <AccordionIcon />
                    </AccordionButton>
                    <AccordionPanel px={0}>
                        <RmgFields
                            fields={props.fields.filter(field => !field.cmmSection || field.cmmSection === 'position')}
                        />
                        <HStack>
                            {(['x', 'y'] as const).map(axis => (
                                <Input
                                    key={`${id}-${axis}-${precise?.[axis]}`}
                                    type="number"
                                    aria-label={t('cmm.offset', { axis })}
                                    placeholder={t('cmm.offset', { axis })}
                                    defaultValue={precise?.[axis] ?? ''}
                                    onBlur={e => setOffset(axis, e.target.value)}
                                    onKeyDown={e => {
                                        if (e.key === 'Enter') e.currentTarget.blur();
                                    }}
                                />
                            ))}
                        </HStack>
                        <InfoSection />
                        <NodePositionSection />
                    </AccordionPanel>
                </AccordionItem>
            </Accordion>
        </VStack>
    );
}
