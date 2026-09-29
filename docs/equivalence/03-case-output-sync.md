# 03 · 按 case 同步预览、等效结果与图片（2026-09-30）

`tools/sync_out.py` 是 `out/` 的统一入口。每个输入 case 在 `out/` 保留一个
对应目录；KiCad 的 `native/` 与 `clean/` 仍分别归档。一个目录包含：

```text
out/<case>/
  <step>.json / <step>.svg / <step>.html
  <step>.MaterialMap.csv / <step>.MaterialMap.meta.json
  <step>.images.html
  masks/<layer>.png
  heatmaps/<layer>.png
```

`out/index.html` 为全部 case 的预览、CSV、meta 与图例页提供入口。图例页按层
链接掩膜和三方向热导率热图。`out/` 是本地生成物，不纳入 Git。

## 使用

```bash
uv sync --all-extras
uv run --all-extras python tools/sync_out.py --px 400 --py 400 --prune

# 只改变物性等效或图片代码时，沿用现有 preview-2 JSON
uv run --all-extras python tools/sync_out.py --px 400 --py 400 --force-equivalent

# 解析器、JSON 契约或预览代码变化时，全部重建
uv run --all-extras python tools/sync_out.py --px 400 --py 400 --force
```

`--px`、`--py` 必填；`--nx`、`--ny` 不填时分别等于 `px`、`py`，显式填写
须在 `1..px`、`1..py` 内。上述 400×400 是本次批量同步使用的分辨率，
不是等效器的隐式默认值。缺少用户材料配置时，等效器使用项目铜/FR4 默认值，
命令行和 meta 都会给出警告。

工具只复用 schema 为 `odbpp/preview-2` 的预览；只有 JSON 来源、像素与
网格参数、CSV/meta、图例页及逐层 PNG 都齐全时才复用等效结果。重新计算后
会删除已不属于本次物理层列表的旧 PNG，避免同一 case 混有过时图片。

## 本次验证

18 个 case 均已完成 400×400 同步：`out/` 共 81 张逐层掩膜、81 张热图，
CSV 列含 `kx`、`ky`、`kz`、密度和比热，合计约 1.603 GB。再次运行全部复用；
端到端测试还验证了缺失输出重建、参数变化重算、旧图片清理和索引链接。
原先会删除仓库真实 `out/minimal-test-odb` 的 CLI 测试已改用 pytest 临时目录。
全量 702 项测试通过；`out/index.html` 的 108 个链接和各层图例页图片链接均能解析到文件。
