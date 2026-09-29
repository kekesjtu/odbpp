# 02 · 等效结果的可视化（材料掩膜 PNG 与 kx/ky/kz 热图）

数据流第 5 段之后，为工程师增加的两样**只给人看**的产物：

```text
[ 4. 等效器 ] → [ 5. MaterialMap.csv + meta.json ]   ← 数值唯一依据
                        │
                        ├──→ masks/<layer>.png      逐层黑白光栅（等效公式实际积分的那张图）
                        └──→ heatmaps/<layer>.png   逐层 kx|ky|kz 三面板热图
                                    ＋ <step>.images.html 图例页
```

图像**不是**结论，也不是新的数据源：每个数值仍以 CSV 与 meta 为准。
AGENTS 验证要求第 3 条明确「不得仅凭显示效果判断等效值正确」，因此掩膜与热图
的用途是**核对几何与场的形态**（图层、走线、过孔、域覆盖、梯度走向），
收敛性判断仍要靠分辨率扫描与逐层均值。

## 1. 命令

```bash
# 一步到位：解析 + 等效 + 掩膜 + 热图 + 图例页
uv run --all-extras python -m odbpp equivalent cases/kicad-kitchen-sink \
    --px 400 --py 400 -o build/ks-eq

# 只要 CSV：关掉全部图像
uv run --all-extras python -m odbpp equivalent --from-json build/ks/PCB.json \
    --px 400 --py 400 -o build/ks-eq --no-raster-png --no-heatmap --no-gallery

# 只从已写出的 CSV 重画热图（不需要 ODB++ 输入，也不需要重算等效）
uv run --all-extras python -m odbpp heatmap build/ks-eq/PCB.MaterialMap.csv \
    -o build/ks-eq/heatmaps --panel-width 900 --shared-scale
```

| 选项 | 作用 |
|---|---|
| `--no-raster-png` | 不写 `<out>/masks/*.png` |
| `--raster-scale N` | 掩膜最近邻放大倍数；`0`（默认）自动取约 700 px 长边，`1` 即恰好 `px × py` |
| `--no-heatmap` | 不写 `<out>/heatmaps/*.png` |
| `--heatmap-panel-width PX` | 单面板宽度（默认 720；高度按板比例） |
| `--heatmap-shared-scale` | 所有层共用一条色标（层间可比，单层对比度下降） |
| `--no-gallery` | 不写图例 HTML |
| `heatmap --layer NAME` | 只画指定层（可重复） |

产物布局：

```text
<out>/
  PCB.MaterialMap.csv        数值唯一依据
  PCB.MaterialMap.meta.json  来源、Z 可信状态、矩形化误差、逐层报告
  PCB.images.html            图例页：层名/类型/绝对 Z/k 范围/单元数 + 两类图
  masks/<layer>.png
  heatmaps/<layer>.png
```

`odbpp heatmap <csv>` 是同一套渲染函数的另一种入口，输出 `heatmaps/*.png` 与
`heatmaps/index.html`；`equivalent` 内部也是**重新读取刚写出的 CSV**再渲染，
所以图像与交付数据不可能分叉。

## 2. 材料掩膜（黑白 PNG）

`masks/<layer>.png` 就是该层参与等效公式的那张光栅，未经平滑、未经缩放插值。
每个像素一个字节，三种色调：

| 值 | 色调 | 含义 |
|---|---|---|
| 0 | 黑 | 铜 / 钻孔投影（材料 1，`black`） |
| 255 | 白 | 计算域内的基底（材料 2，`white`） |
| 160 | 灰 | 该层在此**没有计算域**：板外或空洞 |

灰不是第三种材料。AGENTS 约束 7 禁止把域外当作 FR4，等效器也从不这样算；
灰色只是把「这一层在这里不存在」画出来，避免「整张图都是材料」的错觉。
与真实板的对照中，灰色区域正是外形之外的区域以及钻孔/挖空（该层 profile 的孔）。

- 分辨率：`px × py`（默认最近邻放大到长边约 700 px，见 `--raster-scale`），
  因此可以直接与 CSV 的 `pixel_rows/pixel_cols/pixel_count` 对照。
- 图像上方为板 Y 最大处（与热图一致）；板 Y 向下为图像下方。
- 掩膜在**该层算完时立刻写出**（`mask_sink`），处理下一层前即释放，
  所以整板 43 层也只有一张掩膜常驻内存。

## 3. 等效热导率热图（kx、ky、kz 一张图，一层一张）

`heatmaps/<layer>.png` 把三个方向并排画在同一张图上：左 `kx`、中 `ky`、右 `kz`，
三块面板**共用一条色标**，因此可以直读「哪个方向更连通」。

- **颜色**：本项目自研的蓝→青→绿→黄→红色阶（分段线性、锚点写在
  `visualize._COLOUR_STOPS`），量化到 255 级；不用任何外部色图库。
- **色标**：面板下方为渐变条，左端/右端数字是该层本次的 `min`/`max`
  （`--shared-scale` 时为全层 `min`/`max`）。平面内所有方向、所有单元同标。
- **`n/a`（浅灰）**：该层在此网格单元**没有值**——该层计算域不覆盖它，
  或这里根本没有板内单元。绝不把它当成低热导率。
- **面板范围**：等于该 CSV 中出现的单元范围（所有层网格线的并集）。
  完全没有任何层覆盖的位置不会出现在 CSV 里，也就不会出现在图上；
  此时掩膜 PNG 才是判断域覆盖的依据。图例页会同时给出 meta 的整板
  `nx × ny` 与光栅尺寸。
- **画法**：每个网格单元的像素宽度与其物理宽度成正比（一个统一的
  mm→px 比例，保持长宽比），单元边界取真实网格线坐标；未承载任何数值的
  区间按真实宽度绘制（不为了可见性把它撑成一条假的空白带）。
  窄到不足一个像素但**有值**的单元会至少占 1 像素，以免丢格。
- 图头：`Lk/n`（第 k 层/共 n 层）、绝对 Z 区间（`?` 表示 `thickness_known=false`）、
  面板标题与单位 `W/(m.K)`。字体是内置 5×7 点阵（`png._GLYPHS`），
  只覆盖标签里实际出现的字符；层名等任意文本放在文件名与图例页里。
- **不在图上画的东西**：密度与比热（第一版只要求热导率）、坐标刻度。
  需要更多场时，扩展 `visualize.PANELS` 即可。

## 4. 实现与依赖

- `src/odbpp/png.py`：手写 PNG 编码器（标准库 `zlib`/`struct`）＋ RGB 画布
  ＋ 5×7 点阵字体。只支持 8 位灰度与 8 位 RGB、filter 0、无隔行；
  不支持的一律报错。这样渲染不引入 Pillow 之类的大型二进制依赖。
- `src/odbpp/raster.py::LayerMask`：逐层黑白缓冲，按扫描线区间用切片写入
  （C 速度），`top_down_rows(scale)` 负责翻转为图像行序并做最近邻放大。
- `src/odbpp/visualize.py`：掩膜写盘、CSV 读取（严格的栅格一致性校验）、
  色阶与热图渲染、图例 HTML。
- 内存：掩膜一层一驻留；热图按层汇总 CSV 行（约 48 B/单元），
  必须完整读一遍 CSV。1.45M 单元 / 19 层的刚挠板实测渲染耗时约 20 s、
  峰值内存可控；更大的板建议降低 `px/py` 后再看形态。

## 5. 失败与诊断

- 等效致命（矩形化不通过、`grid.cell_without_pixel`、钻孔跨度未知）时
  **不写任何 CSV，也不写任何 PNG**：掩膜先写成 `<name>.png.tmp`，
  整轮成功后统一 `rename`，因此不会留下半张图。
- 图像渲染失败（磁盘、权限、CSV 结构不符）**不影响交付**：CSV 与 meta 已经写出，
  CLI 记 `visualize.failed` 警告并继续返回。
- `odbpp heatmap` 对不是本项目的 CSV、缺列、栅格不自洽（同一 `cell_i` 出现
  两个 `cell_x0_mm`）一律拒绝并给出可定位信息，不猜、不半画。
- 无法解析的数值（空、`NaN`、非正数）在热图里**留空并计数**，
  CLI 以一行 warning 报出，不会画成一个假的值。

## 6. 验证（`tests/test_visualize.py`）

| 验证项 | 断言 |
|---|---|
| PNG 编码器 | 灰度/RGB 行往返一致；行长度/行数/尺寸非法即报错；只写 filter 0 |
| 画布与字体 | 矩形、边框、行拷贝、文字落墨；未收录字符画为「豆腐块」而非消失 |
| 掩膜三色 | 全铜层无灰；域只覆盖左半时右半为灰；只有被等效的层才有掩膜 |
| 掩膜尺寸 | `scale=2` 时 PNG 为 `2px × 2py`，上白下黑（Y 轴向上） |
| CSV 读取 | 网格线/层序/行数还原；缺列、非数字、栅格不自洽均拒绝；坏值计数留空 |
| 热图 | 一层一张、三面板由**帧线定位**（不重复实现布局算式）；色标端点等于色阶两端 |
| 热图方向 | 铜在上半 → 图像上半为色标高端（与掩膜同向） |
| 共享色标 | 单层尺度下全铜层居中；共享尺度下全铜层等于高端 |
| 覆盖 | 另一层覆盖、本层不覆盖的列画成 `n/a` |
| 文件名 | `A/B` 与 `A:B` 不会互相覆盖 |
| CLI | `equivalent` 生成 masks/heatmaps/gallery；关图选项不生成；致命轮次不留 PNG/临时文件；`heatmap` 只读 CSV；缺文件退出码 2 |

## 7. 已知限制

1. 热图只能画 CSV 里存在的单元；完全无层覆盖的区域不出现在图上（掩膜 PNG 兜底）。
2. 色标是线性、逐层自适应的；跨层比较需显式 `--shared-scale`。
3. 只有 `kx/ky/kz`；密度、比热尚未出图。
4. 没有坐标刻度、图例只有端点值；精确定位请配合 CSV 与 SVG 预览。
5. 图例页与热图 `n/a` 的语义依赖灰度/配色区分，打印成黑白会退化——
   因此 `n/a` 同时写在图例页与文档里，而不是只靠颜色。

## 8. 与本轮矩形化修复的关系

本功能一上线就在真实 case 上暴露了一个既有的矩形化缺陷：先吸附再配对会让
亚容差狭缝把整条竖直 slab 丢掉，邻块又被拉伸覆盖过去（见
[`../fixes/06-rectify-slab-loss.md`](../fixes/06-rectify-slab-loss.md)）。
修复后热图上不再出现虚假的浅灰竖带，`lost`/`gained` 面积也回到对称的阶梯量级。
