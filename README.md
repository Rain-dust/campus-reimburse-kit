# Campus Reimburse Kit

[![Tests](https://github.com/Rain-dust/campus-reimburse-kit/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/Rain-dust/campus-reimburse-kit/actions/workflows/tests.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078D4)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)

**Campus Reimburse Kit（逐曦报账辅助工具）** 是一款本地优先的 Windows
桌面工具，用于整理电子发票、按项目额度组合票据，并生成入库单、出库单和
可读命名的原始票据。

项目默认不上传票据，不要求付费 API。电子 PDF 优先使用本地文本与坐标解析；
扫描 PDF 可选用本地 PaddleOCR。识别失败不会阻塞流程，所有结果都可以人工确认。

> [!IMPORTANT]
> 本项目生成的是报销材料草稿，不是财务记账系统，也不代表学校或其他单位的
> 官方报销规则。金额、票据明细、模板、签字和盖章要求必须由使用者最终核对。

## 适用场景

适合：

- 高校学生团队、实验室或社团整理材料采购发票。
- 多个项目额度并存，需要把票据分组且每组金额不超过额度。
- 需要按照固定 Excel 样式准备入库单、出库单和原始票据。
- 不希望上传发票或为云 OCR、AI API 持续付费。

不适合：

- 多人在线协作、审批流、会计凭证或税务申报。
- 无需人工复核的全自动报销。
- 直接作为学校正式制度、审计结论或法律依据。

## 主要功能

- 分批导入多份 PDF，后续批次不会覆盖已有票据。
- 提取并核对日期、价税合计、供应商、发票号和商品明细。
- 一键确认全部票据，也可逐张修改或删除错误票据。
- 按多个项目额度组合票据，每张票据只分配一次且不超过额度。
- 从发票明细生成可编辑的入库、出库草稿。
- 默认使用内置 Excel 模板，也可为单个工作区导入兼容模板。
- 导出包仅包含 `入库单.xlsx`、`出库单.xlsx` 和 `原始票据/`。
- 工作区可重命名、备份和恢复；数据默认只保存在本机。

## 支持范围

| 项目 | 当前支持 |
| --- | --- |
| 操作系统 | Windows 10/11 x64 |
| 票据输入 | PDF；暂不导入 JPG/PNG 等付款截图 |
| 电子 PDF | 本地文本与坐标解析 |
| 扫描 PDF | 可选本地 PaddleOCR |
| 付费 API | 不需要 |
| Excel 模板 | 内置模板或兼容的 `.xlsx` 模板对 |
| 数据存储 | 本机工作区，无默认云同步 |
| 使用方式 | 本地单用户六步向导 |

## 快速开始

### 使用便携包

完整解压压缩包后，双击：

```text
RMReimbursementMaterials.exe
```

不要只复制或运行单独的 EXE；`_internal` 目录包含运行库、OCR 模型和内置模板。
当前便携版未进行代码签名，Windows 可能显示来源提醒，请只从可信发布页下载并
核对发布页提供的 SHA256。

### 从源码运行

需要 Python 3.10 或更高版本：

```bat
git clone https://github.com/Rain-dust/campus-reimburse-kit.git
cd campus-reimburse-kit
scripts\setup_windows.bat
scripts\run_windows.bat
```

未准备 PaddleOCR 模型时，源码模式会使用人工确认流程，不会伪造 OCR 结果。

## 六步工作流

1. 选择内置模板，或为当前工作区导入一对兼容模板。
2. 分批导入 PDF 票据。
3. 核对并确认票据基础信息。
4. 设置一个或多个项目报销额度。
5. 自动分配票据并核对入、出库明细。
6. 检查后导出材料包。

详细操作见 [便携版使用教程](docs/PORTABLE_USER_GUIDE.md)。

## 自定义模板

向导第 1 步可分别导入入库单、出库单 `.xlsx` 文件。程序会检查：

- 关键 10 列的名称和顺序。
- 明细行是否可写且不存在冲突的合并单元格。
- 合计行、金额公式及大写金额公式。

只有全部通过才会切换。模板标题、校徽、字体和常规样式可以不同，但当前版本
不支持任意列映射、`.xls`、带宏模板或明细区域合并单元格。

通过检查的模板会复制到当前工作区并随备份保存；原文件移动后仍可使用，在另一台
电脑恢复备份时也会自动修正路径。无效模板不会覆盖当前可用模板。

仓库暂时内置湘潭大学教学科研易耗品和材料入库、出库单示例。它们保留了示例
采购内容和原始文档属性，仅用于兼容当前工作流，不代表学校官方维护版本，也不
保证适用于其他单位。使用前必须自行确认表头、示例行、打印、签字和盖章要求。

## 数据、隐私与安全

- `.env`、数据库、工作区、票据、导出文件、OCR 模型和构建产物均已忽略。
- 不要在 Issue、日志、截图或测试数据中提交真实发票、付款记录、密钥和个人信息。
- OCR 文本及金额不可直接信任，报销前必须人工核对。
- 自定义 Excel 模板属于不可信输入，只使用来源可靠的文件。
- 本工具只应在本机使用，不要把本地服务直接暴露到公网。

安全问题请阅读 [SECURITY.md](SECURITY.md)，并通过 GitHub Security Advisory
私下报告。

## 已知限制

- 当前只支持 PDF 票据导入。
- 扫描件、模糊照片转 PDF 和复杂票面可能仍需人工补录。
- 商品名称、规格、数量和单价依赖原始票面质量，无法保证完全自动匹配。
- 自定义模板必须兼容当前 10 列结构，尚不支持可视化列映射。
- 当前是本地单用户工具，不包含飞书审批、云同步和多人权限。
- 内置模板仍包含示例内容，导出后必须检查示例行是否已正确替换。

## 开发与测试

基础检查：

```bat
scripts\check_windows.bat
```

等价命令：

```bat
.venv\Scripts\python.exe -m compileall app core tools materials_desktop.py run_materials_desktop.py
.venv\Scripts\python.exe -m unittest discover -s tests
```

可选安装本地 OCR：

```bat
scripts\install_local_ocr.bat
```

构建和验证 Windows 便携目录：

```bat
scripts\build_materials_portable.bat
scripts\verify_materials_portable.bat
```

## 参与项目

- [贡献指南](CONTRIBUTING.md)
- [行为准则](CODE_OF_CONDUCT.md)
- [变更记录](CHANGELOG.md)
- [安全策略](SECURITY.md)
- [开源审查记录](docs/OPEN_SOURCE_AUDIT.md)

提交公开反馈前，请先移除票据中的姓名、电话、地址、订单号、发票号和付款信息。

## 来源与许可证

本项目基于 [chiupam/InvoiceOCR](https://github.com/chiupam/InvoiceOCR) 的
Apache-2.0 代码改造，现已聚焦为离线报销材料桌面工具。详细归属见
[NOTICE](NOTICE)。

项目代码按 [Apache License 2.0](LICENSE) 发布。内置模板的适用性和使用授权
需要使用者根据所在单位要求自行确认。
