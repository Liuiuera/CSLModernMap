# POI 图标资源生成

`draw_map_icons.py` 从固定版本的 Map Icons 字体及项目自绘坐标生成图标。输入清单为 `map_icons_catalog.json`；版权说明为 `map_icons_NOTICE.md`。

```powershell
python tools/poi/draw_map_icons.py --help
```

源字体需自行取得对应提交 `dbf6fd7caedd60d11b5bfb5f267a114a6847d012`，上游为 https://github.com/scottdejonge/Map-Icons 。默认只生成指定输出目录；`--install` 会更新共用图库与来源记录。保留 `assets/map_icons_source/` 内的许可文件。
