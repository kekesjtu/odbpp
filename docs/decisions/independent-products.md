# 独立产品与 JSON 契约

## 决策

仓库以 uv workspace 管理 `pcbkit`、`pcbeq`、`pcbkit-rust` 与 `pcbeq-rust`，四个包分别构建和发布 wheel / sdist。
根项目是开发工作区，统一锁定 Python 依赖；Cargo workspace 及 Cargo.lock 管理 Rust 核心。
Python 与 Rust 两条产品线独立安装、构建和演进，生产模块不得跨线 import 或依赖对方分发包。共同边界仅为中间 JSON 契约。Python 版独立使用。`pcbkit-rust` 自带 Python 输入与工程预览模块，不导入 `pcbkit`；`pcbeq-rust` 独立执行完整 JSON 到 MaterialMap 流程，Python 只作启动与结果可视化，其所需模块由 `pcbeq-rust` 自带，不导入 `pcbeq`。语言职责见 [Rust 数值核心](rust-numerical-cores.md)。

`pcbkit` 负责 ODB++ 读取、单位解释、几何展开、极性和拓扑、逐层域及绝对 Z。
`pcbeq` 读取中间 JSON 作为唯一板数据来源，独立完成 region、网格、材料采样和 MaterialMap。
来源路径用于追溯，等效器的运行数据来自 JSON 文件本身。

`main` 同时保留两条独立产品线；`dev/py` 只保留 Python 产品与对应测试/工具，`dev/rust` 只保留 Rust 产品、其自带 Python 适配/可视化与对应测试/工具。两个开发分支均保留共享 JSON 契约及适用领域文档。

## 依据与适用范围

文件契约允许解析和等效在独立环境运行，两个包分别携带满足自身运行需求的依赖。
SVG/HTML 服务于工程核对，中间 JSON 交付可计算的物理语义。
两个产品分别承担本阶段的输入校验和可定位诊断。

## 实现对照

`contracts/intermediate.py` 是模型权威源；`tools/sync_contract.py` 同步存在的各分发包内模型和文本 schema。
软件分发版本用于安装管理，接口使用唯一当前结构，并校验字段和必要物理语义。
Python 产品内部几何和计算结构使用 dataclass / 数组，Pydantic 负责其 IO 边界；Rust 等效器使用 Serde 模型和原生结构校验同一文件契约。

`tools/run_pipeline.py` 使用标准库子进程和参数列表调用两个实际 CLI，记录 JSON 哈希、返回码、日志及耗时。
Python 调用方也应在两产品边界显式序列化或读取 JSON。
独立安装、依赖边界与契约同步由 `tests/distribution/`、`tests/contracts/` 和分发验证工具检查。
