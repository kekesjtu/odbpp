# 输出布局、发布与复用

## 正式产物

正式案例保存在 `out/<case>/rust/`，由 `out/index.html` 索引。

| 目录 | 产物 |
| --- | --- |
| `pcbkit/` | `intermediate.json`、可选 `preview.svg` 和 `preview.html`、可选 `layers/*.png`、`diagnostics/run.json` 与 `diagnostics/artifacts.json` |
| `pcbeq/` | `materialmap.json`、唯一的 `diagnostics.json` 和所请求的[工程图表](pcbeq/pcbeq-visualization.md)，不输出产物清单 |
| `pipeline/` | `run.json` 和两个 CLI 的 stdout、stderr 日志 |

## 发布与失败

解析器先在同级暂存目录完成数据和预览，再发布正式产物。等效器先在原生事务中发布 MaterialMap 和数值诊断，同时清理专用目录中的旧图片。随后 Python 从正式 MaterialMap 渲染并独立发布图片。

每次发布失败时回滚本次事务。等效器绘图失败返回非零，本轮已成功发布的数值结果仍可使用。解析器按其产物清单管理文件。等效器只管理固定文件 `materialmap.json`、`diagnostics.json`、`index.html` 和专用图片目录 `regions/`、`grids/`、`heatmaps/`，不使用清单。图片发布整体替换这些专用图片目录及图库入口，未请求的图片类别不保留；这些目录以外的用户文件保留。

等效器的厚度或累计 Z 不可信属于非阻断错误，正常发布物性与未知 Z，返回 0，后续绘图继续。阻断失败时不发布本轮 MaterialMap，尽力以本轮失败诊断覆盖 `diagnostics.json`；目录中仍可能保留上一轮成功的 MaterialMap。调用者依据退出码和诊断状态判断本轮是否有可用结果，不能仅依据文件存在。诊断写入失败同时通过 stderr 报告。

解析器返回 0 表示成功，2 表示失败。中间 JSON 描述一块完整的板：任一 BOARD 层无法解析或合成时终止本轮，不发布中间 JSON 与预览，只发布 `diagnostics/run.json`（`status=failed`，含可定位的错误）。因此产物存在即代表本轮完整解析通过，`polygons: []` 只表示该层图案为空。管道遇到上游非零即停止，失败阶段和日志可从管道报告定位。

## 来源与复用

管道报告记录子进程状态、耗时、日志和已交付 JSON 的摘要。解析器报告记录输出精度和进程峰值内存。等效器报告记录各有效 profile 的几何与网格统计、面积与拓扑验收、默认材料与未知 Z 问题，以及原生阶段耗时。

批量工具根据输入来源、实现、配置和预期输出判断是否复用。缓存命中只决定是否重算。调用方法见[维护工具](../../tools/README.md)。
