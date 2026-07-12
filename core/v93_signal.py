"""解析 daily_review v9.3 日报文本,提取 pid → 信号标签。

2026-07-11 起解析日报全部信号段(此前仅四类正向池):
  【第二步】明日之星           → 明日之星
  【第二步-补充】近3日潜力款   → 潜力款
  【第三步】亏损风险观察       → 亏损风险
  【第四步】爆发单品           → 爆发
  【第五步】预算再平衡:
      🚀加预算款 / 🌱有机强    → 加预算
      ⏸️饱和观察              → 饱和观察
      🛡️保护/持有             → 保护持有
      🔴削减                  → 削减
  【补充】投放溢出洞察(🌟)     → 溢出

【补充】全量分层展示为 display only(全量版覆盖全部在投品),
解析它会让每个品都带信号、列失去区分度,故不解析。
"""
import re

PID_RE = re.compile(r"\[(?!计划)(\d{9,})\]")

_SECTION_STARTS = [
    ("【第二步-补充】", "recent"),        # 必须先于【第二步】匹配
    ("【第二步】", "focus"),
    ("【第三步】", "risk"),
    ("【第四步】", "scale_up"),
    ("【第五步】", "rebalance"),
    ("【补充】投放溢出洞察", "spillover"),  # 必须先于通用【补充】终止符匹配
]
_LABELS = {
    "focus": "明日之星",
    "recent": "潜力款",
    "risk": "亏损风险",
    "scale_up": "爆发",
    "spillover": "溢出",
}

# 第五步桶头 → 标签。只认「emoji + 空格 + 【」的段头;
# 明细区的 🛡️/❌ 计划行(后跟[计划id],无【)不会误改桶。
_BUCKET_HEADS = [
    ("🚀 【加预算款】", "加预算"),
    ("🌱 【有机强", "加预算"),
    ("⏸️ 【", "饱和观察"),
    ("🛡️ 【", "保护持有"),
    ("🔴 【", "削减"),
]


def parse_v93_report(text):
    """返回 {pid: '标签1|标签2'}(标签按日报出现顺序去重拼接)。"""
    found = {}
    section = None
    bucket = None
    for raw in text.splitlines():
        s = raw.strip()
        hit = False
        for mark, sec in _SECTION_STARTS:
            if s.startswith(mark):
                section, bucket, hit = sec, None, True
                break
        if hit:
            continue
        # 其余【补充】段(全量分层展示等)与执行总结终止解析
        if s.startswith("【补充】") or s.startswith("📊 执行总结"):
            section = bucket = None
            continue
        if section == "rebalance":
            matched = False
            for head, lbl in _BUCKET_HEADS:
                if s.startswith(head):
                    bucket, matched = lbl, True
                    break
            if matched:
                continue
            if s.startswith(("🟡 【", "❌ 【")):   # 防御:未知段头不归任何桶
                bucket = None
                continue

        if section in _LABELS:
            label = _LABELS[section]
        elif section == "rebalance" and bucket:
            label = bucket
        else:
            continue
        m = PID_RE.search(s)
        if m:
            found.setdefault(m.group(1), []).append(label)

    return {pid: "|".join(dict.fromkeys(labels)) for pid, labels in found.items()}
