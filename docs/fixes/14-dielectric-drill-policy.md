# 14 · 中间介质几何与 DRILL 作用域

用户明确要求：中间 JSON 保留未扣除 DRILL 投影的介质几何，铜层完全不受 DRILL 影响。
本次同时修改解析、预览、材料采样和当前接口说明；不引入业务版本协商或兼容迁移。

## 当前数据职责

| 阶段 | 铜层 | 介质层 |
|---|---|---|
| Parser / JSON | 本层正向铜图案，保留自身避空区 | 本层图元及 profile 基底规整后几何，未扣跨层 DRILL |
| SVG / HTML | 原铜图案 | 局部绘图副本扣除相交 DRILL 投影，原模型不变 |
| 等效材料采样 | `域 AND 铜图案` 为黑色 | `域 AND 相交 DRILL` 为黑色，其余域内为基底 |
| region / 网格 | 从 step profile 和逐层计算域规划 | 同左；DRILL 不改变实体域和网格 |

`域` 同时受整板及逐层计算域限制。profile 自身的真实孔洞/挖空始终保留，
即使有 DRILL 覆盖，也不能填成材料或生成 region。铜层已有避空区即使与 DRILL 重叠，
仍按铜图案采样为白色基底；用户本次规则不将其当空气。

等效器继续采用实心铜钻孔近似，但只用于介质。meta 同时记录
`drill_model=solid_copper` 和 `drill_scope=dielectric_only`；所有铜层 `drill_overlays` 为空。
通孔、盲孔、埋孔按原 `copper_span` 半开相交规则选择介质，绝对 Z 和厚度来源不变。
介质采样需要的 DRILL 跨度或几何缺失时仍报错；只有铜层的输入不需要 DRILL 跨度。
结构和引用校验仍使用同一个 Pydantic 模型，不因材料层忽略投影而跳过结构检查。

## 实现

- `expand.py` 移除跨层钻孔缓存、介质扣孔函数与调用；图元极性规整保留。
- `drawing.py` 按解析精度构造绘图副本，复用本次渲染的钻孔投影/并集。
  仅选介质而未选 DRILL 时，使用原 step、符号库和展开选项读取其投影；失败明确诊断。
  铜层对象保持原样。绘图副本被阻止导出为中间 JSON，避免误把已扣孔数据写回接口。
- `svg.py` 公共 SVG、HTML 和逐层 SVG 使用同一绘图结果；JSON 写出先使用原模型。
- `equiv.py` 仅为介质收集、匹配 DRILL；铜层材料循环不叠加投影。
  复杂材料图案继续采用扫描线区间逻辑，不执行新的材料 union/difference。
- Pydantic 删除 `parse_info.drills_subtracted/drill_cut_mm2`，并重生成文本 `.schema`。
  内部 dataclass 上的同名字段仅作为绘图审计，不进入 JSON。

## 验证

合成测试涵盖 SIGNAL/POWER_GROUND/MIXED 铜层避空区与钻孔重叠、所有五项物性与
无 DRILL 参考逐单元相等、介质黑色覆盖、profile 孔洞保护、铜层输入中的未知/失败/未导出
DRILL、筛选介质预览的延迟展开、绘图不修改 JSON、逐层 SVG 一致性及绘图副本导出防护。
独立布尔参考同时验证铜层单独图案和介质多个重叠钻孔的扫描线掩膜。

真实案例以 `9db11c7` 为基线，在两个独立进程分别使用对应版本导出、导入和等效，
不向旧代码灌入新格式。报告为 `docs/equivalence/drill-policy-validation.json`。
检查原始浮点物性、原始采样掩膜、铜图案、逐层计算域、Z 与跨度、region 和全局网格坐标。
新铜层另与移除所有 DRILL 的独立参考比较；其余层域保持原样，保证几何规划基础相同。

四案例介质掩膜和五项物性完全相同，铜图案/域/Z 与 region/网格完全相同；
介质绘图几何的对称差面积均为 0。刚挠板有 204 个铜层单元的材料数值按新规则改变，
分布于 10 层；它们均与无 DRILL 参考完全一致。其他三例铜层物性恰好不变，
避空区不被填黑仍由专门合成测试验证。

| 案例 | region | 三维物性行 | JSON MiB 前 → 后 | 合成秒 前 → 后 | SVG 秒 前 → 后 | 等效秒 前 → 后 | 主流水线峰值 MiB 前 → 后 |
|---|---:|---:|---:|---:|---:|---:|---:|
| minimal-test-odb | 1 | 8352 | 0.04 → 0.04 | 0.00 → 0.00 | 0.00 → 0.00 | 0.40 → 0.38 | 77.88 → 78.25 |
| kicad-kitchen-sink | 78 | 12651 | 0.68 → 0.67 | 0.11 → 0.11 | 0.02 → 0.03 | 0.61 → 0.62 | 89.35 → 88.74 |
| kicad-video-odb.tgz | 6 | 24087 | 36.32 → 31.03 | 15.50 → 13.26 | 1.26 → 1.73 | 3.48 → 3.57 | 556.62 → 501.80 |
| designodb-rigidflex.tgz | 45 | 66274 | 91.14 → 83.72 | 59.11 → 77.42 | 2.50 → 4.91 | 6.35 → 7.24 | 1716.68 → 1633.93 |

时间为单次观测，测量时同一机器还在批量生成案例输出，不能据此做严格的速度优劣结论。
主流水线峰值取额外无 DRILL 参考运行前；参考运行时间在 JSON 报告中单独记录。
SVG 阶段现在承担扣孔计算，JSON 导出阶段不再承担该计算。

复现命令：

```powershell
uv sync --all-extras
uv run --all-extras python -m pytest
uv run --all-extras python tools/validate_drill_policy.py --baseline-ref 9db11c7
uv run --all-extras python tools/sync_out.py --grid-level standard --heatmap-grid
uv run --all-extras python tools/validate_architecture.py --audit-out --output docs/equivalence/output-validation.json
```

全量测试：775 passed（247.20 s）。全部 19 个案例输出已重生成，共 100 个物理层、386,938 行 MaterialMap；逐单元核查通过，所有 JSON 通过当前 Pydantic 模型导入校验。
掩膜和逐层热图、region 及全局网格诊断均随案例更新；另直接检查了四层板的铜/介质掩膜。
历史 `intermediate-validation.json` 与先前修复记录保留，避免把本次有意材料规则变化混入先前的接口等价证明。
