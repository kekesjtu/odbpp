# 17 · Region 层级 MaterialMap JSON

按用户确认方案，将正式结果从逐单元 CSV + MaterialMap 元数据旁车改为单个 `<step>.MaterialMap.json`。
当前定义见 [09-region-materialmap.md](../equivalence/09-region-materialmap.md)。

## 行为

- region 保存层名、层类型、三对 XYZ 最小/最大边界和 XY 网格线。每个 region 是具体物理层的三维块。
- Z 联合可信状态为 false 时，两端使用 null；内部 best-effort Z 继续保留在中间输入和诊断中。
- values 按 Y 外层、X 内层的行主序排列，每项固定为 kx/ky/kz/density/specific_heat。
- 不导出单元索引、单元坐标、面积、铜比例、像素计数或覆盖率。
- 导出最多缓存一层，兼容现有跨 region 行带积分；正式文件仅在完整性检查通过后替换。
- 热图仅凭 MaterialMap JSON 还原网格，不需要中间输入或诊断记录。
- 类型、轴线、边界、完整条目数、五项有限正物性、全局笛卡尔一致性和同层不重叠均校验；错误条目不能跳过。
- 材料与配置来源、默认材料警告、几何损失和逐层跳过原因单独留在 diagnostics/run.json；不再输出 MaterialMap.meta.json。独立诊断不写逐单元索引或采样统计。
- 成功发布新结果后删除该案例的旧 MaterialMap CSV / 元数据旁车，索引页与热图链接同步。

内部网格、逐行/逐列串并联公式、DRILL 仅作用于介质的规则及材料采样没有改变。

## 验证

全量测试：796 passed，193.68 s。新增 21 项格式测试覆盖未知 Z 与已知零坐标、固定五项载荷、
非均匀跨 region 行带重排、错误 / 缺少 / 多余物性、错误轴线、重叠块、全局线缺失、失败输出原子替换。

19 个真实案例全部更新，包含 100 个物理层、1,302 个三维 region 和 386,938 个物性单元。
按层身份、还原 XY、可信 Z / null 及固定五项物性归一化，与修改前 CSV 逐单元完全一致；
X/Y 比较沿用旧 CSV 的九位坐标精度，可信 Z 比较沿用旧六位精度。内部完整 region 与全局轴线也完全一致。
正式文件总大小从 63,115,231 字节降为 13,661,237 字节（减少约 78.4%）。
对照见 [materialmap-format-validation.json](../../validation/reports/materialmap-format-validation.json)。

四个代表案例在独立进程中读取相同中间输入，对照基线 51c268b：原始浮点单元、原始掩膜及几何哈希全部一致。
下面记录等效与写出阶段（不含 Parser 和 PNG 渲染）的单次观测，不能据此作严格速度优劣结论。
峰值为整个 worker 进程，包括读入中间数据。报告见 [materialmap-process-validation.json](../../validation/reports/materialmap-process-validation.json)。

| 案例 | region（二维） | 单元 | 秒：旧 → 新 | 峰值 MiB：旧 → 新 | 正式文件字节：旧 → 新 |
|---|---:|---:|---:|---:|---:|
| minimal-test-odb | 1 | 8352 | 0.471 → 0.437 | 74.46 → 75.93 | 1223411 → 246991 |
| kicad-kitchen-sink | 78 | 12651 | 0.712 → 0.648 | 74.09 → 76.55 | 1983953 → 515244 |
| kicad-video-odb.tgz | 6 | 24087 | 2.261 → 2.147 | 230.52 → 232.40 | 4197079 → 1073449 |
| designodb-rigidflex.tgz | 45 | 66274 | 4.966 → 4.581 | 487.79 → 489.46 | 11580529 → 2894414 |

最终全输出核查通过：从 JSON 还原每个单元，检查原始逐层域和 region 归属、全局轴线、尺寸上下限、
实际光栅采样容量、Z / null、五项物性范围与单元总数。记录为 [output-validation.json](../../validation/reports/output-validation.json)。
直接检查了厨房示例 F.CU 和刚挠板 FLEX_5 的三方向热图，图形方向、板内挖空及层域显示正常。

## 复现

```powershell
uv sync --all-extras
uv run --all-extras python -m pytest
uv run --all-extras python tools/sync_out.py --grid-level standard --heatmap-grid
uv run --all-extras python tools/validate_architecture.py --audit-out
uv run --all-extras python tools/validate_materialmap.py --baseline-ref 51c268b
```

历史 CSV 验证报告和修复记录保留用于回溯，不作为当前读取协议。
