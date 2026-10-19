# Third-party notices

CSLModernMap includes third-party dependencies and acknowledges implementation references.

## Desktop and rendering dependencies

- Qt for Python / PySide6: Qt Project, LGPL-3.0-only or commercial/GPL alternatives. QtCore/QtGui remain runtime geometry dependencies even though the Qt viewer is excluded. Standalone distributions must preserve Qt/PySide notices and meet the applicable replacement/relinking requirements.
- Microsoft Edge WebView2 Runtime and SDK assemblies (Windows build): Microsoft license terms shipped by their respective distributors.

## POI icons

The shared POI library under `assets/cn_icons/` includes 150 outlined glyphs from Scott de Jonge's [Map Icons](https://github.com/scottdejonge/Map-Icons), seven project-drawn symbols and one project default marker. The pinned upstream font is declared SIL OFL 1.1; upstream code is MIT. No font binary is bundled. Attribution, pinned source information and license details are preserved in `assets/map_icons_source/THIRD-PARTY.md`. The two retired artwork sets are no longer distributed.

## Geographic export clipping

- Shapely 2.x: Shapely contributors, BSD-3-Clause. https://github.com/shapely/shapely
- GEOS (bundled with Shapely wheels): GEOS contributors, LGPL-2.1-or-later. https://libgeos.org/
- Preserve Shapely distribution metadata, its license files and bundled GEOS notices in standalone packages.

## Embedded Rail Map Painter

- Upstream: https://github.com/railmapgen/rmp
- Pinned tag: `rmp-6.0.18`, commit `401ddafeb869255d10a5b51e3fae1acb34322d57`.
- License: GPL-3.0-only; upstream license: `dev/third_party/rmp/LICENCE`.
- Local changes and build instructions: `dev/third_party/rmp/CSLMM-UPSTREAM.md`.
- The WinUI build bundles local RMP facilities/logo and an offline editor entry.
  The Metro Ribbon's About RMP dialog credits upstream contributors, identifies
  the modified embedded version and provides the software license and original
  bilingual image terms. Exports omit the visible RMP overlay; no subscription
  entitlement is granted or emulated.
- Upstream image terms: `dev/third_party/rmp/RMP-IMAGE-TERMS.txt`, reproduced from
  the pinned `src/components/page-header/terms-and-conditions.tsx`. These include
  attribution recommendations and separate commercial-use/design/font notices;
  do not treat the software GPL as permission for all depicted assets.
- Noto Sans SC 400: Google and contributors, SIL OFL-1.1, supplied by pinned
  `@fontsource/noto-sans-sc` 5.3.1. Original upstream public fonts are excluded.
- Bundled npm dependency versions and license files are collected by
  `tools/renderer/build_rmp_editor.py` into `Assets/Rmp/licenses`.
- Expression parser: expr-eval-fork 3.0.3, MIT, based on expr-eval by Matthew
  Crumley and contributors; https://github.com/jorenbroekema/expr-eval . The
  installed package's full license is included in the collected npm notices.

## WinUI native ribbon

- RibbonSpace.WinUI / RibbonSpace.Core 1.1.0: copyright 2026 Wiesław Šoltés, MIT.
- Upstream: https://github.com/wieslawsoltes/RibbonSpace
- Full license: `LICENSES/RibbonSpace-MIT.txt`, bundled as `Assets/Licenses/RibbonSpace-MIT.txt`.
- The native viewer requires no commercial ribbon license key.

## Tabler Icons and Fluent UI System Icons

- Tabler Icons CSS identifies version 2.47.0; copyright 2020–2023 Paweł Kuna; MIT.
- Font, CSS and generated Ribbon artwork: `dev/CSLModernMapRenderer/resources/fonts/` and `winui/Assets/Ribbon/`.
- License: `dev/CSLModernMapRenderer/resources/fonts/Tabler-LICENSE.txt`.
- Upstream: https://github.com/tabler/tabler-icons/tree/v2.47.0
- Fluent UI System Icons: copyright Microsoft Corporation; MIT. License: `dev/CSLModernMapRenderer/resources/icons/fluent/LICENSE.txt`.

## Web preview and labels

- MapLibre GL JS 6.4.1: BSD-3-Clause; https://github.com/maplibre/maplibre-gl-js . The preview build collects installed production dependency notices into `THIRD_PARTY_LICENSES.txt`, also embedded into exported HTML.
- i18next: MIT; notice: `dev/CSLModernMapRenderer/viewer/public/I18NEXT_LICENSE.txt`.
- Vellum label implementation: copyright 2026 Sebastian Enrique Vargas Pizango; MIT; notice: `dev/CSLModernMapRenderer/viewer/public/VELLUM_LICENSE.txt`.
- Runtime dependency versions are pinned by the viewer's `package-lock.json`.

## Python engine

Runtime dependencies are declared in `dev/CSLModernMapRenderer/requirements.txt`: Pillow (HPND), fonttools (MIT), NumPy (BSD-3-Clause), PyYAML (MIT), pypinyin (MIT), lxml (BSD-3-Clause with separately licensed bundled libxml2/libxslt), aggdraw (HPND), contourpy (BSD-3-Clause), Shapely (BSD-3-Clause) and PySide6-Essentials (LGPL/GPL/commercial alternatives). Python itself uses the PSF license. These summaries do not replace each installed distribution's notices. Except for PySide6-Essentials, versions are ranges; audit the actual frozen build, including native libraries, before binary distribution.
