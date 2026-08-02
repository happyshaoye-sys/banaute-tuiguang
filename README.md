# MetoX v2.0 半自动推广工具

应用版本：MetoX v2.0

外部策略算法版本：v9.3（独立版本，本次未变更）

## v2.0 核心变更

- 聚水潭 30 日 cohort 退款率 `>80%` 标记「极高退款」。
- `>70%且≤80%` 标记「高退款」。
- 两档均将「放量/适当放量」降为「待优化」，`>80%` 只保留极高退款标签。
- 页面与 Excel 退款风险入选线统一为严格 `>70%`。
- `core/__init__.py` 改为引用 `core/segmentation.py`，不再保留第二份分层算法。

## 新电脑直接使用

1. 完整解压正式交付 ZIP，不要在压缩包中直接运行。
2. 双击 `启动MetoX半自动推广工具.bat`。
3. 浏览器打开 `http://127.0.0.1:8765`后使用。
4. 使用期间不要关闭启动窗口。

## 版本治理

- `VERSION.json` 是唯一版本源。
- `core/version.py` 从唯一版本源派生页面显示版本。
- `scripts/release_check.py` 检查业务规则变更是否升版，并校验页面、文档、分支和阈值契约。
- `scripts/release_metox.py` 先执行发布检查，再生成带版本和日期的不可覆盖目录与 ZIP。
- Git 发布标签使用 `MetoX<版本>`，例如 `MetoX2.0`。

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

正式打包需指定已验收的离线运行包作为运行时基线：

```powershell
python scripts/release_metox.py --baseline-package <已验收包目录> --output-root <交付根目录>
```
