# Map-Icons POI artwork attribution

Original glyphs: **Scott de Jonge / Map Icons**.

- Repository: https://github.com/scottdejonge/Map-Icons
- Pinned commit: `dbf6fd7caedd60d11b5bfb5f267a114a6847d012`
- Original copyright statement in the repository license: Copyright (c) 2016 Scott de Jonge.
- Upstream README declares the `dist` font files licensed under **SIL Open Font License 1.1**, code under **MIT**, and remaining documentation under **CC BY 3.0**.
- OFL official text: https://openfontlicense.org/open-font-license-official-text/
- Original README and root MIT license are preserved alongside this notice. The MIT license alone is not a blanket license claim for the font.

These PNGs and outlined SVG illustrations are generated from the unmodified upstream `dist/fonts/map-icons.ttf`, using character codes from `dist/css/map-icons.css`. No font files, Google Maps integration code, or online resources are bundled or required by the application. Any future redistribution of the font itself must include its applicable OFL and copyright notices.

CSLModernMap adds category colors, a vertical gradient, a white circular rim, a soft shadow, and a white foreground. The `default-gray` marker is a geometric gray disk with a white center, based on the project's existing `pin4` appearance; it is not an upstream font glyph.

Category colors reference the retired Apple-style `assets/poi_icons` artwork (historical source paths recorded in the catalog; the retired files are no longer shipped). Only background colors are sampled; its glyph artwork is not copied. The catalog records source files, hashes, sampled colors and semantic assignments. Facilities without an exact old counterpart use a related category color; this is a palette decision, not an approved POI mapping.

150 upstream glyphs, one default marker, seven project-drawn symbols and one transparent traffic-light placeholder are included. The custom low-rise house and apartment symbols follow the meaning of the existing project-drawn residential icons; office and factory symbols are newly drawn geometric shapes. Park, school and university replace the corresponding upstream glyphs with a tree, school building and complete graduation cap. The park uses green at the user's request, overriding the retired Apple palette's yellow. Their coordinates are recorded in `draw_map_icons.py` and the generated catalog, separate from upstream font glyphs. No Apple raster artwork is reused in these shapes.

22 interface, marker-shape, address-code and weather symbols are explicitly excluded in the catalog. Artwork availability does not assert that every glyph has a matching game facility or that POI mappings have been reviewed.

The traffic-light icon is an intentionally fully transparent placeholder in both appearances. Its filename and mappings are retained; no signal lamps, background or border are drawn.
