# 08 · 等效结果可视化审阅修复（2026-09-30）

修复基线：`6c53734`。本轮处理两项审阅发现的问题：掩膜文件名碰撞，以及
默认材料警告只写入元数据、命令行不可见。

## 问题与修改

1. **掩膜重名导致导出中断。**层名 `A/B` 和 `A:B` 都会变成 `A_B.png`。
   旧代码在等效阶段连续写同一个 `A_B.png.tmp`，提交产物时第一次
   `os.replace` 成功，第二次找不到临时文件并抛出 `FileNotFoundError`。
   当时 CSV、meta 已写出，只有一张掩膜，热图尚未生成。现在掩膜与热图都
   调用 `unique_image_stem()` 逐层预留完整文件名；已使用的名称按
   `casefold()` 比较，再依次尝试 `-2`、`-3` 等后缀。完整名称也参与查重，
   因此 `A/B`、`A:B`、`A_B-2` 分别得到 `A_B.png`、`A_B-2.png`、
   `A_B-2-2.png`；`Top` 与 `top` 在 Windows 上也不会互相覆盖。
   图例页保留原层名并链接到各自的图。
2. **默认材料警告缺少命令行反馈。**`MaterialBook` 已把
   `materials.default_used` 记入 `MaterialMap.meta.json`，但等效命令结尾
   只打印错误级诊断。现在成功运行时还会把该代码的告警打印到 stderr，
   涵盖铜与基底以及部分字段回退的情况；CSV、meta 与退出码语义不变。

## 验证

- `uv sync --all-extras` 后运行
  `uv run --all-extras python -m pytest tests/test_visualize.py -q -p no:cacheprovider`：
  44 项通过。
- 新增 CLI 端到端样例包含五个会相互碰撞的层名，检查五张不同的掩膜、五张
  热图、图例链接、CSV/meta 和无残留临时文件。
- 最小真实 case 不提供材料配置时，命令退出码仍为 0、CSV 正常生成，
  stderr 中有两条 `materials.default_used`（铜和基底）。
- 原审阅的 `A/B`、`A:B` 中间 JSON 复现命令现返回 0，实际得到
  `masks/A_B.png`、`masks/A_B-2.png` 及对应的两张热图、图例页，
  没有残留 `.tmp`。
- 全量 `uv run --all-extras python -m pytest -q -p no:cacheprovider`：
  700 项通过。图像仍是人工核对手段，数值以 CSV 和 meta 为准。
