# external/ — 开源参考项目

本目录存放用于**只读参考**的上游开源项目。这些项目**不参与本项目构建**，
其代码**不进入本仓库的版本历史**（见根目录 `.gitignore` 中的 `external/*/`），
需要时按下方命令就地克隆。

## 为什么不做成 submodule

1. 上游均带 **copyleft 许可证**（AGPL-3.0 / GPL-3.0），以 submodule 形式固化引用关系
   容易造成「本项目包含该代码」的误读。
2. 参考项目会持续演进，我们只需要「读某个时间点的实现思路」，不需要版本绑定。
3. 避免在 clone 本项目时被迫下载数十 MB 的无关代码。

## 拉取命令

```bash
# OdbDesign —— C++ ODB++ 解析库（AGPL-3.0），数据结构与文件模型的主要参照
git clone --depth 1 https://github.com/nam20485/OdbDesign.git external/OdbDesign
```

## 使用约束（**重要**）

| 项目 | 许可证 | 允许 | 禁止 |
|---|---|---|---|
| `nam20485/OdbDesign` | **AGPL-3.0** | 阅读文档、数据结构、算法思路 | 复制/改写在任何形式进入本项目 |
| KiCad（未克隆，按需查阅） | GPL-3.0 | 用于交叉验证规范一致性 | 复制代码 |
| `ulikoehler/ODBPy` | Apache-2.0 | 借鉴（需保留 NOTICE） | — |
| `vlovo/pcb2svg` | BSD-3-Clause | 借鉴（需保留版权声明） | — |
| `mcix/odbpp`、`capablemonkey/odb-pp-parser` | **未声明**（默认保留所有权利） | 仅阅读、获取样本 | 复制任何代码或文档文本 |

> AGPL-3.0 对**网络服务**同样触发开源义务，因此「反正是内部服务」不是规避理由。

详细调研结论见 `docs/research/04-open-source-reference-survey.md`。
