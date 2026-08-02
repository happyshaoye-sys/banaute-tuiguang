# MetoX v2.1 半自动推广工具

应用版本：MetoX v2.1

外部策略算法版本：v9.3（独立版本，本次未变更）

## v2.1 核心变更

- 三个启动入口统一使用新版启动器。
- 默认尝试端口 `8765`；若已被其他程序占用，会自动选择后续可用端口。
- 启动窗口会显示实际访问地址，不再要求用户手工设置 `TGTOOL_PORT`。

## 延续的退款规则（v2.0）

- 聚水潭 30 日 cohort 退款率 `>80%` 标记「极高退款」。
- `>70%且≤80%` 标记「高退款」。
- 两档均将「放量/适当放量」降为「待优化」，`>80%` 只保留极高退款标签。
- 页面与 Excel 退款风险入选线统一为严格 `>70%`。
- `core/__init__.py` 改为引用 `core/segmentation.py`，不再保留第二份分层算法。

## 新电脑直接使用

1. 完整解压正式交付 ZIP，不要在压缩包中直接运行。
2. 双击 `启动MetoX半自动推广工具.bat`。
3. 浏览器会自动打开；实际地址以启动窗口中的 `METOX_URL=` 为准。
4. 使用期间不要关闭启动窗口。

## 版本治理

- `VERSION.json` 是唯一版本源。
- `core/version.py` 从唯一版本源派生页面显示版本。
- `scripts/release_check.py` 检查业务规则变更是否升版，并校验页面、文档、分支和阈值契约。
- `scripts/release_metox.py` 先执行发布检查，再生成带版本和日期的不可覆盖目录与 ZIP。
- Git 发布标签使用 `MetoX<版本>`，例如 `MetoX2.1`。

## 核心文件

- `core/metox_algorithm.py`：MetoX 内置算法。
- `core/v93_signal.py`：v9.3 日报信号解析。
- `core/segmentation.py`：36 宫格、风险标签、动作和优先级。
- `core/analysis.py` / `core/exporter.py`：页面和 Excel 分析口径。
- `core/merge.py` / `core/periods.py`：数据合并和周期聚合。

## 验证命令

```powershell
python -m unittest discover -p "test_*.py"
python scripts/release_check.py --release
```

先重建启动器，再指定已验收的离线运行包和新启动器运行时：

```powershell
python scripts/build_launcher.py
python scripts/release_metox.py --baseline-package <已验收包目录> --launcher-runtime dist\TuiGuangTool --output-root <交付根目录>
```
