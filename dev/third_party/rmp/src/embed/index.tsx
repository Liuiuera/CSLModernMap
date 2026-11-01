import { validateNameMetadata, validateNameDisplay } from '../util/station-label';
import { Button, Flex, Input, LightMode, useColorMode, Modal, ModalBody, ModalContent, ModalFooter, ModalHeader, ModalOverlay } from '@chakra-ui/react';
import { RmgThemeProvider } from '@railmapgen/rmg-components';
import { MultiDirectedGraph } from 'graphology';
import React from 'react';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { I18nextProvider } from 'react-i18next';
import { Provider } from 'react-redux';
import DetailsPanel from '../components/panels/details/details';
import SvgWrapper from '../components/svg-wrapper';
import { linePaths, lineStyles } from '../components/svgs/lines/lines';
import { FacilitiesType } from '../components/svgs/nodes/facilities';
import miscNodes from '../components/svgs/nodes/misc-nodes';
import stations from '../components/svgs/stations/stations';
import { MiscNodeType } from '../constants/nodes';
import i18n from '../i18n/config';
import '../index.css';
import store, { useRootDispatch, useRootSelector } from '../redux';
import { setGridLines, setRandomStationsNames, setSnapLines } from '../redux/app/app-slice';
import { redoAction, saveGraph, setFullState, setSvgViewport, undoAction } from '../redux/param/param-slice';
import { clearSelected, closePaletteAppClip, hideDetailsPanel, onPaletteAppClipEmit, refreshEdgesThunk, refreshNodesThunk, setMode, setSelected, showDetailsPanel } from '../redux/runtime/runtime-slice';
import { CanvasSizeContext, embeddedWorkspaceSize, visibleCanvasBounds } from '../util/canvas-size';
import { makeRenderReadySVGElement } from '../util/download';
import { TextLanguage } from '../util/fonts';
import { calculateCanvasSize } from '../util/helpers';
import { ToastContainer } from '../util/notifications';
import { RMPSave, stringifyParam, upgrade } from '../util/save';
import './fonts';
import { rotateMap, validateMapRotation } from '../util/map-rotation';
import { materializeDraft } from './generated-draft';
import { toolCatalog, validateMode } from './host-tools';
import { buildInsertion, insertedSelection, InsertRequest, positionInsertion, validateInsertion } from './insert-elements';
import './style.css';

declare global { interface Window { chrome?: { webview: { postMessage(value: unknown): void; addEventListener(type: string, cb: (event: MessageEvent) => void): void } }; cslRmp: { dispatch(message: Command): Promise<void> } } }
type Command = { protocolVersion: number; type: string; requestId?: string; projectId: string; project?: RMPSave; format?: 'svg' | 'png'; scale?: number; transparent?: boolean; svgVersion?: 1.1 | 2; fit?: boolean; delta?: number; enabled?: boolean; theme?: 'light' | 'dark'; highContrast?: boolean; mode?: string; insertion?: InsertRequest; options?: { gridLines?: boolean; snapLines?: boolean; detailsVisible?: boolean; showWaterControls?: boolean; showNames?: boolean; showDefaultNames?: boolean } };
let projectId = '';
let revision = 0;
let loading = false;
let renderRevision = 0;
let hostEnabled = false;
let showWaterControls = false;
let hostTheme: 'light' | 'dark' = 'light';
let pendingInsertion: InsertRequest | null = null;
let placing = false;
const post = (type: string, payload: object = {}) => window.chrome?.webview.postMessage({ channel: 'rmp', protocolVersion: 1, type, projectId, revision, ...payload });
window.graph = new MultiDirectedGraph();
store.dispatch(setRandomStationsNames('empty'));

function Palette() {
    const input = useRootSelector(state => state.runtime.paletteAppClip.input);
    const dispatch = useRootDispatch();
    const [color, setColor] = React.useState('#0078D4');
    React.useEffect(() => { if (input) setColor(input[2]); }, [input]);
    return <Modal isOpen={!!input} onClose={() => dispatch(closePaletteAppClip())}><ModalOverlay/><ModalContent><ModalHeader>{i18n.t('Color')}</ModalHeader><ModalBody><Input type="color" value={color} onChange={e => setColor(e.target.value)} /></ModalBody><ModalFooter><Button onClick={() => dispatch(onPaletteAppClipEmit(['other', 'other', color, '#FFFFFF'] as never))}>{i18n.t('Confirm')}</Button></ModalFooter></ModalContent></Modal>;
}
function HostTheme() {
    const { setColorMode } = useColorMode();
    React.useEffect(() => {
        const update = () => setColorMode(hostTheme);
        update(); document.addEventListener('rmpHostState', update);
        return () => document.removeEventListener('rmpHostState', update);
    }, [setColorMode]);
    return null;
}
function KeepSelectionVisible({ width, height }: { width: number; height: number }) {
    const selected = useRootSelector(state => state.runtime.selected);
    React.useLayoutEffect(() => {
        const frame = requestAnimationFrame(() => {
            const first = [...selected].find(id => document.getElementById(id));
            const node = first && document.getElementById(first);
            const canvas = document.getElementById('canvas');
            if (!node || !canvas) return;
            const box = node.getBoundingClientRect(), view = visibleCanvasBounds();
            const dx = box.left < view.left + 16 ? box.left - view.left - 16 : box.right > view.right - 16 ? box.right - view.right + 16 : 0;
            const dy = box.top < view.top + 16 ? box.top - view.top - 16 : box.bottom > view.bottom - 16 ? box.bottom - view.bottom + 16 : 0;
            if (dx || dy) {
                const { svgViewBoxZoom: zoom, svgViewBoxMin: min } = store.getState().param;
                store.dispatch(setSvgViewport({ zoom, min: { x: min.x + dx * zoom / 100, y: min.y + dy * zoom / 100 } }));
            }
        });
        return () => cancelAnimationFrame(frame);
    }, [selected, width, height]);
    return null;
}
function App() {
    const [key, setKey] = React.useState(0);
    const [waterControls, setWaterControls] = React.useState(showWaterControls);
    const [enabled, setEnabled] = React.useState(hostEnabled);
    const detailsVisible = useRootSelector(state => state.runtime.isDetailsOpen === 'show');
    const workspace = React.useRef<HTMLDivElement>(null);
    const [size, setSize] = React.useState({ width: 1, height: 1 });
    React.useLayoutEffect(() => {
        const observer = new ResizeObserver(([entry]) => setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
        observer.observe(workspace.current!);
        return () => observer.disconnect();
    }, []);
    React.useEffect(() => { const update = () => { setEnabled(hostEnabled); setWaterControls(showWaterControls); }; document.addEventListener('rmpHostState', update); return () => document.removeEventListener('rmpHostState', update); }, []);
    React.useEffect(() => { const update = () => setKey(renderRevision); document.addEventListener('rmpLoaded', update); return () => document.removeEventListener('rmpLoaded', update); }, []);
    const canvasSize = embeddedWorkspaceSize(size.width, size.height, detailsVisible);
    return <RmgThemeProvider><HostTheme/>
        <Flex className="cmm-editor" height="100vh" direction="column">
            <Flex ref={workspace} minHeight={0} flex={1} overflow="hidden" position="relative"
                inert={!enabled} className={waterControls ? undefined : 'cmm-hide-water-controls'}>
                <CanvasSizeContext.Provider value={canvasSize}>
                    <React.Fragment key={key}><Flex width={canvasSize.width} height="100%" position="relative" data-cmm-visible-width={canvasSize.visibleWidth}>
                        <LightMode><SvgWrapper/></LightMode></Flex><DetailsPanel/>
                        <KeepSelectionVisible width={canvasSize.visibleWidth} height={canvasSize.height}/></React.Fragment>
                </CanvasSizeContext.Provider>
            </Flex>
        </Flex>
        <Palette/><ToastContainer/>
    </RmgThemeProvider>;
}
createRoot(document.getElementById('root')!).render(<Provider store={store}><I18nextProvider i18n={i18n}><App/></I18nextProvider></Provider>);
const editorState = () => {
    const { runtime, app, param } = store.getState();
    const detailsVisible = runtime.isDetailsOpen === 'show';
    return { mode: runtime.mode, selected: [...runtime.selected], canUndo: !!param.past.length, canRedo: !!param.future.length,
        showNames: param.present.attributes?.cmmNameDisplay?.showNames ?? true,
        showDefaultNames: param.present.attributes?.cmmNameDisplay?.showDefaultNames ?? true,
        gridLines: app.preference.gridLines, snapLines: app.preference.snapLines, detailsVisible, showWaterControls,
        insertion: pendingInsertion, placing };
};
let lastState = '';
const publishState = (requestId?: string) => {
    document.documentElement.classList.toggle('cmm-inserting', pendingInsertion !== null);
    const state = editorState();
    const serialized = JSON.stringify(state);
    if (requestId || serialized !== lastState) { lastState = serialized; post('editorState', { requestId, state }); }
};
let lastGraph = store.getState().param.present;
store.subscribe(() => {
    const graph = store.getState().param.present;
    if (graph !== lastGraph) { lastGraph = graph; if (!loading) { revision++; post('changed'); } }
    if (!loading) publishState();
});
const fit = () => {
    if (!window.graph.order) return;
    const { xMin, yMin, xMax, yMax } = calculateCanvasSize(window.graph);
    const canvas = visibleCanvasBounds();
    const zoom = Math.max((xMax-xMin)/Math.max(canvas.width-40, 1), (yMax-yMin)/Math.max(canvas.height-40, 1), .05)*100;
    store.dispatch(setSvgViewport({ zoom, min: { x: (xMin+xMax)/2-canvas.width*zoom/200, y: (yMin+yMax)/2-canvas.height*zoom/200 } }));
};
const validate = (save: RMPSave) => {
    if (!Number.isInteger(save.version) || save.version < 1 || save.version > 77) throw new Error('Unsupported RMP save version');
    if (!Array.isArray(save.graph?.nodes) || !Array.isArray(save.graph?.edges)) throw new Error('Invalid RMP graph');
    if (save.images?.length) throw new Error('Images are not supported in the embedded editor');
    for (const node of save.graph.nodes) if ([MiscNodeType.Image, MiscNodeType.Master].includes(node.attributes!.type as MiscNodeType)) throw new Error('Images and Master nodes are not supported in the embedded editor');
};
const validateCurrent = (save: RMPSave) => {
    validateMapRotation(save.graph.attributes?.cmmRotation);
    validateNameDisplay(save.graph.attributes?.cmmNameDisplay);
    if (!Number.isFinite(save.svgViewBoxZoom) || save.svgViewBoxZoom <= 0 ||
        !Number.isFinite(save.svgViewBoxMin?.x) || !Number.isFinite(save.svgViewBoxMin?.y)) throw new Error('Invalid viewport');
    for (const node of save.graph.nodes) {
        const attrs = node.attributes!;
        validateNameMetadata(attrs);
        if (!Number.isFinite(attrs.x) || !Number.isFinite(attrs.y) ||
            !(attrs.type in stations || attrs.type in miscNodes)) throw new Error(`Invalid node: ${node.key}`);
        if (!Number.isInteger(attrs.zIndex) || attrs.zIndex < -10 || attrs.zIndex > 10) throw new Error(`Invalid node layer: ${node.key}`);
        if (attrs.type === MiscNodeType.Facilities && !Object.values(FacilitiesType).includes(attrs.facilities!.type)) throw new Error(`Invalid facility: ${node.key}`);
    }
    for (const edge of save.graph.edges) {
        const attrs = edge.attributes!;
        if ((attrs.cmmSourceLineId !== undefined && !Number.isSafeInteger(attrs.cmmSourceLineId)) ||
            (attrs.cmmSourceLineName !== undefined && typeof attrs.cmmSourceLineName !== 'string')) throw new Error(`Invalid source line: ${edge.key}`);
        if (!(attrs.type in linePaths) || !(attrs.style in lineStyles) ||
            !Number.isInteger(attrs.zIndex) || attrs.zIndex < -10 || attrs.zIndex > 10) throw new Error(`Invalid edge: ${edge.key}`);
    }
};
async function dispatch(message: Command) {
    if (message.type !== 'loadProject' && message.projectId !== projectId) return;
    try {
        if (message.protocolVersion !== 1) throw new Error('Unsupported RMP bridge protocol');
        if (message.type === 'hostState') {
            hostEnabled = message.enabled === true;
            document.getElementById('root')!.inert = !hostEnabled;
            document.dispatchEvent(new Event('rmpHostState'));
        } else if (message.type === 'setTheme') {
            if (!['light', 'dark'].includes(message.theme!) || typeof message.highContrast !== 'boolean') throw new Error('Invalid editor theme');
            hostTheme = message.theme!;
            document.documentElement.dataset.cmmTheme = hostTheme;
            document.documentElement.dataset.cmmHighContrast = String(message.highContrast);
            document.dispatchEvent(new Event('rmpHostState'));
            post('themeApplied', { requestId: message.requestId });
        } else if (message.type === 'getTools') {
            post('tools', { requestId: message.requestId, tools: toolCatalog() });
        } else if (message.type === 'setMode') {
            const mode = validateMode(message.mode);
            pendingInsertion = null;
            store.dispatch(setMode(mode));
            publishState(message.requestId);
        } else if (message.type === 'beginInsert') {
            const request = validateInsertion(message.insertion);
            if (request.kind === 'legend') buildInsertion(request);
            pendingInsertion = request;
            store.dispatch(setMode('free'));
            publishState(message.requestId);
        } else if (message.type === 'cancelInsert') {
            pendingInsertion = null;
            store.dispatch(setMode('free'));
            publishState(message.requestId);
        } else if (message.type === 'setViewOptions') {
            const options = message.options;
            if (!options || Object.entries(options).some(([key, value]) =>
                !['gridLines', 'snapLines', 'detailsVisible', 'showWaterControls', 'showNames', 'showDefaultNames'].includes(key) || typeof value !== 'boolean'))
                throw new Error('Invalid view options');
            if (options.showNames !== undefined || options.showDefaultNames !== undefined) {
                window.graph.setAttribute('cmmNameDisplay', { showNames: store.getState().param.present.attributes?.cmmNameDisplay?.showNames ?? true,
                    showDefaultNames: store.getState().param.present.attributes?.cmmNameDisplay?.showDefaultNames ?? true,
                    ...Object.fromEntries(Object.entries(options).filter(([key]) => ['showNames', 'showDefaultNames'].includes(key))) });
                store.dispatch(saveGraph(window.graph.export())); store.dispatch(refreshNodesThunk());
            }
            if (options.gridLines !== undefined) store.dispatch(setGridLines(options.gridLines));
            if (options.snapLines !== undefined) store.dispatch(setSnapLines(options.snapLines));
            if (options.detailsVisible !== undefined) store.dispatch(options.detailsVisible ? showDetailsPanel() : hideDetailsPanel());
            if (options.showWaterControls !== undefined) showWaterControls = options.showWaterControls;
            document.dispatchEvent(new Event('rmpHostState'));
            publishState(message.requestId);
        } else if (message.type === 'loadProject') {
            validate(message.project!);
            const save = JSON.parse(await upgrade(JSON.stringify(materializeDraft(message.project!)))) as RMPSave;
            validate(save);
            validateCurrent(save);
            const checked = MultiDirectedGraph.from(save.graph);
            pendingInsertion = null;
            const previous = { projectId, revision, param: store.getState().param, runtime: store.getState().runtime, graph: window.graph.export() };
            const root = document.getElementById('root')!;
            loading = true;
            root.inert = true;
            try {
                window.graph.clear(); window.graph.import(checked.export());
                projectId = message.projectId; revision = 0;
                store.dispatch(clearSelected());
                store.dispatch(setFullState({ present: save.graph, past: [], future: [], svgViewBoxZoom: save.svgViewBoxZoom, svgViewBoxMin: save.svgViewBoxMin }));
                store.dispatch(refreshNodesThunk()); store.dispatch(refreshEdgesThunk());
                renderRevision++; document.dispatchEvent(new Event('rmpLoaded'));
                await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
                await Promise.all([...document.querySelectorAll<SVGImageElement>('#canvas image')].map(async element => {
                    const image = new Image(); image.src = element.href.baseVal; await image.decode();
                }));
                await document.fonts.ready;
                await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
                if (message.fit) fit();
                post('loaded', { requestId: message.requestId });
                publishState();
            } catch (error) {
                window.graph.clear(); window.graph.import(previous.graph);
                projectId = previous.projectId; revision = previous.revision;
                store.dispatch(setFullState(previous.param));
                store.dispatch(refreshNodesThunk()); store.dispatch(refreshEdgesThunk());
                store.dispatch(setSelected(previous.runtime.selected));
                if (previous.runtime.isDetailsOpen === 'hide') store.dispatch(hideDetailsPanel());
                else if (previous.runtime.isDetailsOpen === 'show') store.dispatch(showDetailsPanel());
                renderRevision++; document.dispatchEvent(new Event('rmpLoaded'));
                publishState();
                throw error;
            } finally { loading = false; root.inert = !hostEnabled; }
        } else if (message.type === 'rotateMap') {
            if (rotateMap(window.graph, message.delta!)) {
                // Finish the DOM update before acknowledging; hidden WebViews may suspend animation frames.
                flushSync(() => {
                    store.dispatch(saveGraph(window.graph.export()));
                    store.dispatch(refreshNodesThunk()); store.dispatch(refreshEdgesThunk());
                });
            }
            post('rotated', { requestId: message.requestId });
        } else if (message.type === 'fit') {
            fit(); post('fitted', { requestId: message.requestId });
        } else if (message.type === 'saveProject') {
            post('project', { requestId: message.requestId, project: JSON.parse(stringifyParam({ ...store.getState().param, present: window.graph.export() })) });
        } else if (message.type === 'undo' || message.type === 'redo') {
            store.dispatch(message.type === 'undo' ? undoAction() : redoAction());
            store.dispatch(refreshNodesThunk()); store.dispatch(refreshEdgesThunk());
            post('historyChanged', { requestId: message.requestId });
        } else if (message.type === 'export') {
            if (message.format !== 'svg' && message.format !== 'png') throw new Error('Unsupported export format');
            await document.fonts.ready;
            const exportedRevision = revision;
            const snapshot = MultiDirectedGraph.from(window.graph.export());
            const { elem, width, height } = await makeRenderReadySVGElement(snapshot, true, false, Object.values(TextLanguage), false, message.svgVersion ?? 2);
            if (message.format === 'png' && message.transparent) elem.querySelector('#rmp-background')?.remove();
            const svg = new XMLSerializer().serializeToString(elem);
            if (message.format === 'svg') post('exported', { requestId: message.requestId, revision: exportedRevision, format: 'svg', data: svg });
            else {
                const image = new Image();
                const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
                try {
                    await new Promise<void>((resolve, reject) => { image.onload = () => resolve(); image.onerror = () => reject(new Error('SVG rasterization failed')); image.src = url; });
                    const canvas = document.createElement('canvas');
                    const scale = (message.scale ?? 100) / 100;
                    canvas.width = width * scale; canvas.height = height * scale;
                    const context = canvas.getContext('2d'); if (!context) throw new Error('Canvas unavailable');
                    if (!message.transparent) { context.fillStyle = '#FFFFFF'; context.fillRect(0, 0, canvas.width, canvas.height); }
                    context.drawImage(image, 0, 0, canvas.width, canvas.height);
                    const data = canvas.toDataURL('image/png'); if (data === 'data:,') throw new Error('PNG exceeds browser canvas limits');
                    post('exported', { requestId: message.requestId, revision: exportedRevision, format: 'png', data: data.split(',')[1] });
                } finally { URL.revokeObjectURL(url); }
            }
        } else throw new Error(`Unknown RMP command: ${message.type}`);
    } catch (error) { loading = false; post('error', { projectId: message.projectId, requestId: message.requestId, message: String(error) }); }
}
let commands = Promise.resolve();
window.cslRmp = { dispatch(message) { commands = commands.then(() => dispatch(message)); return commands; } };
window.chrome?.webview.addEventListener('message', event => { void window.cslRmp.dispatch(event.data); });
const frame = () => new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
document.addEventListener('pointerdown', event => {
    if (!hostEnabled || !pendingInsertion || event.button !== 0 || !(event.target instanceof Element) || !event.target.closest('#canvas')) return;
    event.preventDefault(); event.stopImmediatePropagation();
    const request = pendingInsertion;
    const viewport = document.querySelector<SVGGElement>('#rmp-rotation')!;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(viewport.getScreenCTM()!.inverse());
    commands = commands.then(async () => {
        if (pendingInsertion !== request) return;
        pendingInsertion = null;
        placing = true;
        publishState();
        const root = document.getElementById('root')!;
        root.inert = true;
        const previousSelection = store.getState().runtime.selected;
        let insertion: ReturnType<typeof buildInsertion> | undefined;
        try {
            insertion = buildInsertion(request);
            insertion.graph.forEachNode((id, attrs) => window.graph.addNode(id, structuredClone(attrs)));
            insertion.graph.forEachEdge((id, attrs, from, to) => window.graph.addDirectedEdgeWithKey(id, from, to, structuredClone(attrs)));
            store.dispatch(clearSelected());
            store.dispatch(refreshNodesThunk()); store.dispatch(refreshEdgesThunk());
            await frame(); await frame();
            await Promise.all([...document.querySelectorAll<SVGImageElement>('#canvas image')].map(async element => {
                const image = new Image(); image.src = element.href.baseVal; await image.decode();
            }));
            await document.fonts.ready; await frame();
            positionInsertion(insertion, point.x, point.y);
            store.dispatch(saveGraph(window.graph.export()));
            store.dispatch(refreshNodesThunk()); store.dispatch(refreshEdgesThunk());
            store.dispatch(setSelected(insertedSelection(insertion)));
            store.dispatch(showDetailsPanel());
            await frame();
        } catch (error) {
            insertion?.graph.forEachNode(id => { if (window.graph.hasNode(id)) window.graph.dropNode(id); });
            store.dispatch(refreshNodesThunk()); store.dispatch(refreshEdgesThunk());
            store.dispatch(setSelected(previousSelection));
            post('error', { message: String(error) });
        } finally {
            placing = false;
            root.inert = !hostEnabled;
            publishState();
        }
        if (insertion?.focusText) {
            const input = document.querySelector<HTMLTextAreaElement>('[data-testid="side-panel-root"] textarea');
            input?.focus({ preventScroll: true }); input?.select();
        }
    });
}, true);
window.addEventListener('keydown', event => {
    if (hostEnabled && pendingInsertion && event.key === 'Escape') {
        event.preventDefault(); event.stopImmediatePropagation(); pendingInsertion = null; publishState(); return;
    }
    if (hostEnabled && event.target instanceof Element && event.target.closest('#canvas')) {
        const key = event.key.toLowerCase();
        const history = (event.ctrlKey || event.metaKey) && !event.altKey && (key === 'z' || key === 'y');
        if (event.key === 'Escape' || history) {
            event.preventDefault(); event.stopImmediatePropagation();
            if (history) {
                store.dispatch(key === 'y' || event.shiftKey ? redoAction() : undoAction());
                store.dispatch(refreshNodesThunk()); store.dispatch(refreshEdgesThunk());
            } else { store.dispatch(clearSelected()); store.dispatch(setMode('free')); }
            return;
        }
    }
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    const key = event.key.toLowerCase();
    const command = key === 's' ? (event.shiftKey ? 'saveAs' : 'save') :
        key === 'o' && !event.shiftKey ? 'open' : key === 'e' && event.shiftKey ? 'export' : undefined;
    if (command) { event.preventDefault(); event.stopImmediatePropagation(); if (hostEnabled) post('commandRequested', { command }); }
}, true);
post('ready');
