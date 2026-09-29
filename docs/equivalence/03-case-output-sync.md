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
uv run --all-extras python tools/sync_out.py --px 400 --py 400 --coarse-grid 40 --prune

# 只改变物性等效或图片代码时，沿用现有 preview-2 JSON
uv run --all-extras python tools/sync_out.py --px 400 --py 400 --coarse-grid 40 --force-equivalent

# 解析器、JSON 契约或预览代码变化时，全部重建
uv run --all-extras python tools/sync_out.py --px 400 --py 400 --coarse-grid 40 --force
```

`--px`、`--py` 必填；`--nx`、`--ny` 不填时分别等于 `px`、`py`，显式填写
须在 `1..px`、`1..py` 内。上述 400×400 是本次批量同步使用的分辨率，
不是等效器的隐式默认值。缺少用户材料配置时，等效器使用项目铜/FR4 默认值，
命令行和 meta 都会给出警告。

`--coarse-grid N` 是批量规划选项，与 `--nx/--ny` 互斥。它使用与等效器
相同的 JSON step profile 和默认矩形化精度，先计算 region 强制边界数，
再选 `nx=max(N, 强制 X 边界数-1)`、`ny=max(N, 强制 Y 边界数-1)`。
实际网格数不能超过光栅尺寸，矩形化失败时停止；不会放宽误差判据或填平孔洞。
精确指定 `--nx/--ny` 时仍保持原有规则，不兼容就报错。
粗网格模式自动绘制 CSV 单元边界，亦可单独使用 `--heatmap-grid`。
总索引新增实际 XY 网格数列，图例页也记录是否画了边界。

工具只复用 schema 为 `odbpp/preview-2` 的预览；只有 JSON 来源、像素与
网格参数、CSV/meta、图例页及逐层 PNG 都齐全时才复用等效结果。重新计算后
会删除已不属于本次物理层列表的旧 PNG，避免同一 case 混有过时图片。

## 原同步验证

18 个 case 均已完成 400×400 同步：`out/` 共 81 张逐层掩膜、81 张热图，
CSV 列含 `kx`、`ky`、`kz`、密度和比热，合计约 1.603 GB。再次运行全部复用；
端到端测试还验证了缺失输出重建、参数变化重算、旧图片清理和索引链接。
原先会删除仓库真实 `out/minimal-test-odb` 的 CLI 测试已改用 pytest 临时目录。
全量 702 项测试通过；`out/index.html` 的 108 个链接和各层图例页图片链接均能解析到文件。

## 粗网格重新导出（2026-09-30）

光栅保留 400×400，全部重新计算 MaterialMap，再从新 CSV 生成 81 张逐层
`kx/ky/kz` 热图，并用灰线显示单元边界。原 CSV、掩膜、热图与图例被当前结果替换。

| case | 实际整板 XY 网格 | 原因 |
|---|---|---|
| `designodb-rigidflex.tgz` | 50×40 | X 方向至少需要 50 格才能保留 region 边界 |
| `kicad-kitchen-sink` | 187×127 | 复杂外形与孔洞的强制边界已达到该数量 |
| 其他 16 个 case | 40×40 | 目标可满足全部 region 边界 |

网格可以不均匀，板外不输出单元；逐层 CSV 行数也由各层计算域决定。
例如 100×80 mm 的 `minimal-test-odb` 单元为 2.5×2 mm，
每格含 10×10 个光栅样本，两个等效层合计 3200 行。

测试覆盖孔洞强制边界、目标与精确网格互斥、粗网格多像素积分、
有/无边界图片的缓存区分，以及无计算域区域不画网格。
全量 705 项测试通过。实际输出共 136954 行物性数据、81 张掩膜和 81 张热图；
逐行验证热导率、密度和比热为有限正数并位于默认两材料范围内，
270 个文件链接全部有效。`out/` 总大小约 227.17 MB，重复同步全部复用。
