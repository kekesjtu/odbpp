# 独立产品与 JSON 契约

本分支 `dev/rust` 只维护 `pcbkit-rust` 与 `pcbeq-rust`，分别构建和分发 wheel / sdist。
生产模块及其运行依赖归属各自分发包，不导入另一产品线的源码。
Cargo workspace 与 Cargo.lock 管理 Rust 核心；包内 Python 携带输入适配、契约读取及工程图表。

解析器交付正向实体多边形、逐层计算域及绝对 Z；等效器仅从中间 JSON 读取板数据，生成内接三维 region、局部网格与完整单元物性。
两个产品的唯一板数据接口是中间 JSON，不共享运行时几何或内部对象。

`contracts/intermediate.py` 为契约权威源，`tools/sync_contract.py` 同步本分支所需的包内模型与文本 schema。
Rust 等效器使用 Serde 独立验证同一契约，Python 绘图只读取 MaterialMap。

CLI 子进程管道及独立分发验证检查文件交接、错误处理和依赖隔离。
