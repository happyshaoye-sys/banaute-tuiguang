# AI任务策略引擎V2.6修改报告

## 1. 结论

本次仅修复“计划层仍使用单日数据”导致的统计周期不一致，并增加运营可解释性字段。商品策略、渠道策略、商品-计划动作的既有判断规则没有改变；MetoX算法没有改变。

当前本地分支：`release/MetoX2.6`；版本：`2.6`。本次未提交、未打标签、未推送。

## 2. 修改文件

### 业务代码

| 文件 | 变化 |
| --- | --- |
| `core/plan_metrics.py` | 新增完整周期商品×PlanID解析、累计、ROI/CPA重算、Top3展示和覆盖率计算。 |
| `core/merge.py` | 日数据合并时保留全部商品×PlanID明细，不再只留日Top3作为后续周期计算输入。 |
| `core/periods.py` | 按宝贝ID聚合所选7/15/30天后，对全量PlanID做周期累计，再生成Top3；增加30天周期。 |
| `core/task_engine.py` | 读取周期计划结果，增加动作对象、计划覆盖/完整度、AI可信度和三层数据来源。 |
| `app.py` | 向周期聚合传递预期天数、实际天数和日期范围；增加30天选项；AI任务视图展示数据来源、动作对象、覆盖率和可信度。 |

### 版本、门禁和文档

`VERSION.json`、`VERSION.md`、`README.md`、`README_离线使用说明.txt`、`第三方使用说明_先看这个.txt`、`半自动推广工具_数据源与指标说明.md`、`MetoX2.6_新运营培训手册.html`、`scripts/release_check.py`。

### 测试

`test_plan_period_metrics.py`、`test_ai_task_view.py`、`test_version_contract.py`。

工作区中同时存在本任务开始前已有的其他未提交改动；本报告仅列出本次周期统一与可解释性相关改动。

## 3. 变化函数

- `core/merge.py::_promotion_detail_agg`：日粒度保留全量计划记录及日Top3展示。
- `core/periods.py::aggregate_period_frame`：先完整周期聚合，后重算计划指标并生成Top3。
- `core/plan_metrics.py::parse_full_plan_details`
- `core/plan_metrics.py::serialize_full_plan_details`
- `core/plan_metrics.py::format_top_plan_details`
- `core/plan_metrics.py::summarize_plan_records`
- `core/plan_metrics.py::summarize_period_product_plans`
- `core/task_engine.py::_parse_promotion_plans`：解析周期点击等计划指标。
- `core/task_engine.py::_build_product_context`：读取计划总数、展示数、覆盖率、完整度和周期。
- `core/task_engine.py::_build_plan_actions`：输出“商品-计划级”动作对象和数据来源。
- `core/task_engine.py::_source_window`、`_data_source_labels`、`_plan_data_explanation`、`_ai_confidence`：新增解释性计算。
- `core/task_engine.py::_build_task_reason`：说明周期Top3与完整计划数。
- `core/task_engine.py::build_ai_task_view`：附加新增字段，不改变既有策略决策顺序。
- `app.py::load_period_data`：传递完整周期上下文。
- `scripts/release_check.py::check_release`：增加V2.6规则版本门禁。

## 4. 计划统计流程修改前后

### 修改前

```mermaid
flowchart LR
    A[每日推广明细] --> B[每日先选Top3]
    B --> C[周期商品聚合]
    C --> D[推广明细取首个非空值]
    D --> E[计划动作实际依据单日数据]
```

结果：商品和渠道用户选周期，计划层却可能只用某一日的Top3。

### 修改后

```mermaid
flowchart LR
    A[每日推广明细] --> B[保留全部宝贝ID×PlanID数据]
    B --> C[按用户选7/15/30天累计]
    C --> D[汇总花费 归因成交 加购 点击]
    D --> E[重算ROI和CPA]
    E --> F[评估计划效率]
    F --> G[按周期花费排序]
    G --> H[展示Top3关键计划]
```

结果：Top3仅是最后一步的展示方式，不再参与或截断统计口径。

## 5. 三层数据来源

以用户选择近7天为例：

- **商品策略**：近7天商品汇总数据。
- **渠道动作**：近7天商品×渠道数据。
- **计划动作**：近7天商品×PlanID数据（Top3展示）。

选15天或30天时，三层的“近7天”同步替换为“近15天”或“近30天”。

## 6. 新增字段

### AI任务视图

- `动作对象`：固定为“商品-计划级”。
- `计划总数`、`计划展示数`、`计划覆盖率`、`计划数据完整度`、`计划数据说明`。
- `AI可信度`、`AI可信度分`、`AI可信度说明`。
- `商品策略数据源`、`渠道动作数据源`、`计划动作数据源`。

### 计划执行明细

- `动作对象`：“商品-计划级”。
- `执行对象`：“宝贝ID xxx × PlanID xxx”。
- `点击`。
- `数据来源`：例如“近7天商品×PlanID数据”。

### 内部周期字段

- `推广计划全量明细`、`推广计划总数`、`推广计划展示数`。
- `计划覆盖率`、`计划数据完整度`、`计划统计天数`、`计划实际数据天数`。
- `计划数据口径`、`计划数据范围`。

其中计划覆盖率按“Top3周期花费 / 全部计划周期花费”计算。AI可信度只是运营参考，不回流任务类型、商品策略、优先级、渠道动作或计划动作。

## 7. 验证场景和证据

| 场景 | 验证点 | 结果 |
| --- | --- | --- |
| 7天周期 | 5个计划全量累计，同一PlanID跨日汇总，重算ROI/CPA/点击，三层来源均为近7天 | 通过 |
| 15天周期 | 周期中新增计划和中途暂停计划均保留全周期累计，三层来源均为近15天 | 通过 |
| 30天周期 | 花费300、归因成交900、点击120、ROI=3、CPA=5，三层来源均为近30天 | 通过 |
| 一个商品5个计划 | 先累计5个，后展示Top3；计划总数=5、展示数=3 | 通过 |
| Top3展示 | 按周期花费降序选择，非单日Top3；覆盖率基于完整计划池 | 通过 |
| 周期数据不完整 | 降低AI可信度，商品策略、渠道状态/动作和计划摘要不改变 | 通过 |
| 商品、渠道、计划窗口一致 | 7/15/30天数据源标签与计划累计口径一致 | 通过 |
| 商品-计划动作对象 | 明确宝贝ID×PlanID，不解释为共享计划整体动作 | 通过 |
| 版本合同 | VERSION、手册和规则版本门禁一致为2.6 | 通过 |

验证命令与结果：

- `python -m unittest discover`：76个测试全部通过。
- `python scripts/release_check.py --release`：通过，识别`product=MetoX version=2.6`，且分支符合发布规则。
- 未改动`core/metox_algorithm.py`。

## 8. 业务逻辑不变声明

1. 商品策略生成逻辑未修改。
2. 商品定位和商品周期参与策略的方式未修改。
3. 渠道策略生成逻辑未修改。
4. 商品-计划动作的判定逻辑未修改，仅把输入指标从单日统一为所选周期。
5. 同一PlanID下不同商品的动作不做冲突判定。
6. “商品继续经营”与“广告继续投放”仍为两个不同概念。
7. Top3仍保留为产品展示方式，不改变商品级主表和商品-计划级动作定位。
8. `core/metox_algorithm.py`未修改，MetoX标签、分数、原因和规则保持不变。

综上，商品、渠道、计划三层已统一为用户选择的7/15/30天统计窗口；Top3仅影响展示；本次没有改变既有业务判断逻辑。
