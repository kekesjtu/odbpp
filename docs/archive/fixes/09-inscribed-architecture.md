# 09 · 全局笛卡尔栅与自适应档位（2026-09-30）

用户的新决策先写入 AGENTS.md 并提交，再替换局部 region 网格。当前输出 schema 为
`odbpp/materialmap-5`，JSON 输入仍为 `odbpp/preview-2`。完整算法与内部预算见
[当前架构](../equivalence/05-inscribed-architecture.md)。

## 改动与验证

- region 保留竖条阶梯，仅合并完整 Y 截面一致的相邻条带；保留岛、孔和正宽连接检查。
- region 的全部边界传播为共同全局 X/Y 轴线；各层单元是相邻轴线矩形，完整位于一个 region 和实体域内。
- CLI 提供粗/标准/细档位，默认标准。物理尺寸根据板范围与正向铜图案 2A/P 特征估算确定；估算不等同于实测最小线宽。
- 专家尺寸未指定的一项默认最小 0.1 mm、最大 5 mm；整数分格和采样无解时给出可定位错误。
- 默认材料光栅长边至少 4096 像素，在几何确定后增加采样容量；档位最低 8 个中心/轴，专家最低 1 个。显式 Px/Py 不足时报错，坐标不变。
- 掩膜显示长边目标 1400、热图面板宽 1440；扫描线逻辑组合掩膜，全局 Y 带用行前缀和与列累计统计。
- 全部 712 项测试通过。覆盖全铜、FR4、条纹、钻孔、嵌套岛孔、不同层域、窄颈/弯曲连接、强制边界传播、整数无解、光栅不足及默认警告。

## 三档真实案例

耗时只含等效计算，不含 JSON 读取、CSV/PNG 写出及逐单元验证。内存为独立进程峰值，包含 JSON、保留的结果对象和验证开销；CLI 会流式写 CSV，不能直接把此数字当成 CLI 峰值。

| 案例 | 档位 | 最大目标 mm | region | 全局 nx×ny | XY 实体单元 | MaterialMap 行 | 默认光栅 | 秒 | 峰值 MiB |
|---|---|---:|---:|---|---:|---:|---|---:|---:|
| designodb-rigidflex.tgz | coarse | 2.6246 | 287 | 194×125 | 11164 | 134756 | 5887×1563 | 6.930 | 425.5 |
| designodb-rigidflex.tgz | standard | 1.3123 | 287 | 241×129 | 15354 | 204766 | 5887×1563 | 8.113 | 470.9 |
| designodb-rigidflex.tgz | fine | 0.6562 | 287 | 335×138 | 24504 | 359128 | 5887×1563 | 11.035 | 570.3 |
| kicad-kitchen-sink | coarse | 5.5203 | 450 | 359×281 | 59549 | 178647 | 4096×2595 | 3.923 | 180.7 |
| kicad-kitchen-sink | standard | 2.7602 | 450 | 365×281 | 61092 | 183276 | 4096×2595 | 4.055 | 184.5 |
| kicad-kitchen-sink | fine | 1.3801 | 450 | 384×281 | 65901 | 197703 | 4096×2595 | 4.317 | 195.8 |

三档 region 坐标不变，物性网格按档位细分。两案例各档位的最低实际样本数均为 9/轴。
刚挠板全局轴线传播使默认标准档位产生 204,766 行；异形板产生 183,276 行。
全局网格的细节传播可能使档位间单元数差异较小，这已体现在输出计数。

## 固定几何的分辨率与基线对照

固定专家尺寸 0.1–5 mm、内部策略和 JSON，分别取 1200×900、2400×1800、4800×3600、9600×7200。
两个真实案例的 region 与每个 XY 单元坐标哈希完全相同；均通过内接、归属、尺寸、Z、完整体积和物性范围检查。

| 案例 | 旧 2400×1800 region / XY 单元 | 新版固定专家 region / XY 单元 | 新版面积损失 | 板外增益 |
|---|---|---|---:|---:|
| designodb-rigidflex.tgz | 332 / 25856 | 287 / 9368 | 1.389% | 0 mm² |
| kicad-kitchen-sink | 1195 / 432292 | 450 / 59549 | 1.159% | 0 mm² |

旧基线为 5aa06bf，其几何依赖光栅且允许部分覆盖；几何、输出体积语义不同，性能不能当成同等精度的直接比较。
9600×7200 仅作为采样比较参考，不是解析精确解。4800×3600 相对该参考：

| 案例 | kx p95 | ky p95 | kz p95 | 密度 p95 | 比热 p95 |
|---|---:|---:|---:|---:|---:|
| designodb-rigidflex.tgz | 8.795% | 7.152% | 10.007% | 3.321% | 1.986% |
| kicad-kitchen-sink | 0.000% | 0.000% | 0.000% | 0.000% | 0.000% |

少数细走线、钻孔和高对比白色间隙所在单元仍有很大的相对误差；异形板大部分单元为全 FR4，p95 近零不能证明其少数材料细节收敛。
至少 8 个中心/轴是容量保护，并不保证细走线被充分解析或热导率误差低于某百分比。
详细 median/p95/max、耗时和内存见 [验证数据](../../validation/reports/architecture-validation.json)。

## 最终输出核查

18 个 case 更新到 out/，共 641,177 行、81 张掩膜和同数量热图，每个 case 均有实际 region 图及尺寸表。
逐行核查全局轴线索引、所属 region、层域成员关系、尺寸、采样容量、绝对 Z、完整单元覆盖语义和有限正数/材料范围。
全部默认结果至少 9 个采样中心/轴。核查结果见 [output-validation.json](../../validation/reports/output-validation.json)。
第二次相同配置同步复用全部结果；缓存校验 schema、输入、自动/显式光栅模式、档位/专家尺寸、图片和实现指纹。
已目视核查异形板 region 图和 kx/ky/kz 图；图片仅用于诊断，数值结论由上述逐单元核查支持。

## 重放

```powershell
uv sync --all-extras
uv run --all-extras python -m pytest -q -o cache_dir=build/pytest-cache
uv run --all-extras python tools/sync_out.py --grid-level standard --force-equivalent
uv run --all-extras python tools/validate_architecture.py
uv run --all-extras python tools/validate_architecture.py --presets-only
uv run --all-extras python tools/validate_architecture.py --audit-out
```
