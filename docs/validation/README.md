# 验证方法

## 验收入口

[需求](../../requirements.md#验证要求) 定义完整验收范围，日常命令见 [开发说明](../development.md)。
验证分为几何、材料采样、接口、分发与显示，使用独立预期和实际输出核查。

| 范围 | 主要验证 |
| --- | --- |
| 公式与材料 | 全铜、全 FR4、水平 / 竖直条纹、混合质量与热容守恒、有限正值及两相上下界 |
| 钻孔 | 相交介质实心铜、铜层独立、拓扑跨度、未知跨度报错、岛孔与重叠掩膜 |
| 几何 | 内接、非重叠、完整轮廓距离、面积预算、独立实体、孔洞、窄颈和刚挠层域 |
| 网格与采样 | 强制边界保留、共同轴线、最大尺寸、强制短区间、中央宽区间、各轴采样中心与容量错误 |
| 几何独立性 | 固定物理几何及策略，变化 Px/Py 后逐一比较全部 region 和全局轴线坐标 |
| 物理接口 | 完整层目录、域引用、绝对 Z、未知厚度及来源、五项完整单元物性 |
| 独立分发 | wheel / sdist、两环境安装、跨进程 JSON、移走原始板后等效仍可运行 |
| 图片与交互 | 实际 region、热图网格、物理长宽比、层名冲突、矩阵箭头、缩放坐标与测距 |

`tests/` 提供跨包测试，两个包的 `tests/` 提供算法和 CLI 回归。
`tools/audit_out.py` 从正式 MaterialMap 重建单元，独立核查层域、Z、几何预算、尺寸、采样容量与物性范围。
`tools/validate_region_accuracy.py` 检查真实几何及光栅独立性，可额外运行材料采样对照。

## 报告与复现

运行验证时，记录输入、代码版本、参数、测量口径和结果。工具默认将 JSON 报告写入 `tmp/validation/reports/`，该目录不纳入版本控制：

```powershell
uv run --all-extras python tools/audit_out.py
uv run --all-extras python tools/validate_region_accuracy.py
uv run --all-extras python tools/validate_region_accuracy.py --case kicad-video-odb.tgz --sampling-convergence --output tmp/validation/reports/region-sampling.json
```

`--output` 可指定审计或几何验证的报告路径；预览刷新工具使用 `--report`。
原有历史报告保存在当前工作区的 `tmp/validation/history/`，也可从 Git 提交 `2e60504` 的 `docs/validation/reports/` 恢复。
`tmp/` 不随仓库分发，新环境可按需要重新运行验证或从 Git 历史提取报告。

Python 分配峰值不含 GEOS / NumPy 原生分配；进程峰值单独记录。事件同步处理耗时、绘制帧率、阶段计时与完整 CLI 墙钟时间各有不同口径。
细走线和小孔可能保持较大局部相对差异，坐标一致与采样容量通过不能替代物性收敛。
