# Getting started with CSLModernMap

[中文目录](Home.md)

CSLModernMap draws maps from cities exported by the Cities: Skylines and Cities: Skylines II mods. The in-game mod exports the city data. The desktop viewer opens that data and produces maps.

This guide follows the current Windows WinUI viewer. It requires x64 Windows 10 build 19041 or later. The older macOS viewer has a different interface.

## Install and open a city

1. Install and enable the CSLModernMap mod for your game: [Cities: Skylines](https://steamcommunity.com/sharedfiles/filedetails/?id=3781187198) or [Cities: Skylines II](https://mods.paradoxplaza.com/mods/159550/Windows).
2. Load your city and open the mod's settings.
3. Export the city data, or use **Export and Open**. On Windows, **Install / Update Renderer** can prepare the viewer separately where offered by the mod.
4. In the viewer, choose **Open City** and select the exported `.cmm` or `.cmm.gz` file if it has not opened automatically.
5. Wait for the preview, choose a theme, then export a PNG at 2K or 4K to check the result.

Packaged downloads are listed on the mod or release page. Keep the complete application folder when extracting a desktop package.

A city data file is a snapshot. Export again after changing roads, buildings or routes in the game. Older `.mmap` and `.mmap.gz` files can also be opened.

## Find your way around

The ribbon at the top contains the main actions. The central map lets you pan and zoom. The settings panel contains options for the selected layer or task. Progress messages show when loading or exporting is still in progress.

The city map, public transport map and metro diagram have different controls and outputs. Use the return-to-city-map action to resume editing the regular map's layers.

## Themes, layers and names

A **theme** controls the overall colours and drawing style. Start with a built-in theme, then change individual settings as needed. The theme editor can save a customised appearance.

A **layer** controls a group of map features:

| Layer | What it shows |
| --- | --- |
| Terrain and water | Land, hills, rivers and coasts |
| Roads and railways | Street and track networks |
| Buildings and built-up areas | Building footprints and the wider urban background |
| Points of interest | Icons for facilities and places |
| Labels | Road, district and other names |
| Districts and areas | Named areas and their boundaries |
| Traffic | Traffic information included in the export |

A point of interest, or POI, is a place shown with a map icon. Its building shape, icon and name have separate controls. If the map feels crowded, lower icon density, show fewer categories or export a smaller area.

Custom names and game-generated names may have separate display options. The available names also depend on the data exported by the game mod.

Some controls appear only with matching data, such as facility-area selection for CS2 and traffic layers with a traffic snapshot.

## Choose an area and export

Use the full city for an overview. For a close-up, draw a selection on the map, then choose the selection export action. The range window also offers full-city, tile-based and manual selection.

Check the range reference in the export window, choose the destination and size, then export. A smaller map area at the same image size gives more space to local details. Larger images and antialiasing take more time and memory.

| Output | Use |
| --- | --- |
| PNG from the city or transport map | Sharing, printing and layout work |
| HTML from the city map | Panning and zooming in a browser |
| PNG or SVG from the diagram editor | Sharing or further image layout work |
| RMP project | Continuing to edit the network diagram |
| RMG project | Editing route projects in Rail Map Generator |

The HTML map includes the map data and viewer resources. Open the saved file in a browser to view it. For PNG, the export window also offers a copy action for the most recent successful image.

## Public transport maps

A public transport map shows routes and stops against the city, following the exported route geometry. Open the public transport view, select the transport types and routes, then adjust lines, stops and labels.

Start with the routes you want to explain. Reduce background detail if it competes with the lines. Nearby stop markers and names can be grouped using the available merge and aggregation settings. Export this view as PNG.

If a route is missing, check that it exists in the game and is selected in the route list. Export fresh city data after changing a route.

## Metro diagrams and editable projects

A diagram arranges the network for readability. Distances between stations reflect the layout rather than travel distance.

1. Open the metro diagram workspace.
2. Select the desired routes and generation options, then generate a draft.
3. Check station names, interchanges and line layout.
4. Adjust stations, names and lines in the embedded Rail Map Painter editor.
5. Save an RMP project, then export PNG or SVG.

Save a copy before trying a new generated layout. Reopen the RMP project to continue working on the same diagram.

RMG export is a separate route-project output based on the city network and selected routes. Use RMP when you want to preserve the manually edited network canvas.

Related tools: [Rail Map Painter](https://railmapgen.github.io/rmp/) and [Rail Map Generator](https://railmapgen.github.io/rmg/).

## Random close-ups and interchange collections

**Random close-up** chooses an area using the selected preference, such as buildings, water, parks, roads or transport. Inspect the selection, adjust it if needed, then export. Keeping the same city data and seed helps repeat a selection.

**Interchange collection** arranges detected interchanges in a collage. Choose the number of interchanges, each tile's size, labels and layers, then export the collection. Fewer labels can make the road structure easier to read.

## When something looks wrong

- **Old city content:** export fresh city data and open it.
- **Missing place names:** check POI categories, name options and game-generated names, then inspect the area at a larger scale.
- **Crowded text or icons:** reduce density or categories, or make a separate close-up.
- **Preview and PNG differ:** compare the same theme, range and visible layers at the final output size.
- **Slow export:** try a smaller range or image first, then increase the size for the final result.

For a report, include the application version, game, steps, expected result, actual result and a screenshot. Diagnostics are available in the tools area. The Windows startup log is at `%LOCALAPPDATA%/CSLModernMap/logs/winui-startup.log`.

Submit reports through [GitHub Issues](https://github.com/Liuiuera/CSLModernMap/issues). A city data file can help reproduce a problem; check city and custom names before sharing it.
