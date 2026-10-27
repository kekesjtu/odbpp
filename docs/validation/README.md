# 验证方法与证据

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

## 证据使用规则

`reports/` 保存带输入、基线、参数和测量口径的 JSON 证据，路径整理保持其原始字节。
每份报告只证明对应代码与配置的测量结果；读取索引和整理文档不构成重新运行该验证。
当前代码验收应运行相应测试和审计，材料收敛需要比较实际逐单元数值。

最大尺寸单独控制的基线记录为 `6e74aa0`：报告记载 832 项回归、19 个 case 和 398,124 个完整三维单元。
这项记录的来源为 [max-grid-validation.json](reports/max-grid-validation.json)，逐案例输出证据见 [output-validation.json](reports/output-validation.json)。
原始审阅、修复及各阶段说明保存在 [归档](../archive/README.md)，当前设计以主题文档为准。

## 报告索引

| 证据 | 对应验证范围 |
| --- | --- |
| [documentation-validation](reports/documentation-validation.json) | 文档链接、归档完整性、需求保留、JSON 示例、契约路径及 832 项回归 |
| [max-grid-validation](reports/max-grid-validation.json) | 最大边长控制、强制短区间及全案例规模 |
| [output-validation](reports/output-validation.json) | 逐单元域归属、连续距离、全局轴线、容量和物性 |
| [independent-clis-validation](reports/independent-clis-validation.json) | 独立产品、分发环境与进程管道 |
| [no-y-quantization-validation](reports/no-y-quantization-validation.json) | 真实 Y 区间、共享域和几何坐标独立性 |
| [no-y-quantization-tight-validation](reports/no-y-quantization-tight-validation.json) | 收紧预算与最差位置细化路径 |
| [no-y-quantization-sampling](reports/no-y-quantization-sampling.json) | 固定几何的默认 / 双倍光栅物性对照 |
| [region-accuracy-validation](reports/region-accuracy-validation.json) | 对应 0.5 mm 距离预算的连续轮廓检查 |
| [region-accuracy-default-3mm-validation](reports/region-accuracy-default-3mm-validation.json) | 对应 3 mm 默认预算的几何对照 |
| [region-accuracy-tight-validation](reports/region-accuracy-tight-validation.json) | 对应内部种子配置的局部细化 |
| [region-sampling-validation](reports/region-sampling-validation.json) | 对应策略下的材料采样误差 |
| [architecture-validation](reports/architecture-validation.json) | 完整体积、几何哈希、采样误差与资源 |
| [coarse-region-validation](reports/coarse-region-validation.json) | 对应粗 region 配置的面积和拓扑 |
| [drill-policy-validation](reports/drill-policy-validation.json) | 介质未扣孔 JSON 与仅介质钻孔材料覆盖 |
| [drill-z-validation](reports/drill-z-validation.json) | copper_span 派生钻孔 Z 与物性一致性 |
| [intermediate-validation](reports/intermediate-validation.json) | Pydantic 接口、几何与导出 / 导入资源 |
| [remove-parse-info-validation](reports/remove-parse-info-validation.json) | 对应接口精简的逐案例对照 |
| [remove-root-diagnostics-validation](reports/remove-root-diagnostics-validation.json) | 根诊断与独立诊断的接口边界 |
| [materialmap-format-validation](reports/materialmap-format-validation.json) | region 层级格式及行主序映射 |
| [materialmap-process-validation](reports/materialmap-process-validation.json) | 独立进程物性、掩膜与几何对照 |
| [performance-validation](reports/performance-validation.json) | 对应基线的阶段耗时、哈希与资源 |
| [performance-sampling-validation](reports/performance-sampling-validation.json) | 对应基线的逐物性采样比较 |
| [performance-output-validation](reports/performance-output-validation.json) | 对应性能变更的最终输出核查 |
| [viewer-performance-validation](reports/viewer-performance-validation.json) | 浏览器事件、布局与功能对照 |
| [viewer-output-validation](reports/viewer-output-validation.json) | CSS/JS 刷新后的非代码载荷一致性 |

Python 分配峰值不含 GEOS / NumPy 原生分配；进程峰值单独记录。事件同步处理耗时、绘制帧率、阶段计时与完整 CLI 墙钟时间各有不同口径。
细走线和小孔可能保持较大局部相对差异，坐标一致与采样容量通过不能替代物性收敛。
