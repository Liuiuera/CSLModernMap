# Wiki 维护

入门文档源文件放在 `docs/wiki/`，可以先在主仓库里审阅。开发部分保留占位页，后续再补内容。

## 本次页面

首页、八篇中文入门内容、一篇英文入门指南、开发占位页和侧边栏，共十二个 Markdown 文件。

## 准备导入

在本地生成供 GitHub Wiki 使用的页面：

```powershell
python tools/docs/export_wiki.py --output ../cmm-wiki-import
```

脚本复制页面，并把仓库中的 `.md` 链接转换成 Wiki 页面链接。它只准备本地文件。

## 审阅后同步

1. 在仓库设置中启用 Wiki。
2. 首次使用时，在 GitHub 新建一页 `Home`，初始化 Wiki 仓库。
3. 获取 `https://github.com/Liuiuera/CSLModernMap.wiki.git`。
4. 将已导出的页面复制到该 Wiki 工作副本，查看差异并提交。
5. 推送后检查首页、侧边栏和语言入口。

平时修改先回到 `docs/wiki/`，审阅后重新导出并同步，便于保留统一的来源。

## 内容约定

每页先说明功能是什么，再给出常用操作和输出结果。按钮名称以当前界面为准，平台差异写在需要的位置。更新功能时，一并核对相关入门页面。
