> 当前正式输出已改为单个 region 层级 JSON，字段、行主序和未知 Z 规则见[MaterialMap 当前格式](09-region-materialmap.md)。下文的算法及历史测量继续有效；旧 MaterialMap JSON 字段/旁车描述已由该格式替代。

# 02 · 等效结果的可视化（材料掩膜 PNG 与 kx/ky/kz 热图）

数据流第 5 段之后，为工程师增加的两样**只给人看**的产物：

```text
[ 4. 等效器 ] → [ 5. MaterialMap.json ]   ← 数值唯一依据
                        │
                        ├──→ masks/<layer>.png      逐层黑白光栅（等效公式实际积分的那张图）
                        └──→ heatmaps/<layer>.png   逐层 kx|ky|kz 三面板热图
                                    ＋ <step>.images.html 图例页
```

图像**不是**结论，也不是新的数据源：每个数值仍以 MaterialMap JSON 与独立运行诊断 为准。
AGENTS 验证要求第 3 条明确「不得仅凭显示效果判断等效值正确」，因此掩膜与热图
的用途是**核对几何与场的形态**（图层、走线、过孔、域覆盖、梯度走向），
收敛性判断仍要靠分辨率扫描与逐层均值。

## 1. 命令

```bash
# 一步到位：解析 + 等效 + 掩膜 + 热图 + 图例页
uv run --all-extras python -m odbpp equivalent cases/kicad-kitchen-sink \
    --grid-level standard -o build/ks-eq

# 只要 MaterialMap JSON：关掉全部图像
uv run --all-extras python -m odbpp equivalent --from-json build/ks/PCB.json \
    --grid-level standard -o build/ks-eq --no-raster-png --no-heatmap --no-gallery

# 只从已写出的 MaterialMap JSON 重画热图（不需要 ODB++ 输入，也不需要重算等效）
uv run --all-extras python -m odbpp heatmap build/ks-eq/PCB.MaterialMap.json \
    -o build/ks-eq/heatmaps --panel-width 900 --shared-scale
```

| 选项 | 作用 |
|---|---|
| `--no-raster-png` | 不写 `<out>/masks/*.png` |
| `--raster-scale N` | 强制**各向同性**最近邻放大 N 倍（`1` 即恰好 `px × py`）；日常不要用，默认的自动模式会保持长宽比 |
| `--no-heatmap` | 不写 `<out>/heatmaps/*.png` |
| `--heatmap-panel-width PX` | 单面板宽度（默认 1440；高度按板比例） |
| `--heatmap-shared-scale` | 所有层共用一条色标（层间可比，单层对比度下降） |
| `--heatmap-grid` / `heatmap --grid` | 按 MaterialMap JSON 坐标画出存在的单元边界；没有该层的区域不画网格 |
| `--no-gallery` | 不写图例 HTML |
| `heatmap --layer NAME` | 只画指定层（可重复） |

产物布局：

```text
<out>/
  PCB.MaterialMap.json        数值唯一依据
  diagnostics/run.json  来源、Z 可信状态、矩形化误差、逐层报告
  PCB.images.html            图例页：层名/类型/绝对 Z/k 范围/单元数 + 两类图
  masks/<layer>.png
  heatmaps/<layer>.png
```

文件名由原层名转成文件系统安全的名称。若多个层名转成同一名称，按层序加
`-2`、`-3` 等后缀；后缀本身与其他原层名冲突时继续寻找空闲名称，比较时忽略
大小写。图例页仍显示原层名，并分别链接到正确的掩膜和热图。

`odbpp heatmap <csv>` 是同一套渲染函数的另一种入口，输出 `heatmaps/*.png` 与
`heatmaps/index.html`；`equivalent` 内部也是**重新读取刚写出的 MaterialMap JSON**再渲染，
所以图像与交付数据不可能分叉。

## 2. 材料掩膜（黑白 PNG）

`masks/<layer>.png` 就是该层参与等效公式的那张光栅，未经平滑、未经插值。
每个像素一个字节，三种色调：

| 值 | 色调 | 含义 |
|---|---|---|
| 0 | 黑 | 铜图案 / 仅介质内钻孔投影（材料 1，`black`） |
| 255 | 白 | 计算域内的基底（材料 2，`white`） |
| 160 | 灰 | 该层在此**没有计算域**：板外或空洞 |

灰不是第三种材料。AGENTS 约束 7 禁止把域外当作 FR4，等效器也从不这样算；
灰色只是把「这一层在这里不存在」画出来，避免「整张图都是材料」的错觉。
与真实板的对照中，灰色区域正是外形之外的区域以及钻孔/挖空（该层 profile 的孔）。

- **长宽比就是板的长宽比**。光栅沿 X、Y 各自按 `px`、`py` 计数，两轴物理间距
  往往不同（例：163 × 43 mm 的刚挠板在 400 × 400 光栅下，X 每像素 0.41 mm、
  Y 每像素 0.11 mm）。两轴按同一整数放大就会把这块板画成正方形，因此自动模式
  分别计算两个轴的缩放：先取「长边约 1400 px」（`MASK_TARGET_PX`），再保证
  **不会把两个光栅像素抽到一个输出像素上**（即两轴各至少 1:1），二者取大的。
  上例得到 1509 × 400 px，既比例正确又不丢细走线/小孔。
  输出超过 `MASK_MAX_PIXELS`（4 M 像素）时才会按比例压缩，此时细特征可能被抽样掉。
- `write_layer_mask()` 返回 `MaskImage(width, height, zoom_x, zoom_y)`：因为两个轴
  的缩放不同，它不再是一个整数。`--raster-scale N` 仍可强制各向同性 N 倍
  （轴会变形，仅作应急）。
- 图像上方为板 Y 最大处（与热图一致）；板 Y 向下为图像下方。
- 掩膜在**该层算完时立刻写出**（`mask_sink(name, mask, pixel_step_mm)`），
  处理下一层前即释放，所以整板 43 层也只有一张掩膜常驻内存。

## 3. 等效热导率热图（kx、ky、kz 一张图，一层一张）

`heatmaps/<layer>.png` 把三个方向并排画在同一张图上：左 `kx`、中 `ky`、右 `kz`，
三块面板**共用一条色标**，因此可以直读「哪个方向更连通」。

每个物理层只输出一张 PNG，三个方向始终横向并排。图例页直接展示该图，
窄屏通过横向滚动保留最低阅读宽度，点击可打开原图；材料掩膜可以折叠展开。
不再生成独立方向图。

- **绘图引擎**：Matplotlib Agg。每个实际 MaterialMap JSON 单元以毫米矩形加入
  `PolyCollection`，三个方向分别读取对应物性列，不再使用手工像素填色或点阵字体。
- **布局**：独立的层名标题、Z/类型/单元数信息行、三个子图区域和一条底部公共色条。
  布局以物理图幅定义，`--heatmap-panel-width` 只控制输出分辨率。
  标题较长时自动换行；改变图幅分辨率不会改变布局比例或单元物性。
- **颜色**：使用 `viridis` 连续色阶，三个方向共用相同线性归一化。
  默认范围为本层三个方向的联合最小/最大值；`--shared-scale` 使用所选层的联合范围。
  公共色条给出五个刻度和 `W/(m·K)` 单位。均匀层显示单色条、唯一数值及 uniform 标记。
- **无单元**：浅灰表示该层没有输出单元，图内有独立图例；灰色不会被解释为低热导率。
- **坐标与比例**：显示毫米坐标和 X/Y 标签，所有面板取相同 MaterialMap JSON 范围、相同等比例轴。
  完全无任何层单元的位置不会出现在 MaterialMap JSON 范围里，整板范围仍以 JSON/运行诊断和掩膜为准。
- **网格**：`--heatmap-grid` 按存在的 MaterialMap JSON 单元绘制细边界；不存在该层的位置不画格线。
  绘图不会聚合、插值或平滑物性，输出分辨率较低时微小单元仍可能看不清。
- **Z 信息**：绝对 Z 起止值来自 region bounds，null 表示未知；热图不把 null 解释成零厚度。
  层名直接画入图头。系统存在中文字体时优先使用 Microsoft YaHei 或 Noto Sans CJK SC。

## 4. 实现与依赖

- `src/odbpp/png.py` 负责标准库 PNG 编码；掩膜输出保持既有最近邻逻辑。
- `src/odbpp/visualize.py` 负责 JSON 读取、Matplotlib Figure/Agg 绘图及图例 HTML。
  旧色阶、像素网格映射、Canvas heatmap 绘制和独立方向图逻辑已删除。
- Matplotlib 为运行时依赖，因为默认 equivalent/heatmap 命令会使用它；
  `uv sync --all-extras` 同步锁文件和开发依赖。
- 一次只绘制一个层的 Figure，再释放。当前重绘记录见
  [Heatmap 阅读布局](../fixes/12-heatmap-layout.md)。旧版性能数字不代表新绘图引擎。

## 5. 失败与诊断

- 等效致命（矩形化不通过、`grid.cell_without_pixel`、钻孔跨度未知）时
  **不写任何 MaterialMap JSON，也不写任何 PNG**：掩膜先写成 `<name>.png.tmp`，
  整轮成功后统一 `rename`，因此不会留下半张图。
- 图像渲染失败（磁盘、权限、MaterialMap JSON 结构不符）**不影响交付**：MaterialMap JSON 与独立运行诊断 已经写出，
  CLI 记 `visualize.failed` 警告并继续返回。
- odbpp heatmap 校验 region、XY 网格线和固定五项物性数组，按行主序还原实际毫米矩形。
  全部 region 必须遵守共同的全局笛卡尔栅；同层 region 不能重叠。
- 无法解析、非有限或非正的物性，以及缺少 / 多余条目均直接拒绝。
  不跳过错误行，防止后续物性错位。

## 6. 验证（`tests/test_visualize.py`）

| 验证项 | 断言 |
|---|---|
| PNG 编码器 | 灰度/RGB 行往返一致；行长度/行数/尺寸非法即报错；只写 filter 0 |
| 掩膜三色 | 全铜层无灰；域只覆盖左半时右半为灰；只有被等效的层才有掩膜 |
| 掩膜尺寸 | `scale=2` 时 PNG 为 `2px × 2py` 且返回 `isotropic_zoom=2`；上白下黑（Y 轴向上） |
| 掩膜长宽比 | 非方形板（10 × 5 mm，两轴间距不同）得到 `700 × 350`：两轴每毫米像素数相等，两轴 zoom 之比等于间距之比 |
| 掩膜不欠采样 | 163 × 43 mm、400 × 400 光栅的两个轴各至少 1:1（1509 × 400），比例仍等于板 |
| 两轴缩放 | `resampled_rows` 顶行在前、两轴独立放大；尺寸为 0 报错 |
| 间距缺失时 | 只有 `auto_mask_scale`（各向同性整数倍）可用，不假装知道比例 |
| JSON 读取 | 局部矩形/层序/行数还原；缺字段、非数字均拒绝；错误物性、缺少或多余条目均拒绝，不跳过 |
| 热图 | 一层一张、三面板由绘图对象的实际包围盒定位，布局无重叠，公共色条对应物性范围 |
| 热图方向 | 铜在上半 → 图像上半为色标高端（与掩膜同向） |
| 共享色标 | 单层尺度下全铜层居中；共享尺度下全铜层等于高端 |
| 覆盖 | 另一层覆盖、本层不覆盖的列画成 `n/a` |
| 文件名 | `A/B`、`A:B`、`A_B-2` 与只差大小写的层名均不会互相覆盖；掩膜、热图及图例链接一致 |
| CLI | `equivalent` 生成 masks/heatmaps/gallery，默认材料警告可在 stderr 看到；关图选项不生成；致命轮次不留 PNG/临时文件；`heatmap` 只读 MaterialMap JSON；缺文件退出码 2 |

## 7. 已知限制

1. 热图只能画 MaterialMap JSON 里存在的单元；完全无层覆盖的区域不出现在图上（掩膜 PNG 兜底）。
2. 色标是线性、逐层自适应的；跨层比较需显式 `--shared-scale`。
   实测上铜（385）与 FR4（0.3）相隔三个量级，线性色标会把中间段全部压暗，
   因此**层内**看图案用默认逐层尺度，**跨层**看谁导电用 `--shared-scale`，
   两者都不能代替 MaterialMap JSON 里的数字。
3. 只有 `kx/ky/kz`；密度、比热尚未出图。
4. 坐标刻度用于定位；精确数值与单元边界仍应配合 MaterialMap JSON 与 SVG 预览核对。
5. 图例页与热图 `n/a` 的语义依赖灰度/配色区分，打印成黑白会退化——
   因此 `n/a` 同时写在图例页与文档里，而不是只靠颜色。
6. 掩膜/热图的分辨率与网格数、光栅数无关（掩膜默认长边约 1400 px，
   热图面板默认宽 1440 px）；要逐格核对时用 `--raster-scale` /
   `--heatmap-panel-width` 把它们放到与网格同尺度。

## 8. 与本轮矩形化修复的关系

本功能一上线就在真实 case 上暴露了一个既有的矩形化缺陷：先吸附再配对会让
亚容差狭缝把整条竖直 slab 丢掉，邻块又被拉伸覆盖过去（见
[`../fixes/06-rectify-slab-loss.md`](../fixes/06-rectify-slab-loss.md)）。
修复后热图上不再出现虚假的浅灰竖带，`lost`/`gained` 面积也回到对称的阶梯量级。

同一轮对照还发现两类图本身的缺陷，已一并修正（记录见
[`../fixes/07-picture-aspect.md`](../fixes/07-picture-aspect.md)）：

1. **掩膜被拉成正方形**：两轴共用同一个整数放大倍数，而光栅两轴的物理间距不同。
   现在自动模式两轴分别定标，长宽比等于板的长宽比。
2. **热图被竖向拉伸**：为了「不让窄单元消失」而给每个有数据的行至少 1 px，
   在网格行数多于面板高度像素数时（如 400 行 → 190 px 的面板）整体变高。
   现在按累积取整绘制，面板高宽比 = 板的高宽比，与掩膜一致。
