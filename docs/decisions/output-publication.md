# 输出布局、发布与复用

## 正式产物

```text
out/<case>/
  pcbkit/
    intermediate.json
    preview.svg, preview.html
    diagnostics/run.json, artifacts.json
  pcbeq/
    materialmap.json
    masks/*.png
    heatmaps/*.png
    regions/regions.png, regions.svg, regions.csv, regions.stats.json
    grids/cartesian-grid.png, cartesian-grid.svg, cartesian-grid.stats.json
    index.html
    diagnostics/run.json, artifacts.json
  pipeline/
    run.json
    pcbkit.stdout.log, pcbkit.stderr.log
    pcbeq.stdout.log, pcbeq.stderr.log
```

`out/index.html` 索引各案例。解析器与等效器各有运行报告和产物清单；诊断独立于两个数值接口。
region CSV 是几何编号 / 尺寸表，MaterialMap 的正式数值格式为 JSON。

## 发布决策

两个产品在同级临时目录完成计算和所请求的图片，再发布正式目录；发布失败执行回滚。
产物清单只管理本软件生成的文件，成功替换时清理登记中失效的产物，保留用户自行放入的文件。

`pcbkit` 返回 0 表示成功，1 表示带显式失败层的部分预览，2 表示直接失败。
管道遇上游非零即停止；`pcbeq` 请求的绘图失败也视为整轮失败，保留上一轮正式产物。
失败状态和本轮日志明确显示在管道报告中。

## 来源与复用

`pipeline/run.json` 记录子进程返回码、耗时、日志及交接 JSON 的 SHA-256；各产品运行报告记录实际精度、物理约定和进程内峰值内存。
批量复用检查输入来源、内容哈希、当前字段结构、实现指纹、光栅、网格、几何精度及图片配置和产物完整性。
缓存命中只决定是否重算，几何分支的输入始终是物理坐标与几何配置。

## 依据与实现对照

数值、图片和本轮状态共同发布，便于工程核对同一次计算。
实现位于两产品的 `application/publication/report` 与 `tools/run_pipeline.py`、`tools/sync_out.py`。
热图读取正式 MaterialMap，region 图使用实际规划矩形，网格图还原最终全局轴线。
图像语义见 [可视化](../visualization.md)，字段见 [接口](../interfaces/README.md)。
