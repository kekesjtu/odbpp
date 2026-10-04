# 12 · Heatmap 绘制重写

2026-10-05。用户明确要求三个方向保留在同一张图中，并完全删除旧绘图逻辑重写。
先前的独立方向卡片方案已退役，本文记录最终实现。

## 实现

- 删除手工色阶、量化与 RGB 行转换、像素边界映射、Canvas 热图绘制、点阵标签、
  重复色条及独立方向 PNG 生成逻辑。
- 使用 Matplotlib Figure/Agg 和 PolyCollection，按实际毫米坐标直接绘制 CSV 矩形。
  每层只生成一张 `heatmaps/<layer>.png`，kx、ky、kz 横向并排。
- 三个面板使用相同坐标范围、等比例轴、viridis 色阶及一条公共色条。
  图中显示层名、绝对 Z、类型、单元数、毫米坐标和热导率单位。
- 标题、元信息、子图、坐标标签与色条分配独立空间；长标题自动换行。
  均匀层显示单色条、唯一数值和 uniform 标记；未知厚度明确标注。
- 页面始终显示完整三方向图，窄屏采用横向滚动，点击可以查看原 PNG。
  材料掩膜保持折叠展示。移除独立方向图相关数据字段和同步规则。
- Matplotlib 从工具 extra 移入运行时依赖，更新 uv.lock，保证默认绘图命令可用。

## 验证

```powershell
uv sync --all-extras
uv run --all-extras python -m pytest tests/test_visualize.py tests/test_sync_out.py tests/test_packaging.py
```

54 项通过。像素解码验证三个方向对应独立的物性列、Y 轴方向、层域留空、
共享范围、均匀层、网格边界和层名碰撞。布局检查基于绘图对象实际包围盒，
验证宽板比例、标题与元信息/子图标题互不重叠，三个面板及公共色条彼此分离。
PNG 保持 RGB/filter 0 格式；运行时依赖检查通过。

重新绘制 `out/` 中 18 组案例的 81 张三方向热图，更新相应图例页，
删除上一版 243 张独立方向 PNG 及空目录。重绘耗时 25.44 s。
逐文件 SHA-256 核对 CSV 和 meta 完全不变，图例页全部图片和本地链接存在。
人工检查刚挠板 SIGNAL_1 和 kicad-kitchen-sink F.CU 的字体、坐标、比例及公共色条。
region、网格、材料采样和物性等效公式保持原有计算结果。

输出按项目规则保存在 out/，不纳入 Git；后续 heatmap/equivalent 命令使用此版本。
