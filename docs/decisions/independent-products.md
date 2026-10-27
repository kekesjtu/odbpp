# 独立产品与 JSON 契约

## 决策

仓库以 uv workspace 管理 `packages/pcbkit` 与 `packages/pcbeq`，两者分别构建和发布 wheel / sdist。
根项目是开发工作区，引用两个本地成员并统一锁定依赖。

`pcbkit` 负责 ODB++ 读取、单位解释、几何展开、极性和拓扑、逐层域及绝对 Z。
`pcbeq` 读取中间 JSON 作为唯一板数据来源，独立完成 region、网格、材料采样和 MaterialMap。
来源路径用于追溯，等效器的运行数据来自 JSON 文件本身。

## 依据与适用范围

文件契约允许解析和等效在独立环境运行，两个包分别携带满足自身运行需求的依赖。
SVG/HTML 服务于工程核对，中间 JSON 交付可计算的物理语义。
两个产品分别承担本阶段的输入校验和可定位诊断。

## 实现对照

`contracts/intermediate.py` 是模型权威源；`tools/sync_contract.py` 同步两个包内模型和文本 schema。
软件分发版本用于安装管理，接口使用唯一当前结构，并校验字段和必要物理语义。
内部几何和计算结构使用 dataclass / 数组，Pydantic 负责 IO 边界。

`tools/run_pipeline.py` 使用标准库子进程和参数列表调用两个实际 CLI，记录 JSON 哈希、返回码、日志及耗时。
Python 调用方也应在两产品边界显式序列化或读取 JSON。
独立安装、依赖边界与契约同步由 `tests/distribution/`、`tests/contracts/` 和分发验证工具检查。
