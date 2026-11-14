# 项目架构

## 产品边界

```text
ODB++ 单板目录或压缩包
  └─ pcbkit-rust ──> 中间 JSON ──> pcbeq-rust ──> MaterialMap JSON
        └─ SVG / HTML / PNG 预览             └─ 工程图表与运行诊断
```

`pcbkit-rust` 解释 ODB++ 的单位、图元、极性和叠层，输出毫米坐标下的正向实体多边形及逐层几何。`pcbeq-rust` 规划近似 region 与局部网格，采样材料图案，为每个完整单元计算物性。

[中间 JSON](interfaces/intermediate-json.md) 是两者交接板数据的唯一接口。两个包分别安装和分发，来源路径用于追溯。软件分发有各自版本，板数据使用同一当前契约。等效结果定义见 [MaterialMap](interfaces/materialmap.md)。

## 从轮廓到有效计算域

| 名称                     | 含义                                                                                                   |
| ------------------------ | ------------------------------------------------------------------------------------------------------ |
| 整板轮廓（step profile） | ODB++ `steps/<step>/profile` 定义的整板外边界、岛和内孔边界                                            |
| 层轮廓（layer profile）  | 可选的 `steps/<step>/layers/<layer>/profile`，描述该层铺设到哪里                                       |
| 层实体范围(有效计算域)   | 有层轮廓时，由层岛扣除层孔与 step 孔的并集得到；缺少层轮廓时取整板轮廓围成的实体范围。层可伸出 step 岛 |

例如刚挠板的某层只覆盖刚性区，该层轮廓只围住刚性区；某层也可伸出整板轮廓，伸出部分同样属于该层实体。中间 JSON 的 `profiles` 保存按规整几何去重的轮廓多边形，层范围包含继承的 step 孔。`profiles[].layers` 列出所属 BOARD 层，`step.profile_id` 指定整板轮廓。铜图案和钻孔投影描述有效计算域内的材料分布。选择轮廓和解释层厚的规则见[逐层轮廓与叠层](decisions/pcbkit/layer-profiles-and-stackup.md)。

## 等效器的数据流

```text
中间 JSON
  ├─ 整板与层实体范围 ──> 有效域去重、独立分区、局部网格
  └─ 材料图案与钻孔跨度 ──> 按 region 采样 ──> 单元物性 ──> MaterialMap
```

各有效 profile 独立规划二维矩形，与引用物理层的 Z 区间共同定义三维 region。相同有效几何复用同一 region 划分方案。几何规划使用矢量范围、拓扑和物理尺寸。网格确定后，材料分支为各 region 生成光栅，再按网格统计样本。调整采样密度不会移动 region 或网格。具体约束见[几何规划](decisions/pcbeq/geometry-planning.md)和[材料采样](decisions/pcbeq/material-sampling.md)。

## 分发与语言职责

| 分发包        | 职责与语言边界                                                                                                                               |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `pcbkit-rust` | 包内 Python 负责输入调度、Pydantic 契约校验与预览，Rust 负责 features/profile 解析、符号展开、整层合成与几何规整                                                         |
| `pcbeq-rust`  | 独立 Rust CLI 从 JSON 读取校验到 MaterialMap 和数值诊断发布，可单文件分发。可选 Python 工具通过子进程调用 CLI，并从正式 MaterialMap 渲染图表 |

MISC 不进入中间 JSON，由 Rust 生成独立有序绘图指令供 Python 预览。BOARD 图元及中间几何保存在 Rust 内部，Python 在 JSON 契约与绘图边界读取最终结果。

解析器的 wheel / sdist 使用 Maturin 构建 PyO3 扩展。等效器由 Cargo 构建 `pcbeq` 可执行文件，计算不依赖 Python；其 Python 管线和绘图单独构建纯 Python wheel / sdist，通过 `PATH`、`PCBEQ_EXE` 或显式路径定位可执行文件。计算参数及默认值由 Rust CLI 解释，绘图可以独立重跑。uv workspace 锁定 Python 依赖，Cargo workspace 管理 Rust 依赖，两个产品通过文件契约独立运行。

等效器的平面几何统一使用纯 Rust `geo` 库，Windows 发布静态链接 C 运行库，无需额外 Python、GEOS 或 VC 运行库。验收关注物理区域、拓扑、面积预算和物性容差，不以特定几何库的顶点排列或分块结果为标准。Shapely 仅可用于开发侧独立验收。

`main` 维护 Rust 产品线及其随包 Python 适配。`reference` 同时保留 Python 与 Rust 两条产品线，便于对照。独立安装与跨环境交接见[分发验证工具](../tools/README.md)，数值与图表的发布边界见[输出与发布](decisions/output-publication.md)。
