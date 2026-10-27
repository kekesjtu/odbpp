# 官方规范与参考格式

## ODB++ 格式规范

| 项目 | 项目采用的材料 |
| --- | --- |
| 标题 | ODB++Design Format Specification |
| 发布方 | Siemens Industry Software |
| 版本 | Release 8.1 Update 4，product.version.8.1.4 |
| 日期 | 2024-07-16，PDF ModDate 2024-07-17 |
| 页数 | 515 |
| 本地文件 | [ODB++_Format_User_Guide.pdf](ODB++_Format_User_Guide.pdf) |
| MD5 | f57b7264488e8670f81c6b94cb3b9fe1 |
| 官方来源 | [规范 PDF](https://odbplusplus.com/wp-content/uploads/sites/2/2024/08/odb_spec_user.pdf) |

PDF 为本地参考文件，由 `.gitignore` 排除。需要时从上述来源获取并核对摘要：

```powershell
Get-FileHash -LiteralPath docs/reference/ODB++_Format_User_Guide.pdf -Algorithm MD5
```

摘要变化时核对规范修订并复核相关语义。文档和代码统一使用 `p.<PDF 页码>`，例如单位语义 p.36 / p.166。
该版本是项目依据，外部最新发布状态不由本页断言。

## MaterialMap 参考资料

[Flotherm_reference_MaterialMap.csv](Flotherm_reference_MaterialMap.csv) 约 117 MiB，使用 Git LFS 保存，服务于输出信息组织的研究。
本项目的正式接口独立设计，定义见 [MaterialMap JSON](../interfaces/materialmap.md)。

```powershell
git lfs install
git lfs pull
```

来源数据、项目接口与生成结果按各自用途维护；正式案例输出位于 `out/`。
