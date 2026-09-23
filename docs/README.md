# docs/ — 格式说明与项目文档

## `ODB++_Format_User_Guide.pdf`

| 项 | 值 |
|---|---|
| 标题 | *ODB++Design Format Specification* |
| 发布方 | Siemens Industry Software（ODB++ 规范所有者） |
| 版本 | **Release 8.1 Update 4**（`product.version.8.1.4`） |
| 日期 | 2024-07-16（PDF ModDate 2024-07-17） |
| 页数 | 515 |
| 本地文件名 | `docs/ODB++_Format_User_Guide.pdf` |
| MD5 | `f57b7264488e8670f81c6b94cb3b9fe1` |
| 官方下载 | <https://odbplusplus.com/wp-content/uploads/sites/2/2024/08/odb_spec_user.pdf> |

### 为什么它不在 git 里

1. **体积**：7.9 MB 二进制，每次修改都会永久占用对象库，且无法 diff。
2. **版权**：该 PDF 由 Siemens 发布，本仓库不是它的分发渠道。

文件**保留在工作区** `docs/` 中正常使用；`.gitignore` 只把它排除出版本历史。
若确实需要入库，执行：

```bash
git add -f docs/ODB++_Format_User_Guide.pdf
```

### 重新获取与校验

```bash
python tools/fetch_spec.py            # 下载到 docs/ 并校验 MD5
python tools/fetch_spec.py --verify   # 只校验现有文件
```

脚本会校验 MD5；若不是上表中的值，说明官方已更新规范，
**必须重新走一遍调研**（单位制、符号表、层子类型都可能变动）。

## `docs/research/`

调研报告目录，索引见 [`research/README.md`](research/README.md)。

## 审阅与修复记录

- [`reviews/`](reviews/README.md)：按时间排列的问题审阅及性能测量。
- [`fixes/`](fixes/README.md)：对应问题的修复与验证记录。

## 本项目对规范的引用约定

文档与代码中引用规范时，一律使用 `p.<PDF 页码>`，例如
「单位双轨制见 p.36 / p.166」。这些页码可通过
`python tools/extract_spec_text.py --page 36 36` 直接复核。
