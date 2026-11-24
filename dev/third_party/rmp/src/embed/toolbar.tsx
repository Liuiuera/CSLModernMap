import { Button, Flex, IconButton, Menu, MenuButton, MenuItem, MenuItemOption, MenuList, MenuOptionGroup, Portal, Select, Text } from '@chakra-ui/react';
import React from 'react';
import { useTranslation } from 'react-i18next';
import { LuChevronDown, LuHand, LuMousePointer2, LuPanelRight, LuRedo2, LuRotateCcw, LuRotateCw, LuScan, LuUndo2, LuX } from 'react-icons/lu';
import { toolCatalog } from './host-tools';
import type { InsertRequest } from './insert-elements';

type ViewOptions = { gridLines?: boolean; snapLines?: boolean; detailsVisible?: boolean; showWaterControls?: boolean; showNames?: boolean; showDefaultNames?: boolean };
type ToolbarCommand = { type: string; mode?: string; insertion?: InsertRequest; options?: ViewOptions; delta?: number };
type EditorState = { mode: string; canUndo: boolean; canRedo: boolean; selected: string[]; insertion: InsertRequest | null; placing: boolean } & Required<ViewOptions>;

export default function EditorToolbar({ enabled, state, execute, onPickFacility }: {
    enabled: boolean;
    state: EditorState;
    execute: (command: ToolbarCommand) => void;
    onPickFacility: () => void;
}) {
    const { t } = useTranslation();
    const portalContainer = React.useRef(document.getElementById('root'));
    const catalog = toolCatalog();
    const [path, setPath] = React.useState(catalog.paths[0].id);
    const [style, setStyle] = React.useState(catalog.styles[0].id);
    const drawingLine = state.mode.startsWith('line-');
    const [activePath, activeStyle] = drawingLine ? state.mode.slice(5).split('/') : [path, style];
    const styles = catalog.styles.filter(item => item.paths.includes(activePath as never));
    const selectedStyle = styles.some(item => item.id === activeStyle) ? activeStyle : styles[0]?.id;
    const run = (command: ToolbarCommand, focus = true) => {
        execute(command);
        if (focus) execute({ type: 'focusEditor' });
    };
    const icon = (key: string, child: React.ReactElement, command: ToolbarCommand, disabled = false, pressed?: boolean) => (
        <IconButton key={key} size="sm" aria-label={t(`editor.${key}`)} title={t(`editor.${key}`)} icon={child}
            isDisabled={!enabled || disabled} aria-pressed={pressed} onClick={() => run(command)} />
    );
    const mode = (value: string) => run({ type: 'setMode', mode: value });
    const toggle = (key: keyof ViewOptions) => run({ type: 'setViewOptions', options: { [key]: !state[key] } }, false);
    const choice = (label: string, value: string, items: { id: string; label: string }[], onChange: (value: string) => void) => (
        <Select size="sm" aria-label={t(`editor.${label}`)} title={t(`editor.${label}`)} value={value} isDisabled={!enabled}
            onChange={e => onChange(e.target.value)} width="auto" maxWidth="160px">
            <option value="" disabled>{t(`editor.${label}`)}</option>
            {items.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </Select>
    );
    return <Flex direction="column" className="cmm-toolbar" data-testid="editor-toolbar" flexShrink={0}>
        <Flex role="toolbar" aria-label={t('editor.tools')} gap={1} wrap="wrap" align="center">
            {icon('move', <LuHand/>, { type: 'setMode', mode: 'free' }, false, state.mode === 'free' && !state.insertion)}
            {icon('select', <LuMousePointer2/>, { type: 'setMode', mode: 'select' }, false, state.mode === 'select')}
            {choice('stations', state.mode.startsWith('station-') ? state.mode : '', catalog.stations, mode)}
            <Button size="sm" data-testid="draw-line" aria-pressed={drawingLine} isDisabled={!enabled || !selectedStyle}
                onClick={() => mode(`line-${activePath}/${selectedStyle}`)}>{t('editor.drawLine')}</Button>
            {drawingLine && <>
                {choice('paths', activePath, catalog.paths, value => {
                    setPath(value);
                    const next = catalog.styles.find(item => item.id === selectedStyle && item.paths.includes(value as never)) ?? catalog.styles.find(item => item.paths.includes(value as never))!;
                    setStyle(next.id); mode(`line-${value}/${next.id}`);
                })}
                {choice('styles', selectedStyle ?? '', styles, value => { setStyle(value); mode(`line-${activePath}/${value}`); })}
            </>}
            <Menu isLazy>
                <MenuButton as={Button} size="sm" data-testid="insert-elements" rightIcon={<LuChevronDown/>} isDisabled={!enabled}>{t('editor.insert')}</MenuButton>
                <Portal containerRef={portalContainer}><MenuList className="cmm-editor-menu">
                    <MenuItem onClick={() => run({ type: 'beginInsert', insertion: { kind: 'title' } })}>{t('editor.title')}</MenuItem>
                    <MenuItem onClick={() => run({ type: 'beginInsert', insertion: { kind: 'legend' } })}>{t('embed.legend')}</MenuItem>
                    <MenuItem data-testid="insert-facility" isDisabled={!catalog.facilities.length} onClick={onPickFacility}>{t('editor.facilityPicker')}</MenuItem>
                    {catalog.elements.filter(item => item.id !== 'misc-node-facilities').map(item => <MenuItem key={item.id} onClick={() => mode(item.id)}>{item.label}</MenuItem>)}
                </MenuList></Portal>
            </Menu>
        </Flex>
        <Flex role="toolbar" aria-label={t('editor.view')} gap={1} wrap="wrap" align="center">
            {icon('undo', <LuUndo2/>, { type: 'undo' }, !state.canUndo)}
            {icon('redo', <LuRedo2/>, { type: 'redo' }, !state.canRedo)}
            {icon('fit', <LuScan/>, { type: 'fit' })}
            {icon('rotateLeft', <LuRotateCcw/>, { type: 'rotateMap', delta: -90 })}
            {icon('rotateRight', <LuRotateCw/>, { type: 'rotateMap', delta: 90 })}
            {icon('details', <LuPanelRight/>, { type: 'setViewOptions', options: { detailsVisible: !state.detailsVisible } }, false, state.detailsVisible)}
            <Menu closeOnSelect={false} isLazy>
                <MenuButton as={Button} size="sm" rightIcon={<LuChevronDown/>} isDisabled={!enabled}>{t('editor.view')}</MenuButton>
                <Portal containerRef={portalContainer}><MenuList className="cmm-editor-menu">
                    {(['gridLines', 'snapLines', 'detailsVisible', 'showWaterControls', 'showNames', 'showDefaultNames'] as const).map(key => (
                        <MenuOptionGroup key={key} type="checkbox" value={state[key] ? [key] : []} onChange={() => toggle(key)}>
                            <MenuItemOption value={key} isDisabled={key === 'showDefaultNames' && !state.showNames}>
                                {t(key.startsWith('show') && key !== 'showWaterControls' ? `cmm.${key}` : `editor.${key}`)}
                            </MenuItemOption>
                        </MenuOptionGroup>
                    ))}
                </MenuList></Portal>
            </Menu>
            {(state.insertion || (state.mode !== 'free' && state.mode !== 'select')) && <Button size="sm" leftIcon={<LuX/>} isDisabled={!enabled} onClick={() => run({ type: 'cancelInsert' })}>{t('editor.cancel')}</Button>}
            <Text role="status" className="cmm-tool-status" fontSize="xs" marginLeft="auto">
                {state.insertion ? t('editor.placeHint') : drawingLine ? t('editor.lineHint') : state.mode.startsWith('station-') || state.mode.startsWith('misc-node-') ? t('editor.nodeHint') : t('editor.selection', { count: state.selected.length })}
            </Text>
        </Flex>
    </Flex>;
}
