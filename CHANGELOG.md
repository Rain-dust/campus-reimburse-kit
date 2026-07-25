# 变更记录

本文件记录用户可见的重要变化。版本号遵循语义化版本约定。

## [Unreleased]

### Added

- 工作区支持重命名，内部 ID 和已有数据保持不变。
- 每个工作区可保存一对兼容的自定义入库单、出库单模板。
- 自定义模板随工作区备份和恢复，并可随时切回内置模板。
- GitHub Issue、Pull Request、安全和开源审查文档。

### Changed

- 模板启用前会检查 10 列表头、可写明细行、合计及金额公式。
- README 更明确地区分适用场景、隐私边界和已知限制。

### Fixed

- Windows CI 中临时目录长路径与 8.3 短路径表示不同导致的测试误报。

## [1.2.0] - 2026-07-25

### Added

- 首个公开 Windows 本地版本。
- PDF 发票导入、字段确认、票据删除和批量确认。
- 多项目额度下的票据组合与人工调整。
- 入库单、出库单和可读命名原始票据导出。
- 离线 PaddleOCR 便携运行环境。
- 工作区创建、删除、备份和恢复。

[Unreleased]: https://github.com/Rain-dust/campus-reimburse-kit/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/Rain-dust/campus-reimburse-kit/releases/tag/v1.2.0
