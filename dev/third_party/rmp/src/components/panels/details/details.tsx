import ProjectSettings, { SelectedStationSettings } from './project-settings';
import { MiscNodeType } from '../../../constants/nodes';
import { STATION_TYPE_VALUES, StationType } from '../../../constants/stations';
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
    VStack,
    Tabs,
    TabList,
    Tab,
    TabPanels,
    TabPanel,
    Menu,
    MenuButton,
    MenuList,
    MenuItem,
    Portal,
    Tooltip,
} from '@chakra-ui/react';
import { RmgSidePanel, RmgSidePanelBody, RmgSidePanelFooter, RmgSidePanelHeader } from '@railmapgen/rmg-components';
import { nanoid } from 'nanoid';
import React from 'react';
import { useTranslation } from 'react-i18next';
import {
    LuClipboardCopy,
    LuClipboardPaste,
    LuCopy,
    LuCopyPlus,
    LuMerge,
    LuPanelRightOpen,
    LuTrash2,
} from 'react-icons/lu';
import { Id, LineId, NodeId, StnId } from '../../../constants/constants';
import { useRootDispatch, useRootSelector } from '../../../redux';
import { saveGraph } from '../../../redux/param/param-slice';
import {
    clearSelected,
    hideDetailsPanel,
    refreshEdgesThunk,
    refreshNodesThunk,
} from '../../../redux/runtime/runtime-slice';
import { checkAndChangeStationIntType } from '../../../util/change-types';
import {
    EdgeSpecificAttrsClipboardData,
    exportEdgeSpecificAttrs,
    exportNodeSpecificAttrs,
    exportSelectedNodesAndEdges,
    getSelectedElementsType,
    importEdgeSpecificAttrs,
    importNodeSpecificAttrs,
    NodeSpecificAttrsClipboardData,
    parseClipboardData,
} from '../../../util/clipboard';
import { isPortraitClient } from '../../../util/helpers';
import { sendErrorNotification } from '../../../util/notifications';
import { reconcileSelectedEdges } from '../../../util/reconcile-ui';
import InfoSection from './info-section';
import LineExtremitiesSection from './line-extremities-section';
import NodePositionSection from './node-position-section';
import { LineSpecificAttributes, NodeSpecificAttributes } from './specific-attrs';

const embedded = import.meta.env.MODE === 'embedded';

const DetailsPanel = () => {
    const { t } = useTranslation();
    const portalContainer = React.useRef(document.getElementById('root'));
    const dispatch = useRootDispatch();
    const graph = React.useRef(window.graph);
    const hardRefresh = React.useCallback(() => {
        dispatch(saveGraph(graph.current.export()));
        dispatch(refreshNodesThunk());
        dispatch(refreshEdgesThunk());
    }, [dispatch, refreshNodesThunk, refreshEdgesThunk, saveGraph]);
    const { selected, isDetailsOpen } = useRootSelector(state => state.runtime);
    const {
        preference: { autoChangeStationType },
    } = useRootSelector(state => state.app);
    const [selectedFirst] = selected;

    const stationSelected =
        selected.size === 1 &&
        graph.current.hasNode(selectedFirst) &&
        STATION_TYPE_VALUES.has(graph.current.getNodeAttribute(selectedFirst, 'type') as StationType);
    const selectionKey = [...selected].sort().join(',');
    const [tab, setTab] = React.useState(selected.size ? 0 : 1);
    React.useEffect(() => setTab(selected.size ? 0 : 1), [selectionKey]);
    const nodeType =
        selected.size === 1 && graph.current.hasNode(selectedFirst)
            ? graph.current.getNodeAttribute(selectedFirst, 'type')
            : undefined;
    const title = !selected.size
        ? t('cmm.mapSettings')
        : selected.size > 1
          ? t('cmm.selectedObjects', { count: selected.size })
          : stationSelected
            ? t('cmm.station')
            : graph.current.hasEdge(selectedFirst)
              ? t('cmm.lineSegment')
              : nodeType === MiscNodeType.Text
                ? t('cmm.textObject')
                : nodeType === MiscNodeType.Facilities
                  ? t('cmm.facilityObject')
                  : t('cmm.objectProperties');
    const canCopyAttrs = selected.size === 1;
    const hasMultipleEdges = React.useMemo(
        () => [...selected].filter(id => graph.current.hasEdge(id)).length >= 2,
        [selected]
    );

    const handleClose = () => {
        if (!embedded && !isPortraitClient()) {
            dispatch(clearSelected());
        } else {
            dispatch(hideDetailsPanel());
        }
    };
    const handleDuplicate = (selectedFirst: string) => {
        const allAttr = structuredClone(graph.current.getNodeAttributes(selectedFirst));
        allAttr.x += 50;
        allAttr.y += 50;
        const id = selectedFirst.startsWith('stn') ? `stn_${nanoid(10)}` : `misc_node_${nanoid(10)}`;
        graph.current.addNode(id, allAttr);
        dispatch(saveGraph(graph.current.export()));
        dispatch(refreshNodesThunk());
    };
    const handleCopy = (selected: Set<Id>) => {
        const s = exportSelectedNodesAndEdges(graph.current, selected);
        navigator.clipboard.writeText(s);
    };
    const handleRemove = (selected: Set<Id>) => {
        dispatch(clearSelected());
        selected.forEach(s => {
            if (graph.current.hasNode(s)) graph.current.dropNode(s);
            else if (graph.current.hasEdge(s)) {
                const [u, v] = graph.current.extremities(s);
                graph.current.dropEdge(s);

                // Automatically change the station type to basic if the station is connected by lines in a single color.
                if (autoChangeStationType && u.startsWith('stn'))
                    checkAndChangeStationIntType(graph.current, u as StnId);
                if (autoChangeStationType && v.startsWith('stn'))
                    checkAndChangeStationIntType(graph.current, v as StnId);
            }
        });
        hardRefresh();
    };

    const handleCopyAttrs = () => {
        if (selected.size !== 1) return;
        const id = selectedFirst;

        if (graph.current.hasNode(id)) {
            const s = exportNodeSpecificAttrs(graph.current, id as NodeId);
            navigator.clipboard.writeText(s);
        } else if (graph.current.hasEdge(id)) {
            const s = exportEdgeSpecificAttrs(graph.current, id as LineId);
            navigator.clipboard.writeText(s);
        }
    };

    const handlePasteAttrs = async () => {
        let s = '';
        try {
            s = await navigator.clipboard.readText();
        } catch (error) {
            console.warn('Failed to read clipboard:', error);
            sendErrorNotification(t('error'), t('clipboard.errors.readText'));
            return;
        }

        const parsed = parseClipboardData(s);
        const selectionInfo = getSelectedElementsType(graph.current, selected);
        if (!parsed || parsed.type === 'elements' || !selectionInfo.allSameType || !selectionInfo.category) {
            sendErrorNotification(t('error'), t('clipboard.errors.cannotPasteSpecificAttrs'));
            return;
        }

        if (selectionInfo.category === 'node') {
            if (selectionInfo.nodeType !== parsed.type) {
                sendErrorNotification(t('error'), t('clipboard.errors.cannotPasteSpecificAttrs'));
                return;
            }

            if (!importNodeSpecificAttrs(graph.current, selected, parsed.data as NodeSpecificAttrsClipboardData)) {
                sendErrorNotification(t('error'), t('clipboard.errors.cannotPasteSpecificAttrs'));
                return;
            }

            hardRefresh();
            return;
        }

        if (selectionInfo.category !== 'edge' || selectionInfo.edgeStyleType !== parsed.type) {
            sendErrorNotification(t('error'), t('clipboard.errors.cannotPasteSpecificAttrs'));
            return;
        }

        if (!importEdgeSpecificAttrs(graph.current, selected, parsed.data as EdgeSpecificAttrsClipboardData)) {
            sendErrorNotification(t('error'), t('clipboard.errors.cannotPasteSpecificAttrs'));
            return;
        }

        hardRefresh();
    };

    const handleReconcile = () => {
        if (reconcileSelectedEdges(graph.current, selected)) {
            hardRefresh();
        }
    };

    const properties = (
        <>
            {selected.size > 1 && (
                <>
                    <SelectedStationSettings />
                    <InfoSection />
                </>
            )}
            {selected.size === 1 && (
                <>
                    {graph.current.hasNode(selectedFirst) && <NodeSpecificAttributes />}
                    {graph.current.hasEdge(selectedFirst) && (
                        <>
                            <InfoSection section="appearance" />
                            <Heading as="h5" size="sm">
                                {t('cmm.lineAppearance')}
                            </Heading>
                            <LineSpecificAttributes />
                        </>
                    )}
                    {!stationSelected && (
                        <Accordion allowToggle className="cmm-other-properties">
                            <AccordionItem>
                                <AccordionButton>
                                    {t('cmm.otherProperties')}
                                    <AccordionIcon />
                                </AccordionButton>
                                <AccordionPanel px={0}>
                                    {graph.current.hasNode(selectedFirst) && (
                                        <>
                                            <InfoSection />
                                            <NodePositionSection />
                                        </>
                                    )}
                                    {graph.current.hasEdge(selectedFirst) && <><InfoSection section="other" /><LineExtremitiesSection /></>}
                                </AccordionPanel>
                            </AccordionItem>
                        </Accordion>
                    )}
                </>
            )}
        </>
    );
    return (
        <RmgSidePanel isOpen={isDetailsOpen === 'show'} width={embedded ? 320 : 300} header={title} alwaysOverlay>
            <RmgSidePanelHeader onClose={handleClose}>
                {embedded && <LuPanelRightOpen className="cmm-details-icon" aria-hidden="true" />}
                {embedded ? title : t('panel.details.header')}
            </RmgSidePanelHeader>
            <RmgSidePanelBody>
                {embedded ? (
                    <Tabs index={tab} onChange={setTab} isLazy lazyBehavior="keepMounted" className="cmm-details-tabs">
                        <TabList aria-label={t('panel.details.header')}>
                            <Tab isDisabled={!selected.size}>{t('cmm.properties')}</Tab>
                            <Tab>{t('cmm.mapSettings')}</Tab>
                        </TabList>
                        <TabPanels>
                            <TabPanel px={0}>{properties}</TabPanel>
                            <TabPanel px={0}>
                                <ProjectSettings />
                            </TabPanel>
                        </TabPanels>
                    </Tabs>
                ) : (
                    <>
                        <InfoSection />
                        {selected.size === 1 && (
                            <Box p={1}>
                                <Heading as="h5" size="sm">
                                    {t('panel.details.specificAttrsTitle')}
                                </Heading>
                                {window.graph.hasNode(selectedFirst) && <NodeSpecificAttributes />}
                                {window.graph.hasEdge(selectedFirst) && <LineSpecificAttributes />}
                            </Box>
                        )}
                        {selected.size === 1 && (
                            <Accordion allowToggle>
                                <AccordionItem>
                                    <AccordionButton>
                                        {t('cmm.more')}
                                        <AccordionIcon />
                                    </AccordionButton>
                                    <AccordionPanel px={0}>
                                        {graph.current.hasNode(selectedFirst) && <NodePositionSection />}
                                        {graph.current.hasEdge(selectedFirst) && <LineExtremitiesSection />}
                                    </AccordionPanel>
                                </AccordionItem>
                            </Accordion>
                        )}
                    </>
                )}
            </RmgSidePanelBody>
            <RmgSidePanelFooter>
                {embedded ? (
                    <HStack spacing={1} width="100%" className="cmm-object-actions">
                        <Tooltip label={t('cmm.duplicateObject')}>
                            <Button
                                isDisabled={selected.size !== 1 || !graph.current.hasNode(selectedFirst)}
                                onClick={() => handleDuplicate(selectedFirst)}
                            >
                                {t('cmm.duplicateObject')}
                            </Button>
                        </Tooltip>
                        <Tooltip label={`${t('copy')} (Ctrl+C)`}>
                            <Button isDisabled={!selected.size} onClick={() => handleCopy(selected)}>
                                {t('copy')}
                            </Button>
                        </Tooltip>
                        <Tooltip label={`${t('remove')} (Delete)`}>
                            <Button
                                className="cmm-remove"
                                isDisabled={!selected.size}
                                onClick={() => handleRemove(selected)}
                            >
                                {t('remove')}
                            </Button>
                        </Tooltip>
                        <Menu placement="top-end">
                            <MenuButton as={Button} flex={1} isDisabled={!selected.size}>
                                {t('cmm.moreActions')}
                            </MenuButton>
                            <Portal containerRef={portalContainer}>
                                <MenuList className="cmm-editor-menu">
                                    <MenuItem
                                        icon={<LuClipboardCopy />}
                                        onClick={handleCopyAttrs}
                                        isDisabled={!canCopyAttrs}
                                    >
                                        {t('cmm.copyStyle')}
                                    </MenuItem>
                                    <MenuItem icon={<LuClipboardPaste />} onClick={handlePasteAttrs}>
                                        {t('cmm.pasteStyle')}
                                    </MenuItem>
                                    {hasMultipleEdges && (
                                        <MenuItem icon={<LuMerge />} onClick={handleReconcile}>
                                            {t('panel.details.info.reconcile')}
                                        </MenuItem>
                                    )}
                                </MenuList>
                            </Portal>
                        </Menu>
                    </HStack>
                ) : (
                    <VStack spacing={2} align="stretch" width="100%">
                        <HStack>
                            {selected.size === 1 && graph.current.hasNode(selectedFirst) && (
                                <Button
                                    size="sm"
                                    variant="outline"
                                    leftIcon={embedded ? <LuCopyPlus aria-hidden="true" /> : undefined}
                                    onClick={() => handleDuplicate(selectedFirst)}
                                    flex={1}
                                >
                                    {t('panel.details.footer.duplicate')}
                                </Button>
                            )}
                            <Button
                                size="sm"
                                variant="outline"
                                leftIcon={embedded ? <LuCopy aria-hidden="true" /> : undefined}
                                onClick={() => handleCopy(selected)}
                                flex={1}
                            >
                                {t('copy')}
                            </Button>
                            <Button
                                size="sm"
                                variant="outline"
                                className={embedded ? 'cmm-remove' : undefined}
                                leftIcon={embedded ? <LuTrash2 aria-hidden="true" /> : undefined}
                                onClick={() => handleRemove(selected)}
                                flex={1}
                            >
                                {t('remove')}
                            </Button>
                        </HStack>

                        <Button
                            size="sm"
                            variant="outline"
                            leftIcon={embedded ? <LuClipboardCopy aria-hidden="true" /> : undefined}
                            onClick={handleCopyAttrs}
                            isDisabled={!canCopyAttrs}
                        >
                            {t(embedded ? 'cmm.copyStyle' : 'copyAttrs')}
                        </Button>
                        <Button
                            size="sm"
                            variant="outline"
                            leftIcon={embedded ? <LuClipboardPaste aria-hidden="true" /> : undefined}
                            onClick={handlePasteAttrs}
                        >
                            {t(embedded ? 'cmm.pasteStyle' : 'pasteAttrs')}
                        </Button>
                        {hasMultipleEdges && (
                            <Button
                                size="sm"
                                variant="outline"
                                leftIcon={embedded ? <LuMerge aria-hidden="true" /> : undefined}
                                onClick={handleReconcile}
                            >
                                {t('panel.details.info.reconcile')}
                            </Button>
                        )}
                    </VStack>
                )}
            </RmgSidePanelFooter>
        </RmgSidePanel>
    );
};

export default DetailsPanel;
