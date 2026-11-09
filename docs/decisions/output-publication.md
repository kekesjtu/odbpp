# 输出布局、发布与复用

## 正式产物

正式案例按 `out/<case>/<python|rust>/` 保存，`out/index.html` 同时索引两种版本。各版本包含以下目录。

| 目录 | 产物 |
| --- | --- |
| `pcbkit/` | `intermediate.json`、可选 `preview.svg` 和 `preview.html`、`diagnostics/run.json` 与 `diagnostics/artifacts.json` |
| `pcbeq/` | `materialmap.json`、所请求的[工程图表](pcbeq/pcbeq-visualization.md)、`diagnostics/run.json` 与 `diagnostics/artifacts.json`。Rust 的图片另由 `diagnostics/image-artifacts.json` 管理 |
| `pipeline/` | `run.json` 和两个 CLI 的 stdout、stderr 日志 |

## 发布与失败

解析器与 Python 等效器在同级暂存目录完成本轮输出，再发布正式产物。发布失败执行回滚。Python 等效器请求的绘图失败时，本轮失败，保留上一轮正式产物。

Rust 等效器先在原生事务中发布 MaterialMap 和数值诊断，同时清理登记的旧图片。随后 Python 从正式 MaterialMap 渲染并独立发布图片。图片失败返回非零，本轮已成功发布的数值结果仍可使用。各产物清单仅管理本软件登记的文件，保留用户自行放入的文件。

解析器返回 0 表示成功，1 表示已发布带显式层失败状态的部分结果，2 表示直接失败。管道遇到上游非零即停止，失败阶段和日志可从管道报告定位。

## 来源与复用

管道报告记录子进程状态、耗时、日志和已交付 JSON 的摘要。各产品的运行报告按本阶段记录诊断，Python 等效器和解析器还记录进程峰值内存，Rust 等效器记录原生阶段耗时和光栅像素规模。

批量工具根据输入来源、实现、配置和登记产物判断是否复用。缓存命中只决定是否重算。调用方法见[维护工具](../../tools/README.md)。
