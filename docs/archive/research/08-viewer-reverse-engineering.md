# 08 · Siemens ODB++ Viewer 内部解析逻辑的逆向可行性

调研日期：2026-09-25。本文的“逆向”专指**分析已安装 Viewer 程序内部如何解析 ODB++**，不指从截图重建 PCB。仅做安装包结构和文档的只读检查；未反编译、调试或修改程序。

## 已确认的安装情况

Windows 开始菜单快捷方式指向 `E:\SiemensEDA\Valor\ODB++_Viewer\odbviewer_2604\edir\get\odbviewer.exe`。该文件版本为 **2604.1**，大小约 63 KB；安装树中还有大量原生模块（本机盘点为 273 个 `.dll`、101 个 `.exe`，包含其他辅助程序）。因此 `odbviewer.exe` 本身不能代表完整的解析实现，也不能仅凭模块名称认定哪个 DLL 处理某种 ODB++ 记录。Siemens [下载页](https://odbplusplus.com/design/download/odb-viewer/)将当前产品定位为免费查看 ODB++ 产品模型的工具。

安装包带有 `edir/all/manuals/docs/odbv_user.pdf`（2604 用户手册，25 页）以及 HTML 帮助。手册说明 Viewer 可导入 ODB++ 目录和压缩包，并在界面中查看层、组件和图元；[Siemens 的 Viewer 指南](https://resources.sw.siemens.com/en-US/technical-paper-odb-viewer-user-guide/)也描述其查看用途。该 Viewer 专用手册未给出“导出解析后的多边形/JSON”的接口说明，不能把“程序能显示”推断成“有可调用的解析库”。

安装树的 `edir/all/python/Valor.py` 是文本脚本，头部标明 *Valor NPI module*；它向宿主的管道发送 `COM`/`AUX` 等命令。**这只能证明安装包包含 NPI 脚本桥接材料，不能证明 ODB++ Viewer 2604 对外开放该接口，更不能证明它暴露解析后的 polygon API。**

## 三条技术路线

| 路线 | 可行性 | 对本项目的实际价值 |
|---|---|---|
| **黑盒对照**：把自制小 ODB++ 作业导入 Viewer，读取图元信息、层次、尺寸和显示效果；每次只改一个字段 | 高，Viewer 手册支持导入和逐图元查看；不依赖内部实现 | 可形成独立的“接受/拒绝、尺寸、极性、孔洞”对照表，验证本解析器的具体假设 |
| **寻找受支持的自动化接口**：进一步核对产品文档或向 Siemens 询问 Viewer 是否提供脚本/SDK/批量导出 | 尚无证据；不能把 `Valor.py` 当成 Viewer API | 若存在，能将人工对照改为自动回归 |
| **原生二进制逆向**：定位解析模块、动态跟踪、反汇编/反编译并重建算法 | 技术上可能，但预计成本高、版本脆弱；目前没有模块边界或符号信息的证据 | 对已公开的 ODB++ v8.1 文件语法收益有限；不适合作为当前 parser 的主要实现依据 |

黑盒测试的最小步骤：取一个已知可解析的小作业，记录 Viewer 的导入结果和图元属性；分别只改变 `UNITS`、一个标准符号尺寸、一个正/负极性记录、一个钻孔跨度、一个层级 `profile`；记录 Viewer 的接受/拒绝和数值，再与 `odbpp` 输出比较。Viewer 中的 [Feature Information](https://odbplusplus.com/wp-content/resource-docs/odbplusplus.any/docs/pdfdocs/vsure_gs.pdf)可显示图元的坐标、symbol、极性、钻孔尺寸和属性，适合少量人工核验。对观察到的差异应先回到本地官方 v8.1 Update 4 规范核对：Viewer 的显示行为不自动等于规范要求，特别是它可能容忍或修复不规范输入。

## 决策与边界

**结论：可做行为逆向，不建议现在直接拆解 Viewer 的内部解析器。** 当前已有 [官方格式规范](../../reference/ODB++_Format_User_Guide.pdf)、[开源实现调研](04-open-source-survey.md)和本项目的手写夹具；Viewer 最有价值的角色是独立、人工可核验的参考工具。若将来发现规范无法解答、真实样本与 Viewer 行为冲突，再针对一个具体问题评估深入逆向。

此外，安装包中的 `legal/` 主要是第三方组件披露，不能据此判断 Siemens 自有代码的反编译授权。免费使用 Viewer 也不等于获得逆向、复制或再分发内部实现的许可；在开展二进制逆向前应核对实际适用的许可协议与当地法律。本文不据此作法律结论。
