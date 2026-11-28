# CSLModernMap Code Mod

[English](README.md) | [简体中文](README.zh-CN.md)

The Cities: Skylines and Cities: Skylines II code mods for CSLModernMap. Both export the current city as a versioned `.cmm` map archive.

The exporter source is in [CS1](CS1/) and [CS2](CS2/). Official mod releases include the [companion viewer](../README.md), which reads exported maps without accessing game saves.

## Features

- Exports roads, tracks, buildings, functional areas, public transport, terrain, water, and native traffic snapshots.
- Writes city data and optional satellite images to the CMM archive.
- Opens completed exports with the companion renderer included in official releases.
- Preserves native and custom names, area ownership, and facility relationships.
- Shows export and installation progress, with settings available in 12 languages.

## Build

CS1 requires Windows, .NET SDK 10 and local game/mod references;
see [CS1 build setup](CS1/README.md). CS2 requires Windows, the repository's .NET SDK 10 and the official Cities: Skylines II modding toolchain.

Run the commands below from the repository root.

```powershell
# Cities: Skylines
dotnet build codemod\CS1\CSLModernMap.csproj -c Release
# Cities: Skylines II
dotnet build codemod\CS2\CSLModernMapCs2.csproj -c Release
```

The CS1 build produces a verified `codemod/CS1/bin/private/CSLModernMap.dll` with its license files.
The CS2 build also deploys into the local game Mods directory; exit CS2 first.
Game/mod assemblies and viewer payloads must be supplied separately. For Windows releases,
stage the signed companion viewer MSIX before building CS2 or assembling the CS1 release folder.

    python codemod/tools/pack_cs2_renderer.py --msix path/to/viewer.msix
    python codemod/tools/pack_cs2_renderer.py --msix path/to/viewer.msix --target path/to/CS1-release

The staging command writes the MSIX and its application identity. Both export buttons offer
installation when the viewer is missing. After you return from the installer, the mod checks again.
If the viewer is still missing, it saves the map and displays a warning. **Export and Open**
launches the installed viewer after saving. **Install / Update Viewer** opens the installer again,
including when the same version is already installed.
The mods do not download required files from the network.

## License

The project-owned mod source is copyright © 2026 Liuiuera and licensed under [GPL-3.0-only](../LICENSE). See the [third-party notices](THIRD_PARTY_NOTICES.md) for component licenses.

## Contributors

- Liuiuera
- OpenAI Codex

Some code and documentation were generated with AI assistance and reviewed by the project maintainer.
