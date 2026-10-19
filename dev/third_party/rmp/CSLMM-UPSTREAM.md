# CSLModernMap embedded Rail Map Painter

- Upstream: https://github.com/railmapgen/rmp
- Tag: rmp-6.0.18
- Commit: 401ddafeb869255d10a5b51e3fae1acb34322d57
- License: GPL-3.0-only. Native save version: 77.
- Source: vendored snapshot of the pinned upstream commit, with local modifications below.

The public CSLModernMap tree omits upstream test sources, test setup and
test-only dependencies, plus the bundle visualizer. Runtime source and the
embedded build entry are retained. Local lockfile changes record this cleanup.
The lockfile also retains required peer dependencies for default `npm ci`;
it does not require `--legacy-peer-deps`.

Unused upstream canvas-size, mime-types and react-router-dom packages and
their type packages are removed. Canvas sizing uses the local workspace hook.

The public tree replaces the unmaintained `expr-eval` dependency with
MIT-licensed `expr-eval-fork` 3.0.3 for its security fixes. The parser API and
master-attribute formula handling are retained. Other security updates stay
within the declared dependency ranges; exact versions are in the lockfile.

Embedded patches add a separate Vite entry, local component theming, a local
colour picker, a local tool list and the WinUI message bridge. The drawing and
save format remain upstream. Account, subscription, cloud gallery/image/name services,
telemetry and their obsolete web UI have been removed. Native license notices are retained. Embedded SVG/PNG exports omit the visible RMP
logo and attribution overlay. Both build entries use the local host-driven editor.
Clipboard and file import exclude Image/Master.

The native About RMP dialog identifies upstream contributors and this modified
build, links the source repositories, and displays the bundled GPL, third-party
notices and original bilingual image terms. RMP-IMAGE-TERMS.txt preserves the
fixed upstream terms with JSX markup removed; they are distinct from the
software license. The native canvas heading also credits RMP.

Noto Sans SC 400, @fontsource/noto-sans-sc 5.3.1, is used for portable offline
text and embedded SVG fonts (SIL OFL-1.1). Upstream public/fonts is excluded from
the embedded build. Facilities and logo retain the upstream GPL notices.

Build: `npm ci`, then `python tools/renderer/build_rmp_editor.py` from the CMM
repository root. Only dist-embed is distributed. Node/npm and node_modules are
build dependencies. Preserve package-lock.json when updating the subtree.

Native-format compatibility is checked against the pinned upstream source.
There is no runtime cloud connection or automatic update.

Route imports use real station nodes and native diagonal paths; RMP owns corner
rounding and parallel offsets. Export paints a white SVG background, used for SVG and PNG rasterization.
Upstream v77 has no project background setting; the canvas white is only CSS.

Water imports preserve the final Python surface, including smooth shores, river
bands and island holes. Native Fill cycles are partitioned by straight cuts to
fit the upstream loop limit without changing the surface. The native Ribbon
adds "Water control points", off by default. It hides imported water
handles and hidden boundary guides in the editor only, without changing graph
visibility, save data, undo history or exports. Existing CMM water node/edge IDs
are supported, including drafts created before this switch was added.

The embedded entry supplies the canvas and native Details panel inside the WinUI
main window. The host's Metro Ribbon owns project, generation, history, drawing,
elements, view and export commands; there is no embedded title bar or Ribbon.
Station, local line path/style and miscellaneous tools come from the native
registries through getTools; setMode validates their availability. setViewOptions
uses native Redux actions. editorState reports mode, selection, history availability
and view switches only when those values change. Protocol version 1 retains
projectId/requestId/revision. Project file I/O remains with the WinUI host.
The workspace is hidden between city/transit modes, retaining the project and history.
The measured workspace size is shared by canvas, snapping and touch controls;
both build entries use the same measured workspace. PNG rasterization
paints an opaque white canvas before drawing the native SVG, including edge pixels.
The I18nText tool is omitted: upstream has no contents editor for this node and
its text form cannot edit the translated contents. Ordinary Text remains available.

The embedded skin uses Lucide icons from the existing react-icons dependency
and scoped native Details styling. Details icons preserve upstream handlers and local editing fields.

New WinUI drafts carry only station, route, source line colour, water geometry
and facility metadata. The embedded frontend creates native objects from the RMP
registries, including line widths, station dimensions, fonts and river colour;
Python rendering widths, casing and label appearance are not imported. Saved
projects bypass draft styling and retain their edited attributes. Legacy CLI/Qt
RMP export retains its existing conversion.

Local editing has no account or subscription state: generic layers, local paths/styles,
parallel lines and clipboard operations operate directly on the native graph.
Image/Master import remains outside the host scope. Station-name romanisation uses
local pinyin; fonts, facility symbols and export resources are bundled locally.

The host exposes title, legend and facility insertion through beginInsert and
cancelInsert. Facilities and translated labels share the upstream registry.
Placement creates ordinary native objects in one history transaction, measured
against rendered symbol bounds; cancelling a pending placement creates nothing.
Legends snapshot visible route styles and used station/facility types, excluding
water, helper geometry and previous legends. Optional cmmSourceLineName and
cmmDecoration attributes preserve source labels and identify legend objects;
they do not introduce node types, permanent groups or a project version change.

The native export window shares PNG/SVG format selection and system file pickers
with other map outputs. The embedded export bridge accepts PNG scale and
transparency plus SVG version (1.1 or 2), reusing makeRenderReadySVGElement.
Transparent PNG exports remove the painted background before rasterization;
fonts remain embedded and existing attribution behavior is preserved. Project
files are unchanged.
