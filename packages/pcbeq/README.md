# pcbeq

独立安装和运行的 PCB 物性等效器。板结构与几何输入仅为随包 intermediate.py 定义的中间 JSON，无需安装解析器，也不会读取来源路径中的 ODB++ 文件。

```powershell
pcbeq run intermediate.json -o result --grid-level standard
pcbeq run intermediate.json -o result --max-boundary-retreat-mm 0.2 --max-area-loss-ratio 0.01
pcbeq run intermediate.json -o result --px 4096 --py 4096 --max-cell-mm 2 3
```

光栅 Px/Py 表示整板各方向像素数，必须一起提供。默认采用密集光栅；网格档位与专家最大尺寸互斥。最小网格参数、下限约束及冲突诊断已删除，短的强制区间直接保留。region 和全局网格由物理几何决定，显式光栅不足时仍报错。

region 采用真实 Y 区间竖条阶梯划分，通过受约束的相邻区间合并减少几何块。

region 几何同时满足最大轮廓退缩量 `--max-boundary-retreat-mm`（有限正数，默认 3 mm）和最大面积损失比例 `--max-area-loss-ratio`（[0,1)，默认 0.05）。完整外轮廓与孔洞边界采用双向欧氏距离检查，不含内部矩形接缝。程序每次细化最严重超差位置，合并及共享层域后复检；零面积损失只适用于可精确矩形化的板型。运行报告和 region 图记录预算、实际距离上界及损失。

`--materials FILE` 可提供全局、材料层类型和逐层覆盖。缺省时警告并使用铜 k=385 W/(m·K)、密度 8960 kg/m³、比热 385 J/(kg·K)，FR4 k=0.3 W/(m·K)、密度 1850 kg/m³、比热 1100 J/(kg·K)；具体来源与实际采用值写入运行报告。钻孔按实心铜处理且只影响介质层。

输出 `materialmap.json`，每个 region 记录层身份、XYZ 边界与 XY 网格线，每个单元包含 kx/ky/kz/density/specific_heat。默认同时生成 masks、heatmaps、regions、grids、index.html 和 diagnostics。各图可通过 `--no-raster-png`、`--no-heatmap`、`--no-regions`、`--no-grids` 关闭；`--no-gallery` 关闭索引。

失败返回非零，保留上一轮已发布产物。成功返回 0。中间 JSON 模型随软件携带，不提供 ODB++ 或 MaterialMap 的替代业务输入路径。
