# 贡献指南

感谢你改进 Campus Reimburse Kit。项目优先保证现有票据整理流程可靠、可核对，
再考虑扩大功能范围。

## 项目原则

- 本地优先：默认不上传票据，不强制使用付费 API。
- 人工可控：OCR 或自动匹配失败时必须允许用户核对和修改。
- 最小数据：不收集完成当前流程不需要的信息。
- 输出可审计：金额、票据分组和 Excel 明细必须能追溯到原始输入。
- 兼容优先：不要为了重构破坏已有工作区、模板或导出格式。

## 提交 Issue 前

1. 搜索现有 Issue，确认问题没有重复。
2. 使用最新 `main` 或最新公开版本复现。
3. 删除截图、PDF、日志中的真实姓名、电话、地址、订单号、发票号和付款信息。
4. 安全漏洞不要提交公开 Issue，请按 [SECURITY.md](SECURITY.md) 私下报告。

## 开始开发

```bat
git clone https://github.com/Rain-dust/campus-reimburse-kit.git
cd campus-reimburse-kit
scripts\setup_windows.bat
scripts\check_windows.bat
```

PaddleOCR 是可选依赖。大多数业务逻辑和路由测试不需要安装本地 OCR。

## 修改要求

- 测试样例必须脱敏，供应商、发票号和人员信息应使用虚构内容。
- 不要提交真实发票、付款记录、数据库、工作区、导出包、OCR 模型或云服务密钥。
- 修改 Excel 模板时，必须说明来源、授权、适用单位及打印布局影响。
- 修改 OCR 解析时，同时提供失败样例和不会误填字段的回归测试。
- 修改导出逻辑时，核对表头、公式、金额合计、打印布局和签章区域。
- 只做与当前问题有关的最小改动，避免无关格式化和大规模重构。

## 提交前检查

```bat
.venv\Scripts\python.exe -m compileall app core tools materials_desktop.py run_materials_desktop.py
.venv\Scripts\python.exe -m unittest discover -s tests
git diff --check
git status
```

请确认：

- 编译检查和全部测试通过。
- 新行为有相应测试，错误路径不会破坏工作区。
- 没有新增票据、数据库、模型、密钥或构建产物。
- 用户可见行为和限制已同步到 README 或使用教程。

## Pull Request

PR 应说明：

- 要解决的问题及复现方式。
- 实现方式和有意保留的限制。
- 验证命令与结果。
- 对旧工作区、模板、OCR 和导出的兼容性影响。

提交贡献即表示你有权提交相关代码或素材，并同意按项目的
[Apache License 2.0](LICENSE) 发布。
