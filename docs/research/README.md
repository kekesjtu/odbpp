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

## 复现调研结论的工具

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
   **不可用常数换算。**（`01` §3.2）
3. **介质层基底不能一律继承 `steps/<step>/profile`**：生产级刚挠板带层级 `profile`，
   机械套用会造出假介质。建议改为「层级 `profile` 优先，缺失才回退」。（`03` §2.4(d)）
