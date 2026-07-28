# 半自动推广工具 v4.1

应用版本：v4.1

外部策略算法版本：v9.3（保持不变）

这是可直接下载使用的离线版半自动推广工具。仓库中已经包含：

- `TuiGuangTool.exe`
- `_internal/` 离线运行环境
- `app.py`
- `core/` 核心逻辑
- `templates/`
- `.streamlit/config.toml`
- `启动半自动推广工具.bat`

## v4.1 / MetoX1.1

- 修复选择“当日、3天、15天”时品类分析固定读取“7日环比”导致的 KeyError。
- “7天GMV”和“7日GMV环比”按完整的当前7日与上一7日品类数据汇总，不再只覆盖本期仍存在的商品ID。
- 固定7日品类汇总复用已加载日数据，不增加文件读取。

## 新电脑直接使用

1. 在 GitHub 页面点击 `Code` -> `Download ZIP`。
2. 完整解压 ZIP，不要在压缩包里直接双击文件。
3. 进入解压后的文件夹。
4. 双击 `启动半自动推广工具.bat`。
5. 浏览器打开 `http://127.0.0.1:8765` 后即可使用。
6. 使用过程中不要关闭黑色启动窗口，关闭窗口工具会停止。

## 数据要求

工具本身可以离线启动，但分析数据仍需要来源：

- 默认读取共享盘数据路径，例如 `\\10.1.22.253\天猫mark`。
- 新电脑必须能访问共享盘，或在工具侧边栏手动选择本地数据目录。
- 聚水潭库存/退款快照没有放进仓库，因为本地快照目录体积约 6GB，不适合提交到 GitHub。
- 如果没有聚水潭快照，退款率和在仓库存相关列可能为空，其余功能仍可继续使用。

## 源码运行方式

如果不用离线 EXE，也可以用 Python 源码方式运行：

```powershell
pip install -r requirements.txt
streamlit run app.py
```

或指定本机 Python：

```powershell
D:\python\python.exe -m pip install -r requirements.txt
D:\python\python.exe -m streamlit run app.py
```

## v9.3 相关核心文件

- `core/v93_algorithm.py`：内置 v9.3 算法命中逻辑。
- `core/v93_signal.py`：v9.3 日报信号解析。
- `core/schema.py`：字段别名、导出字段、分层矩阵。
- `core/merge.py`：商品、推广、库存、退款数据合并。
- `core/periods.py`：周期聚合与环比。
- `core/loader.py`：源文件加载与缓存。

## 校验命令

```powershell
D:\python\python.exe -m compileall app.py core _regression_v93_algorithm.py _regression_export_period.py
D:\python\python.exe _regression_v93_algorithm.py
D:\python\python.exe _regression_export_period.py
```
