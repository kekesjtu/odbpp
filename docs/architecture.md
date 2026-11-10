# 项目架构

## 产品边界

```text
ODB++ 单板目录或压缩包
  └─ 解析器 ──> 中间 JSON ──> 物性等效器 ──> MaterialMap JSON
       └─ SVG / HTML 预览                     └─ 工程图表和运行诊断
```

解析器解释 ODB++ 的单位、图元、极性和叠层，输出毫米坐标下的正向实体多边形和逐层几何。等效器规划内接 region 与局部网格，采样材料图案，并为每个完整单元计算物性。

[中间 JSON](interfaces/intermediate-json.md) 是两者交接板数据的唯一接口。遵循同一当前契约的 Python 和 Rust 产品可交叉配对。来源路径用于追溯。软件分发有各自版本，契约字段变化时同步调用方。等效结果定义见 [MaterialMap](interfaces/materialmap.md)。

## 从轮廓到有效计算域

| 名称 | 含义 |
| --- | --- |
| 整板轮廓（step profile） | ODB++ `steps/<step>/profile` 定义的整板外边界、岛和内孔边界 |
| 层轮廓（layer profile） | 可选的 `steps/<step>/layers/<layer>/profile`，描述该层铺设到哪里 |
| 层实体范围 | 由层轮廓确定的二维实体区域，扣除轮廓定义的内孔。缺少层轮廓时取整板轮廓围成的实体范围 |
| 层有效计算域 | 等效器取层实体范围与整板实体范围的交集，作为该层建模和材料采样的面积范围 |

例如刚挠板的某层只覆盖刚性区，该层轮廓只围住刚性区。中间 JSON 的 `domains` 存的是轮廓规整成的实体多边形，因此可以直接做面积和包含性计算。铜图案和钻孔投影描述有效计算域内的材料分布。选择轮廓和解释层厚的规则见 [逐层轮廓与叠层](decisions/pcbkit/layer-domains-and-stackup.md)。

## 等效器的数据流

```text
中间 JSON
  ├─ 整板与层实体范围 ──> 共享二维分区、层归属、局部网格
  └─ 材料图案与钻孔跨度 ──> 按 region 采样 ──> 单元物性 ──> MaterialMap
```

共享二维矩形与所属物理层的 Z 区间共同定义三维 region。几何规划使用矢量范围、拓扑和物理尺寸。网格确定后，材料分支为各 region 生成光栅，再按网格统计样本。调整采样密度不会移动 region 或网格。具体约束见 [几何规划](decisions/pcbeq/geometry-planning.md) 和 [材料采样](decisions/pcbeq/material-sampling.md)。

## 独立分发与语言职责

| 分发包 | 职责与语言边界 |
| --- | --- |
| `pcbkit`、`pcbeq` | 可独立安装的 Python 解析器和等效器，也用于与 Rust 数值核心对照 |
| `pcbkit-rust` | 包内 Python 负责输入调度、契约校验与预览，Rust 负责几何离散、整数布尔和扫掠 |
| `pcbeq-rust` | 从 JSON 读取校验到 MaterialMap 和数值诊断发布由 Rust 完成。Python 启动命令，并从正式 MaterialMap 渲染图表 |

四个包分别构建并发布 wheel / sdist。各包携带所需运行模块，生产代码不跨包或跨产品线导入。uv workspace 锁定 Python 依赖，Cargo workspace 管理内部 Rust 核心，PyO3 扩展由 Maturin 构建。共享 Rust crate 用于内部几何复用。

Rust 等效器直接调用 GEOS C API 做连续几何检查。GEOS 可从 Shapely wheel 的原生库目录定位，或通过 `PCBEQ_GEOS_LIBRARY` 指定。Rust 扩展缺失时明确报错。两版均遵守相同的物理约束，允许环排序和离散细节有差异，验收比较物理区域、拓扑、误差预算及物性容差。

`reference` 同时保留 Python 和 Rust 两条产品线，便于对照。`main` 维护 Rust 产品线及其随包 Python 适配。独立安装和跨环境交接由[分发验证工具](../tools/README.md)检查。数值、图表和诊断的发布边界见[输出与发布](decisions/output-publication.md)。
