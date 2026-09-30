# 03 · 按 case 同步预览、等效结果与图片（2026-09-30）

`tools/sync_out.py` 是 `out/` 的统一入口。每个 case 保留一个对应目录；
KiCad `native/` 与 `clean/` 分别归档。目录包含：

```text
out/<case>/
  <step>.json / <step>.svg / <step>.html
  <step>.MaterialMap.csv / <step>.MaterialMap.meta.json
  <step>.images.html
  masks/<layer>.png
  heatmaps/<layer>.png
  grid/cartesian-grid.png / cartesian-grid.svg / cartesian-grid.stats.json
  regions/regions.png / regions.svg / regions.csv / regions.stats.json  # 分区诊断
```

总入口为 `out/index.html`，列出实际 XY 网格数、CSV/meta、逐层图例；
已生成 region 诊断图的 case 另有分区链接。`out/` 是本地生成物，不纳入 Git。

## 使用

```bash
uv sync --all-extras
uv run --all-extras python tools/sync_out.py --grid-level standard --heatmap-grid

# 改动等效器后重算；沿用有效 preview-2 JSON
uv run --all-extras python tools/sync_out.py --grid-level standard --heatmap-grid --force-equivalent

# 改动解析器后加 --force；清理没有来源 case 的输出用 --prune
# X/Y 可分别设置，例如最小 0.1/0.2 mm，最大 4/3 mm
uv run --all-extras python tools/sync_out.py --px 9600 --py 7200 \
    --min-cell-mm 0.1 0.2 --max-cell-mm 4 3 --heatmap-grid
```

`--px/--py` 可成对覆盖默认密集光栅。常规采用 `--grid-level coarse|standard|fine`，默认 standard；尺寸参数进入专家模式。`--min-cell-mm/--max-cell-mm` 默认 0.1 / 5 mm，
各接受一个值或 X Y 两个值。旧的 `--nx/--ny/--coarse-grid` 已移除。
所有 region 边界传播为全局 X/Y 轴线，相邻轴线区间按尺寸上下限细分；不兼容就报错。
网格细分不再受整板格数预算限制，因此中部宽区间也会细分。
光栅像素仍用于等效积分，热图直接读取重新计算的 CSV，不做二次聚合。

缺少用户材料配置时使用项目铜/FR4 默认表，命令行和 meta 明确警告。

## 复用与重建

预览必须为一个 step 的 `odbpp/preview-2`，且输入来源一致。
等效结果必须为 `odbpp/materialmap-5`：JSON 来源、自动/显式光栅参数、网格档位或各轴尺寸上下限、
有/无热图边界的配置、pipeline_fingerprint，以及 CSV/meta、图例和逐层 PNG 均匹配才复用。
只更改最大或最小尺寸也会重算。成功替换后清理已不属于当前层列表的旧 PNG。

## region 分区图

```bash
uv run --all-extras python tools/plot_regions.py \
    out/designodb-rigidflex.tgz/CELLULAR_FLIP-PHONE.json --px 9600 --py 7200
uv run --all-extras python tools/plot_regions.py \
    out/kicad-kitchen-sink/PCB.json --px 9600 --py 7200
```

使用与等效器相同的矩形化函数；默认写入该 case 的 `regions/`。
颜色表示 region 面积（对数色标），黑线是矩形边界，红色虚线是原 profile。
包括整板图、两端放大图和短边直方图；SVG 悬停显示编号、尺寸、面积，CSV 列出全部矩形。
同步工具每次从最终 meta 的实际 rectangles 绘制；修改光栅只改变采样，分区坐标必须相同。
指定 --metadata <step>.MaterialMap.meta.json 可独立绘制同一最终分区。

## 历史结果

此前按整板 400×400，以及目标 40×40 导出的结果已被尺寸约束版本替换。
目标格数取强制边界最低数量时，曾产生中央 64.52 mm 宽的单列，说明最低格数
不能代表合理的空间分辨率。当前实现及验证见 [05](05-inscribed-architecture.md)；[04](04-grid-sizes-and-regions.md) 为历史记录。

## 全局笛卡尔栅示意图

每个 case 的 `grid/cartesian-grid.png/svg` 直接读取最终 meta 的
`grid.x_lines_mm/y_lines_mm`，不重新划网格；同时逐单元检查实际边界与轴线一致。
图中蓝线为建模域内单元边界，灰色虚线为域外轴线延伸，橙线为 region 边界，
红色虚线为原始 step profile。包含整板、边缘和主体放大图；右侧列出实际 nx/ny、
实体单元数、region 数及尺寸约束。该图是共享 XY 几何，逐层存在性仍以 meta/CSV 为准。

```powershell
uv run --all-extras python tools/plot_grid.py out/kicad-kitchen-sink/PCB.json
```

`sync_out.py` 自动生成并在 `out/index.html` 增加“全局笛卡尔栅”链接。
该图与 region 面积诊断图、材料掩膜及物性热图分别保存。
