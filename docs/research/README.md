# docs/research/ — 调研报告

本目录存放项目启动阶段的调研产出。所有格式语义结论均以
`docs/ODB++_Format_User_Guide.pdf`（*ODB++Design Format Specification*,
Release 8.1 Update 4, Siemens, 2024-07-16）为**唯一权威依据**。

| 文档 | 内容 | 回答的问题 |
|---|---|---|
| [01-odbpp-v8.1-format-and-units.md](01-odbpp-v8.1-format-and-units.md) | 文件系统模型、行记录/结构化文本语法、**单位制**、属性两段间接寻址 | 「数据长什么样、单位怎么算」 |
| [02-geometry-model-and-symbols.md](02-geometry-model-and-symbols.md) | 5 类图元、表面/多边形方向与孔洞约定、标准符号展开、自定义符号递归、变换语义、正向前实体构造流水线 | 「几何怎么变成多边形」 |
| [03-cases-inventory-and-observations.md](03-cases-inventory-and-observations.md) | `cases/` 四例普查、图元统计、实测反例、实现 Checklist、覆盖缺口 | 「拿什么验证、哪里会踩坑」 |
| [04-open-source-reference-survey.md](04-open-source-reference-survey.md) | 8 个开源项目的能力/许可证分析、技术选型建议、社区文档错误清单 | 「借鉴谁、不能抄谁」 |
| [05-spec-ambiguities-and-implementation-notes.md](05-spec-ambiguities-and-implementation-notes.md) | 实现期实际遇到、**规范无法唯一确定**的语义点，以及本 parser 采取的读法与验证状态；§12 为几何合成阶段（扫掠、极性、批处理）的判定与实测，§13 为 `FORM` 按规范 p.66 默认值解析（含一次初稿判定的推翻） | 「哪一行代码按哪个未经证实的假设写的」 |
| [06-project-review-2026-09-21.md](06-project-review-2026-09-21.md) | 当前项目审阅与四例完整导出报告：验证范围、七项已确认问题、复现步骤与产物清单 | 「现在哪里是错的」 |
| [07-review-fixes-2026-09-21.md](07-review-fixes-2026-09-21.md) | 对 `06` 七项问题的修正记录：根因、改法、验证数据与本轮残留风险 | 「改了什么、凭什么说改好了」 |
| [08-followup-review-2026-09-21.md](08-followup-review-2026-09-21.md) | 修复后的再次审阅：三项可复现问题（含原始坐标与面积）与全量测试结果 | 「改完还剩什么」 |
| [09-review2-fixes-2026-09-21.md](09-review2-fixes-2026-09-21.md) | 对 `08` 两项 P1、一项 P2 的修正 + 绝对 Z 起止与英制铜厚换算：改法与验证 | 「这一轮改了什么」 |
| [10-third-review-2026-09-21.md](10-third-review-2026-09-21.md) | 第三次审阅：两项 P1 几何用例通过，确认三项输出一致性/Z 标记 P2 | 「改完还剩什么」 |
| [11-third-review-fixes-2026-09-21.md](11-third-review-fixes-2026-09-21.md) | 对 `10` 三项 P2 的修正：装饰层厚度标记、三产物共享分辨率并重新规整、汇总/边界从写出的层派生 | 「这一轮改了什么」 |

## 复现调研结论的工具

报告 13 中的 HTML 标签、零位小数和 profile 精度问题已修复，见 [14-review-fixes-2026-09-22.md](14-review-fixes-2026-09-22.md)，包含真实浏览器验证和四例重新导出结果。

导出性能审阅见 [13-export-performance-review-2026-09-22.md](13-export-performance-review-2026-09-22.md)：重复边界计算与渲染的优化、四例产物兼容性及耗时对比，以及本轮发现的既有显示问题。可用 `tools/benchmark_export.py` 复测并比较文件 SHA-256。

最新复审见 [12-fourth-review-2026-09-21.md](12-fourth-review-2026-09-21.md)：上次三个 P2 的独立复现均通过，本轮未发现明确新增回归问题。

第三次审阅见 [10-third-review-2026-09-21.md](10-third-review-2026-09-21.md)：上次核心几何用例通过，仍有三个输出一致性/Z 标记问题；其修正记录见 [11-third-review-fixes-2026-09-21.md](11-third-review-fixes-2026-09-21.md)。

修复后的再次审阅见 [08-followup-review-2026-09-21.md](08-followup-review-2026-09-21.md)：保留三个可复现问题及全量测试结果。

当前项目审阅见 [06-project-review-2026-09-21.md](06-project-review-2026-09-21.md)（七项已确认
问题、复现方法与验证范围），其修正记录见 [07-review-fixes-2026-09-21.md](07-review-fixes-2026-09-21.md)。

```bash
# 把 515 页 PDF 转成可 grep 的带页码文本（输出到 build/，不入库）
python tools/extract_spec_text.py

# 按页码引用规范原文
python tools/extract_spec_text.py --page 36 41

# 全文检索（带页码）
python tools/extract_spec_text.py --grep "copper weight"

# 对 cases/ 做图元/极性/符号统计
python tools/census_cases.py
```

## 三条最需要在实现中守住的结论

1. **单位双轨制**：坐标用 `mm`/`inch`，但**符号参数、resize 因子、钻孔尺寸**用
   `µm`/`mil`。搞错即 1000 倍误差。（`01` §3.3）
2. **`copper weight` 的物理量随单位制改变**：`MM` → 铜厚 µm；`INCH` → 面密度 oz/ft²。
   两者**不是同一个物理量**，不能用规范常数互换；本 parser 保留 `kind`/`unit` 区分，
   并对英制面密度用**公开假定的铜密度 8.96 g/cm³** 派生厚度（用于绝对 Z），
   在输出中记录 `thickness_source` 与 `assumed_density_g_cm3`。（`01` §3.2、`09` §5）
3. **介质层基底不能一律继承 `steps/<step>/profile`**：生产级刚挠板带层级 `profile`，
   机械套用会造出假介质。已改为「层级 `profile` 优先，缺失才回退；层级 `profile`
   存在但不可解析则报错」。（`03` §2.4(d)、`05` §5）

## 实现期新增的发现

`05-spec-ambiguities-and-implementation-notes.md` 是**实现阶段**的产物，与上述
四份「开工前调研」性质不同：它逐条记录规范原文不足以唯一确定语义的地方、本仓库
择定的读法、以及该读法是否被真实样本验证过。新增实现若引入新的未证实假设，
应同步登记到该文件。
