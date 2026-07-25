# Campus Reimburse Kit

高校报销材料助手是一款本地运行的 Windows 桌面工具，用于整理电子发票、按项目额度组合票据，并生成入库单、出库单和重命名后的原始票据。

项目默认不上传票据，不要求付费 API。电子 PDF 优先使用本地文本与坐标解析；扫描 PDF 可选用本地 PaddleOCR，识别失败时仍允许人工确认。

## 主要功能

- 分批导入多份 PDF，后续批次不会覆盖已有票据。
- 提取并核对日期、价税合计、供应商和发票号。
- 按多个项目额度组合票据，每张票据只分配一次且不超过额度。
- 从发票明细生成可编辑的入库、出库草稿。
- 默认使用内嵌 Excel 模板，也可为单个工作区导入兼容模板。
- 导出包仅包含 `入库单.xlsx`、`出库单.xlsx` 和 `原始票据/`。
- 工作区支持重命名；工作区、票据、模板副本和导出结果默认仅保存在本机。

## 模板说明

当前仓库暂时内嵌湘潭大学教学科研易耗品和材料入库、出库单模板，并保留了模板中的示例数据。它们不代表其他学校或单位的正式报销规范。内置模板仍是默认选项，不需要每次上传。

其他单位可以在向导第 1 步分别导入入库单、出库单 `.xlsx` 文件。程序会先检查关键 10 列的名称和顺序、明细行是否可写、合计行、金额公式及大写金额公式；只有兼容性检查全部通过才会切换。模板标题、校徽、字体和常规样式可以不同，但当前版本不支持任意列映射、明细区域合并单元格、`.xls` 或带宏模板。

通过检查的自定义模板会复制到当前工作区：

- 原模板被移动或删除后，当前工作区仍可使用。
- 下载工作区备份时会包含模板副本。
- 在另一台电脑恢复备份时会自动改写模板路径。
- 无效模板不会覆盖当前正在使用的模板。

使用前请自行确认：

- 本单位是否允许使用该模板。
- 表头、签字、盖章及打印要求是否仍有效。
- 示例行是否已在最终导出文件中被正确替换。

自定义模板只改变 Excel 输出格式，不会提高或降低票据 OCR 的识别精度。

## Windows 快速运行

需要 Python 3.10 或更高版本。

```bat
scripts\setup_windows.bat
scripts\run_windows.bat
```

程序会打开本地窗口。源代码模式未准备 PaddleOCR 模型时，会自动使用人工确认模式，不会伪造识别结果。

运行检查：

```bat
scripts\check_windows.bat
```

## 可选本地 OCR

PaddleOCR 不是基础运行依赖。Windows CPU 环境可执行：

```bat
scripts\install_local_ocr.bat
```

安装过程需要从 PaddlePaddle 和 PyPI 下载较大的依赖及模型。项目不提交 OCR 模型文件，也不要求腾讯云或其他付费 API。

## 构建便携版

构建机需要先安装本地 OCR 依赖并准备完整模型缓存：

```bat
scripts\build_materials_portable.bat
scripts\verify_materials_portable.bat
```

输出目录为：

```text
dist\RMReimbursementMaterials\
```

请分发整个目录或其压缩包，不要只发送 EXE。

## 数据与隐私

- `.env`、数据库、工作区、上传票据、导出文件和 OCR 模型均已加入忽略规则。
- 不要在 Issue、日志或测试数据中提交真实发票、付款截图、密钥及个人信息。
- 公开反馈问题时请使用脱敏样例。

## 测试

```bat
.venv\Scripts\python.exe -m compileall app core tools materials_desktop.py run_materials_desktop.py
.venv\Scripts\python.exe -m unittest discover -s tests
```

## 项目来源

本项目基于 [chiupam/InvoiceOCR](https://github.com/chiupam/InvoiceOCR) 的 Apache-2.0 代码改造，现已聚焦为离线报销材料桌面工具。详细归属见 [NOTICE](NOTICE)。

## 许可证

使用 [Apache License 2.0](LICENSE)。提交贡献即表示你有权提交相关代码或素材，并同意按该许可证发布。

## 文档

- [详细使用教程](docs/PORTABLE_USER_GUIDE.md)
- [贡献指南](CONTRIBUTING.md)
- [安全说明](SECURITY.md)
