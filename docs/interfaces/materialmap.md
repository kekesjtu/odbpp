# MaterialMap：单个 region 层级 JSON

正式等效结果为 `pcbeq/materialmap.json`。每个三维 region 保存一次层身份、六个边界值和 XY 网格线，单元只保存五项物性。运行诊断单独写入 `diagnostics/run.json`。

几何规划、全局笛卡尔栅和材料采样共同生成完整单元物性。Parser 的中间 JSON 仍是解析器与等效器之间的独立接口；本文件是等效器的输出。

## 完整结构示例

```json
{
  "units": {
    "length": "mm",
    "thermal_conductivity": "W/(m*K)",
    "density": "kg/m3",
    "specific_heat": "J/(kg*K)"
  },
  "properties": ["kx", "ky", "kz", "density", "specific_heat"],
  "drill_model": "solid_copper",
  "drill_scope": "dielectric_only",
  "step": "pcb",
  "regions": [
    {
      "layer": {"name": "C0", "type": "SIGNAL"},
      "bounds_mm": {
        "x": [0, 3],
        "y": [0, 5],
        "z": [null, null]
      },
      "x_lines_mm": [0, 1, 3],
      "y_lines_mm": [0, 2, 5],
      "values": [
        [385, 385, 385, 8960, 385],
        [0.3, 0.3, 0.3, 1850, 1100],
        [385, 385, 385, 8960, 385],
        [0.3, 0.3, 0.3, 1850, 1100]
      ]
    }
  ]
}
```

这里定义 2 × 2 个非均匀 XY 单元，Z 暂不可确定。物性值仅作结构示例，不代表一个真实案例的计算结果。

## region 与六个边界值

`bounds_mm.x/y/z` 各有两个值，分别是最小和最大坐标，总共六个边界值。XY 必须有限且有正长度，Z 为从叠层顶面向下的绝对 mm 坐标。

每个条目是某一物理层中的一个三维建模块。同一 XY 矩形出现在多个层时，各层分别记录自己的 region；通过层身份和坐标区分，不在文件中保存二维 region 编号或网格单元索引。

region 必须完整位于所属层计算域及 step profile 内，不能跨越轮廓孔洞。文件不表达部分覆盖单元，也不保存用来折减体积或质量的覆盖率。物性应用于完整单元体积。

### 未知 Z

确定的 Z 使用数值，`0` 是合法且已知的坐标。未知使用 `null`，不能用 0、负值、NaN 或字符串代替。

当前内部 `thickness_known` 是本层与累计绝对 Z 的联合可信标记，没有独立端点可信标记。因此，当它为 false 时，导出器保守地把该 region 的两个 Z 都写为 null；即便内部保留 best-effort 数字，也不将其写成确定边界。

原始中间 JSON 与独立运行诊断仍保留 best-effort Z 和来源。热图只使用 XY，可以在 Z 未知时正常绘制；下游三维建模、体积和质量计算必须先补齐未知 Z，不能把 null 当成零厚度。

## 网格线与物性顺序

每个 region 的 X/Y 网格线包含其两侧边界，必须严格递增；首尾值分别等于对应 bounds 的最小/最大值。保留边界和网格线这两种表达，是为了直接读取建模块范围，同时精确恢复内部非均匀单元；读取时校验它们一致。

网格线截取自实际使用的全局笛卡尔栅，不能在序列化时对每个 region 重新等分。其他 region 的边界引入的全局轴线，仍须在穿过本 region 时完整保留。

```python
nx = len(region["x_lines_mm"]) - 1
ny = len(region["y_lines_mm"]) - 1
assert len(region["values"]) == nx * ny

for j in range(ny):                 # Y 由小到大
    for i in range(nx):             # X 由小到大，变化最快
        values = region["values"][j * nx + i]
        # 单元边界由 x[i], x[i+1], y[j], y[j+1] 唯一还原。
```

在 Y 向上的 PCB 坐标系中，从左下角开始，向右走完一行再向上进入下一行。图像 PNG 的上到下存储顺序不改变这个文件约定。每个物性条目的顺序固定为 `kx, ky, kz, density, specific_heat`，五项均须为有限正数。

无需导出 nx/ny、cell_i/cell_j、单元坐标、单元面积、铜面积比例、像素行列数 / 数量或覆盖率。nx/ny、单元边界、面积和已知 Z 下的体积均可从 region 几何推导。

物性发布精度：热导率通常保留六位小数，密度与比热保留四位。极小正值若按该精度会变成 0，则保留原正数，以保证输出有效。XY 网格线和确定 Z 保留内部实际坐标，不以重复单元边界的舍入结果反推网格。

## 热图和诊断

热图直接读取正式 MaterialMap JSON，按网格线重建每个单元的矩形，不依赖 ODB++ 输入或运行报告。

热图在正式 JSON 写出后生成。
图片语义见 [pcbeq 可视化](../decisions/pcbeq/pcbeq-visualization.md)。

`diagnostics/run.json` 是独立运行诊断，记录必要的材料来源、默认材料警告、光栅配置、网格尺寸策略、几何损失、逐层跳过原因和实现指纹。它不是 MaterialMap 的另一部分，热图读取不需要它；其中也不写逐单元索引、面积和采样统计。region / 全局网格诊断图继续单独生成。

几何与材料验收工具从 JSON 网格线重建单元，再独立核对原始域归属、尺寸约束、实际光栅采样中心容量、Z 与物性范围。计算期间仍须检查采样充足性，不能因为正式文件删去统计而取消内部校验。
