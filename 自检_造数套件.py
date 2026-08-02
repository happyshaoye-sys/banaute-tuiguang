# -*- coding: utf-8 -*-
"""造数自检套件(验收门禁 T1/T3/T4/T5a)。

人工构造 5+3 商品×4 数据源,期望值全部手算,过真实管线逐项断言:
  T1  公式正确性(合并/比率/分层/风险/分析/周期/滚动/v9.3)
  T3  同一款多规则命中正确合并(多计划/多渠道/多风险标签/多信号/动量×退款/同款多链接)
  T4  零动销商品不被过滤
  T5a 分界点配置修改后分层正确变化

用法: python -X utf8 自检_造数套件.py   (退出码 0=全过, 1=有失败)
离线可跑,不依赖任何真实数据文件。
"""
import io
import math
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warnings

warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

from core.merge import merge_sources
from core.periods import (aggregate_period_frame, apply_period_comparisons,
                          apply_rolling_daily_metrics, build_period_ranges)
from core.segmentation import segment_products
from core.analysis import (calc_kpis, segment_summary, category_analysis,
                           refund_risk, channel_top, inventory_actions)
from core.schema import to_num, classify_channel, safe_div
from core.v93_signal import parse_v93_report

PASS, FAIL = 0, []


def check(name, actual, expected, tol=1e-6):
    global PASS
    if isinstance(expected, float) and isinstance(actual, (int, float, np.floating)):
        if math.isnan(expected):
            ok = isinstance(actual, (float, np.floating)) and math.isnan(float(actual))
        else:
            ok = abs(float(actual) - expected) <= tol
    else:
        ok = actual == expected
    if ok:
        PASS += 1
    else:
        FAIL.append(f"{name}: 期望 {expected!r}, 实得 {actual!r}")


# ══ 0. 基础工具函数 ══
check("to_num 千分位", to_num("1,234.5"), 1234.5)
check("to_num 空串", to_num("--"), 0.0)
check("to_num 百分号(不除100,已知语义)", to_num("5%"), 5.0)
check("classify_channel 关键词推广", classify_channel("关键词推广"), "关键词")
check("classify_channel 精准人群推广", classify_channel("精准人群推广"), "人群")
check("classify_channel 货品全站推广", classify_channel("货品全站推广"), "全站")
check("classify_channel 店铺直达", classify_channel("店铺直达"), "店铺直达")
check("classify_channel 未知场景兜底→关键词", classify_channel("超级直播"), "关键词")
check("safe_div 除0", safe_div(5, 0), 0.0)

# ══ 1. 合成源数据(期望值全部手算) ══
# 1001 常规品(两计划两渠道+1天干扰行) / 1002 高流量低价值 / 1003 仅无界(亏损投放)
# 1005 成本缺失 / 436273237 店铺主体(P0-1:应被 merge 按主体类型剔除) / 2001 零动销(全0) / 2002+2003 同款多链接多规则
product = pd.DataFrame([
    {"宝贝ID": "1001", "GMV": 1000.0, "商品访客数": 100, "支付买家数": 10, "支付件数": 20,
     "商品加购件数": 50, "成功退款金额": 100.0, "淘宝类目": "连衣裙", "商品名称": "P1",
     "统计日期": "2026-07-09", "推广消耗金额": 10.0},
    {"宝贝ID": "1002", "GMV": 500.0, "商品访客数": 1000, "支付买家数": 5, "支付件数": 10,
     "商品加购件数": 20, "成功退款金额": 0.0, "淘宝类目": "连衣裙", "商品名称": "P2", "统计日期": "2026-07-09"},
    {"宝贝ID": "1005", "GMV": 200.0, "商品访客数": 400, "支付买家数": 2, "支付件数": 4,
     "商品加购件数": 8, "成功退款金额": 0.0, "淘宝类目": "半身裙", "商品名称": "P5", "统计日期": "2026-07-09"},
    # T4: 零动销品(有行但全 0)
    {"宝贝ID": "2001", "GMV": 0.0, "商品访客数": 0, "支付买家数": 0, "支付件数": 0,
     "商品加购件数": 0, "成功退款金额": 0.0, "淘宝类目": "T恤", "商品名称": "零动销", "统计日期": "2026-07-09"},
    # T3: 同款两链接(共享货号 SKU9) — 2002 高退款+缺货+高ROI 多标签; 2003 衰退+动量
    {"宝贝ID": "2002", "GMV": 8000.0, "商品访客数": 4000, "支付买家数": 60, "支付件数": 80,
     "商品加购件数": 200, "成功退款金额": 500.0, "淘宝类目": "毛衣", "商品名称": "多规则A", "统计日期": "2026-07-09"},
    {"宝贝ID": "2003", "GMV": 3500.0, "商品访客数": 5000, "支付买家数": 30, "支付件数": 40,
     "商品加购件数": 100, "成功退款金额": 0.0, "淘宝类目": "毛衣", "商品名称": "多规则B", "统计日期": "2026-07-09"},
])
wujie = pd.DataFrame([
    {"宝贝ID": "1001", "转化周期": "15天转化", "场景名字": "关键词推广", "计划ID": "A",
     "付费": 100.0, "GMV": 300.0, "推广投产": 3.0, "展现": 1000, "点击": 100, "商品加购件数": 30},
    {"宝贝ID": "1001", "转化周期": "15天转化", "场景名字": "精准人群推广", "计划ID": "B",
     "付费": 50.0, "GMV": 0.0, "推广投产": 2.0, "展现": 0, "点击": 0, "商品加购件数": 5},
    {"宝贝ID": "1001", "转化周期": "1天转化", "场景名字": "关键词推广", "计划ID": "A",
     "付费": 999.0, "GMV": 999.0, "推广投产": 1.0, "展现": 9, "点击": 9, "商品加购件数": 9},
    {"宝贝ID": "1003", "转化周期": "15天转化", "场景名字": "全站推广", "计划ID": "C",
     "付费": 120.0, "GMV": 0.0, "推广投产": 0.0, "展现": 500, "点击": 40, "商品加购件数": 2},
    {"宝贝ID": "436273237", "转化周期": "15天转化", "场景名字": "店铺直达", "计划ID": "D",
     "付费": 60.0, "GMV": 0.0, "推广投产": 5.0, "展现": 200, "点击": 80, "商品加购件数": 0,
     "主体类型": "店铺"},
    # T3: 2002 同一款四渠道四计划(渠道分列+明细TOP3按花费排序)
    {"宝贝ID": "2002", "转化周期": "15天转化", "场景名字": "关键词推广", "计划ID": "K1",
     "付费": 300.0, "GMV": 3000.0, "推广投产": 10.0, "展现": 3000, "点击": 300, "商品加购件数": 60},
    {"宝贝ID": "2002", "转化周期": "15天转化", "场景名字": "精准人群推广", "计划ID": "K2",
     "付费": 200.0, "GMV": 1000.0, "推广投产": 5.0, "展现": 2000, "点击": 150, "商品加购件数": 30},
    {"宝贝ID": "2002", "转化周期": "15天转化", "场景名字": "货品全站推广", "计划ID": "K3",
     "付费": 150.0, "GMV": 0.0, "推广投产": 4.0, "展现": 1000, "点击": 80, "商品加购件数": 20},
    {"宝贝ID": "2002", "转化周期": "15天转化", "场景名字": "店铺直达", "计划ID": "K4",
     "付费": 50.0, "GMV": 200.0, "推广投产": 4.0, "展现": 500, "点击": 40, "商品加购件数": 10},
])
# 真实无界导出每行都带"主体类型";合成数据仅假商品显式标"店铺",其余默认"商品"
# (P0-1 修复后 merge 按此列剔除店铺主体品)。
wujie["主体类型"] = wujie["主体类型"].fillna("商品")
mapping = pd.DataFrame([
    {"宝贝ID": "1001", "货号": "SKU1", "创建时间": "2026-01-01", "成本价（含税）": 20.0},
    {"宝贝ID": "1002", "货号": "SKU2", "创建时间": "2026-02-01", "成本价（含税）": 30.0},
    {"宝贝ID": "1005", "货号": "SKU5", "创建时间": "2026-03-01"},
    {"宝贝ID": "2002", "货号": "SKU9", "创建时间": "2026-04-01", "成本价（含税）": 40.0},
    {"宝贝ID": "2003", "货号": "SKU9", "创建时间": "2026-04-01", "成本价（含税）": 40.0},
])
refund = pd.DataFrame([
    {"宝贝ID": "1001", "退款率": 0.5},
    {"宝贝ID": "1002", "退款率": 0.3},
    {"宝贝ID": "2002", "退款率": 0.45},
])
stock = pd.DataFrame([
    {"货号": "SKU1", "在仓库存": 120.0, "在途": 0.0},
    {"货号": "SKU1", "在仓库存": 80.0, "在途": 10.0},
    {"货号": "SKU2", "在仓库存": 60.0, "在途": 0.0},
    {"货号": "SKU9", "在仓库存": 5.0, "在途": 20.0},   # 多链接共享同一低库存池
])

sources = {
    "商品-经营投产比": [("p.xlsx", product)],
    "无界-商品主体": [("w.xlsx", wujie)],
    "商品资料": [("m.csv", mapping), ("r.csv", refund)],
    "库存": [("s.csv", stock)],
}
df, plan_ids = merge_sources(sources)
df = df.set_index("宝贝ID", drop=False)

# ── 行骨架/T4 ──
check("商品池=两源并集(店铺主体436273237已剔除)", sorted(df.index),
      ["1001", "1002", "1003", "1005", "2001", "2002", "2003"])
check("T4 零动销品在表(未被过滤)", "2001" in df.index, True)
check("T4 仅无界侧品在表", "1003" in df.index, True)
check("计划集合(D随店铺主体剔除)", sorted(plan_ids), ["A", "B", "C", "K1", "K2", "K3", "K4"])

r1 = df.loc["1001"]
check("付费=15天行求和(1天行剔除)", r1["付费"], 150.0)
check("MetoX推广花费=无界15天付费而非商品表推广消耗", r1["MetoX推广花费"], 150.0)
check("归因成交=实测300+回退100", r1["推广成交金额"], 400.0)
check("渠道:关键词", r1["关键词"], 100.0)
check("渠道:人群", r1["人群"], 50.0)
check("关键词归因成交", r1["关键词归因成交"], 300.0)
check("人群归因成交(回退花费×ROI)", r1["人群归因成交"], 100.0)
check("推广投产=400/150", r1["推广投产"], 400 / 150)
check("链接投产=1000/150", r1["链接投产"], 1000 / 150)
check("退款率挂载", r1["退款率"], 0.5)
check("推广实际投产=×(1-0.5)", r1["推广实际投产"], 400 / 150 * 0.5)
check("UV价值=1000/100", r1["访客平均价值"], 10.0)
check("支付转化率=10/100", r1["商品支付转化率"], 0.1)
check("件单价=1000/20", r1["件单价"], 50.0)
check("利润率=(50-20)/50", r1["利润率"], 0.6)
check("预估利润=(1000-100)×0.6", r1["预估利润"], 540.0)
check("倍率=50/20", r1["倍率"], 2.5)
check("购物车价值=1000/50", r1["购物车价值"], 20.0)
check("点击率=100/1000", r1["点击率"], 0.1)
check("货号", r1["货号"], "SKU1")
check("在仓库存=预聚合款式快照首个正值", r1["在仓库存"], 120.0)
check("在途=首个正数", r1["在途"], 10.0)
check("创建时间", r1["创建时间"], "2026-01-01")

check("成本缺失→利润率=100%(已知风险P2-1)", df.loc["1005", "利润率"], 1.0)
check("退款未覆盖→NaN 非0", float(df.loc["1005", "退款率"]), float("nan"))
check("仅无界品 GMV=0", df.loc["1003", "GMV"], 0.0)
check("仅无界品 推广投产=0(真亏损非缺数)", df.loc["1003", "推广投产"], 0.0)
check("店铺主体已剔除(P0-1已修复)", "436273237" in df.index, False)

# ── T3: 多计划/多渠道合并 ──
r2 = df.loc["2002"]
check("T3 四渠道花费分列", [r2["关键词"], r2["人群"], r2["全站"], r2["店铺直达"]],
      [300.0, 200.0, 150.0, 50.0])
check("T3 四渠道花费之和=总付费", r2["付费"], 700.0)
check("T3 归因成交=3000+1000+回退600+200", r2["推广成交金额"], 4800.0)
check("T3 全站归因成交=回退150×4", r2["全站归因成交"], 600.0)
det = str(r2["推广明细"]).splitlines()
check("T3 推广明细=3行(TOP3计划)", len(det), 3)
check("T3 明细第1行=花费最大K1", "K1" in det[0] and "花费:300.00" in det[0], True)
check("T3 明细第2行=K2", "K2" in det[1], True)
check("T3 零动销品各值=0", [df.loc["2001", c] for c in ["GMV", "付费", "商品访客数"]],
      [0.0, 0.0, 0.0])

# ── T3: 同款多链接共享库存 ──
check("T3 同款两链接货号一致", [df.loc["2002", "货号"], df.loc["2003", "货号"]], ["SKU9", "SKU9"])
check("T3 两链接共享款式库存=5", [df.loc["2002", "在仓库存"], df.loc["2003", "在仓库存"]], [5.0, 5.0])

# ══ 2. 分层+36宫格+多风险标签(固定分界点) ══
TB, VB = [50, 150, 400, 1500, 3000], [0.5, 1.5, 3, 4.5, 6]
# 挂动量列:2003 日销3>日销7 触发上升验证;2002 不触发
base = df.reset_index(drop=True).copy()
base["日销3"] = base["宝贝ID"].map({"2003": 8.0}).fillna(0.0)
base["日销7"] = base["宝贝ID"].map({"2003": 5.0}).fillna(0.0)
seg = segment_products(base, TB, VB).set_index("宝贝ID", drop=False)

exp_seg = {
    "1001": ("待改善", "明星款", "C", "待改善", "加预算"),
    "1002": ("冲刺款", "滞销款", "C", "待改善", "待优化"),
    "1003": ("滞销款", "滞销款", "D", "滞销款", "观察期"),
    "1005": ("潜力款", "滞销款", "C", "待改善", "待优化"),
    "2001": ("滞销款", "滞销款", "D", "滞销款", "观察期"),   # T4: 零动销正常分层
    "2002": ("金牛款", "潜力款", "A", "冲刺款", "成长期"),   # 访客4000∈(3000,∞)=金牛? 4000>3000→明星款!
}
# 修正手算: 4000>3000 → 明星款; uv=2.0∈(1.5,3] → 潜力款; 明星款潜力款=A/冲刺款/待优化
exp_seg["2002"] = ("明星款", "潜力款", "A", "冲刺款", "待优化")
# 2003: 访客5000→明星款; uv=0.7∈(0.5,1.5]→待改善; 明星款待改善=B/潜力款/衰退预警→动量改上升验证
for pid, (t, v, g, pos, act) in exp_seg.items():
    row = seg.loc[pid]
    check(f"{pid} 流量定位", row["流量定位"], t)
    check(f"{pid} 价值定位", row["价值定位"], v)
    check(f"{pid} 综合等级", row["综合等级"], g)
    check(f"{pid} 商品定位", row["商品定位"], pos)
    check(f"{pid} 对应操作", row["对应操作"], act)
r3_ = seg.loc["2003"]
check("2003 动量改标上升验证", r3_["对应操作"], "上升验证")

# T3: 多风险标签共存于同一款
tags2002 = str(seg.loc["2002", "风险标签"])
check("T3 2002 退款率0.45不触发高退款", "⚠高退款" in tags2002, False)
check("T3 2002 📦库存不足(5<10)", "📦库存不足" in tags2002, True)
check("T3 2002 🚚在途补货中", "🚚在途补货中" in tags2002, True)
check("T3 2002 无🚨(0.45≤0.5)", "🚨" not in tags2002, True)
check("T3 2002 库存两标签并存", tags2002.count(" ") >= 1, True)
tags2003 = str(seg.loc["2003", "风险标签"])
check("T3 2003 📈动量↑与📦并存", "📈动量↑" in tags2003 and "📦库存不足" in tags2003, True)
check("1001 退款率0.5不触发高退款", "⚠高退款" in str(seg.loc["1001", "风险标签"]), False)
check("1003 💸亏损投放", "💸亏损投放" in str(seg.loc["1003", "风险标签"]), True)
check("1003 👁流量低迷", "👁流量低迷" in str(seg.loc["1003", "风险标签"]), True)
check("1001 优先级P4(高退款)", seg.loc["1001", "预算优先级"], "P4")

# ══ 3. T5a 配置修改→分层正确变化 ══
TB2 = [10, 150, 400, 1500, 8000]   # 改两端: 2001访客0仍滞销;1001访客100→待改善不变;2002访客4000→金牛(≤8000)
seg2 = segment_products(df.reset_index(drop=True), TB2, VB).set_index("宝贝ID")
check("T5a 分界点变更后 2002 明星→金牛", seg2.loc["2002", "流量定位"], "金牛款")
check("T5a 4000≤8000 边界正确", seg2.loc["2003", "流量定位"], "金牛款")
check("T5a 组合联动(金牛款潜力款→A/冲刺款)", seg2.loc["2002", "综合等级"], "A")
check("T5a 未跨界品不变(1003 仍滞销)", seg2.loc["1003", "流量定位"], "滞销款")
VB2 = [0.1, 0.3, 3, 4.5, 6]        # 价值分界点下移: 2003 uv0.7→潜力款(≤3)
seg3 = segment_products(df.reset_index(drop=True), TB, VB2).set_index("宝贝ID")
check("T5a 价值分界点变更 2003 待改善→潜力款", seg3.loc["2003", "价值定位"], "潜力款")

# ══ 4. KPI/分析函数 ══
seg["v9.3信号"] = ""
kpis = calc_kpis(seg, plan_ids)
check("KPI GMV", kpis["GMV"], 13200.0)
check("KPI 花费(剔除店铺主体60)", kpis["本期花费"], 970.0)
check("KPI 店铺ROI", kpis["店铺ROI"], round(13200 / 970, 2))
check("KPI 在推广商品数(436273237剔除后3个)", kpis["在推广商品数量"], 3)
cat = category_analysis(seg)
basic = {r["品类"]: r for r in cat["basic"]}
check("品类退款率 连衣裙 GMV加权", basic["连衣裙"]["退款率"], "43.3%")
check("品类退款率 毛衣=(0.45×8000)/11500", basic["毛衣"]["退款率"],
      f"{0.45*8000/11500*100:.1f}%")
check("品类ROI 毛衣=11500/700", basic["毛衣"]["ROI"], f"{11500/700:.2f}")
kw = channel_top(seg, "关键词", 5)
check("渠道TOP 排序(2002在前)", kw[0]["宝贝ID"], "2002")
check("渠道TOP 渠道投产=3000/300", kw[0]["渠道投产"], "10.00")
check("渠道TOP 1001 渠道投产=300/100", kw[1]["渠道投产"], "3.00")
rr = refund_risk(seg)
check("退款风险: 严格>0.7入选", sorted(r["宝贝ID"] for r in rr), [])
inv = inventory_actions(seg)
check("缺货仍投放(436273237剔除后仅1003)", sorted(r["宝贝ID"] for r in inv["no_stock"]), ["1003"])

# ══ 5. v9.3 解析+多信号合并+分层汇总 ══
v93_txt = """
【第二步】明日之星识别
1. [100000001001] 连衣裙A | 综合分:90
【第四步】爆发单品
- [100000001001] 连衣裙A
【第五步】预算再平衡
🔴 【削减】(1个)
- [100000001003] 半身裙C
【补充】投放溢出洞察
🌟 [100000001001] 溢出
【补充】全量分层展示
- [100000009999] 不应被解析
"""
sig = parse_v93_report(v93_txt)
check("T3 v9.3 三信号合并(顺序去重)", sig.get("100000001001"), "明日之星|爆发|溢出")
check("v9.3 削减桶", sig.get("100000001003"), "削减")
check("v9.3 全量分层段不解析", "100000009999" in sig, False)
seg["v9.3信号"] = seg["宝贝ID"].map({"1001": "明日之星|爆发", "1002": "明日之星"}).fillna("")
ss = segment_summary(seg)
row_c = next(r for r in ss if r["组合"] == "待改善明星款")
check("分层表 信号计数聚合", row_c["v9.3信号"], "明日之星×1 | 爆发×1")
check("分层表 付费求和", row_c["付费"], 150.0)
# P0-1 连带:滞销款滞销款组原含 1003/2001/436273237,店铺主体剔除后剩 1003/2001(2 个)
check("T4 零动销品计入分层汇总",
      sum(r["商品数"] for r in ss if r["组合"] == "滞销款滞销款") >= 2, True)
check("分层汇总Σ=全表", sum(r["商品数"] for r in ss), len(seg))

# ══ 6. 周期聚合/环比/滚动 ══
day2 = df.reset_index(drop=True).copy()
for c in ["GMV", "付费", "推广成交金额", "关键词", "人群", "全站", "店铺直达",
          "关键词归因成交", "人群归因成交", "全站归因成交", "店铺直达归因成交",
          "商品访客数", "支付买家数", "支付件数",
          "商品加购件数", "成功退款金额", "展现", "点击"]:
    if c in day2.columns:
        day2[c] = pd.to_numeric(day2[c], errors="coerce").fillna(0) * 0.5
agg2 = aggregate_period_frame(pd.concat([df.reset_index(drop=True), day2], ignore_index=True)).set_index("宝贝ID")
a1 = agg2.loc["1001"]
check("周期 GMV=1.5倍", a1["GMV"], 1500.0)
check("周期 付费=1.5倍", a1["付费"], 225.0)
check("周期 推广投产重算", a1["推广投产"], 600 / 225)
check("周期 UV价值重算", a1["访客平均价值"], 10.0)
check("周期 退款率不叠加(max)", a1["退款率"], 0.5)
check("周期 零动销品保留(T4)", "2001" in agg2.index, True)

prev = df.reset_index(drop=True).copy()
prev["GMV"] = prev["GMV"] * 2
prev["付费"] = prev["付费"] * 4
cmp_df = apply_period_comparisons(
    df.reset_index(drop=True), {"7日环比": {"current": df.reset_index(drop=True), "previous": prev}}
).set_index("宝贝ID")
check("上周期金额", cmp_df.loc["1001", "上周期金额"], 2000.0)
check("上周期付费", cmp_df.loc["1001", "上周期付费"], 600.0)

day_frame = df.reset_index(drop=True)[["宝贝ID", "支付件数", "付费"]].copy()
day_map = {d: (day_frame, None, 0, 0, set()) for d in ["2026-07-07", "2026-07-08", "2026-07-09"]}
roll = apply_rolling_daily_metrics(df.reset_index(drop=True), day_map, "2026-07-09").set_index("宝贝ID")
check("日销3", roll.loc["1001", "日销3"], 20.0)
check("日销7(缺失日按0)", roll.loc["1001", "日销7"], 60 / 7)
check("花费3", roll.loc["1001", "花费3"], 150.0)
check("花费15", roll.loc["1001", "花费15"], 30.0)
check("可售天数重算", roll.loc["1001", "可售天数"], round(120 / (60 / 7), 1))
p = build_period_ranges("2026-07-09")["7日环比"]
check("7日窗当前起点", p["current_start"], "2026-07-03")
check("7日窗对比区间", (p["prev_start"], p["prev_end"]), ("2026-06-26", "2026-07-02"))

print(f"══ 造数自检: 通过 {PASS} 项, 失败 {len(FAIL)} 项 ══")
for f in FAIL:
    print("FAIL", f)
sys.exit(1 if FAIL else 0)
