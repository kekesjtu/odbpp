# MaterialMap：region 局部均匀网格 JSON

正式结果为 `pcbeq/materialmap.json`。每个三维 region 保存层身份、XYZ 最小/最大边界及 XYZ 网格数，单元仅保存五项物性。范围与网格数唯一确定全部均匀单元，无需网格线数组或逐单元坐标。

各 region 独立划分，允许相邻 region 不共形。Parser 中间 JSON 仍是两产品之间唯一的板数据接口；本文件是等效器输出。

## 结构示例

```json
{
  "units": {
    "length": "mm", "thermal_conductivity": "W/(m*K)",
    "density": "kg/m3", "specific_heat": "J/(kg*K)"
  },
  "properties": ["kx", "ky", "kz", "density", "specific_heat"],
  "drill_model": "solid_copper",
  "drill_scope": "dielectric_only",
  "step": "pcb",
  "regions": [{
    "layer": {"name": "C0", "type": "SIGNAL"},
    "bounds_mm": {"x": [0, 3], "y": [0, 5], "z": [0, 0.035]},
    "grid_counts": {"x": 2, "y": 2, "z": 1},
    "values": [
      [385, 385, 385, 8960, 385],
      [0.3, 0.3, 0.3, 1850, 1100],
      [385, 385, 385, 8960, 385],
      [0.3, 0.3, 0.3, 1850, 1100]
    ]
  }]
}
```

该 region 定义 2 × 2 × 1 个均匀单元，X/Y 单元边长为 1.5 / 2.5 mm；物性仅作结构示例。

## 范围与网格数

`bounds_mm.x/y/z` 各有两个值，表示最小/最大坐标。XY 必须有限且有正长度，Z 为自叠层顶面向下的绝对 mm。
`grid_counts.x/y/z` 必须为正整数，不接受布尔、字符串或小数。分格数的选择见[几何规划](../decisions/pcbeq/geometry-planning.md#局部均匀网格)。

每个条目完整属于一个物理层；当前每层不沿 Z 切分，`grid_counts.z=1`。同一 XY region 出现在多个物理层时，各层分别记录自己的三维 region。
region 必须完整位于所属层的有效计算域内，不能跨轮廓定义的孔洞。本文件表达完整单元，采样覆盖率不折减体积或质量。

### 未知 Z

未知 Z 写为 `[null,null]`，不能用 0 代替未知值。内部厚度/累计 Z 不可信时，两个端点均置 null；中间 JSON 与独立诊断保留 best-effort Z 和来源。
Z 未知时仍能恢复 XY 网格并绘制热图，Z 分格数仍为 1；三维坐标、体积和质量须先补齐 Z，不能把 null 当零厚度。

## 坐标恢复与物性顺序

各方向第 i 条边界由 `low+(high-low)*i/n` 恢复，首尾保留原始 bounds。按 Z 外层、Y 次层、X 内层排列，X 变化最快：

```python
nx, ny, nz = (region["grid_counts"][axis] for axis in "xyz")
assert len(region["values"]) == nx * ny * nz
for k in range(nz):
    for j in range(ny):
        for i in range(nx):
            values = region["values"][(k * ny + j) * nx + i]
```

PCB 坐标 Y 向上，从左下角向右遍历一行再向上。PNG 上到下的存储不改变数据顺序。
每行固定为 `kx,ky,kz,density,specific_heat`，五项必须有限且正。缺行、多行、重复单元、非均匀内部划分均不得静默导出。

正式 JSON 不保存网格线数组、单元索引/坐标、面积、铜占比或采样统计。物性发布精度为热导率六位小数、密度和比热四位；极小正值若会舍入为零，则保留原值。region 边界保留实际几何精度。

## 参考与工程核对

[Flotherm CSV 参考](../reference/Flotherm_reference_MaterialMap.csv) 的 region 范围与 resolution 展示了均匀分格的紧凑表达。本项目采用自定义 XYZ 网格数与五物性元组，不要求该 JSON 可被 Flotherm 直接导入。

图表的输入与用途见[可视化](../decisions/pcbeq/pcbeq-visualization.md)。材料来源、几何验收和采样配置由独立的 `diagnostics/run.json` 记录。格式采用唯一的当前结构。
