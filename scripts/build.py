# -*- coding: utf-8 -*-
"""
把 data/YYYY-MM-DD.json 渲染成单文件看板 board/index.html
单文件内嵌数据，双击即开，不依赖服务器。
"""
import glob
import json
import os
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("AB_HOME") or BASE   # 与 fetch.py 保持一致，见那里的注释
DATA_DIR = os.path.join(HOME, "data")
OUT_DIR = os.path.join(HOME, "board")

MIN_RENDER = 30   # 低于此条数视为非交易日，不渲染，避免产出空壳看板

TOPIC_ORDER = ["合同中标", "增减持", "并购重组", "业绩", "回购", "股权激励",
               "风险警示", "诉讼担保", "融资", "经营投资", "资金募集", "公司治理", "其他"]


def fmt_amount(v):
    if not v:
        return "—"
    if v >= 1e8:
        return f"{v/1e8:.2f}亿"
    if v >= 1e4:
        return f"{v/1e4:.0f}万"
    return f"{v:.0f}"


def _weekday(day):
    """0=周一 … 6=周日。非法日期串按工作日处理（保守，不影响主流程）"""
    try:
        return datetime.strptime(day, "%Y-%m-%d").weekday()
    except ValueError:
        return 0


def build(day=None, all_days=True):
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.json")))
    if not files:
        print("没有数据，先跑 fetch.py")
        return None
    days = [os.path.basename(f)[:-5] for f in files]
    day = day or days[-1]

    # index.html 指向哪个日期：优先「最近一个工作日 + 有足够数据」。
    # 理由：周末上市公司照样披露公告（实测周日 58 条，下周开盘前有用），
    # 这些要落盘、要能翻，但只有几十条的周末页不该顶掉周五那份完整看板。
    latest_valid = days[-1]

    def _enough(d):
        try:
            with open(os.path.join(DATA_DIR, f"{d}.json"), encoding="utf-8") as fh:
                return json.load(fh).get("count", 0) >= MIN_RENDER
        except (OSError, ValueError):
            return False

    for d in reversed(days):
        if _enough(d) and _weekday(d) < 5:
            latest_valid = d
            break
    else:
        # 全是周末（如长假期间连续抓取），退回最近一个有数据的日期
        for d in reversed(days):
            if _enough(d):
                latest_valid = d
                break

    targets = days if all_days else [day]
    out, rendered = None, 0
    for d in targets:
        r = render_one(d, days, latest_valid)
        if r:
            out, rendered = r, rendered + 1
    idx = os.path.join(OUT_DIR, "index.html")
    if rendered:
        print(f"看板已生成: {idx}（{rendered} 个交易日）")
    else:
        print("没有可渲染的交易日（全部低于条数阈值）")
    return out


def render_one(day, days, latest_valid=None):
    path = os.path.join(DATA_DIR, f"{day}.json")
    if not os.path.exists(path):
        print(f"缺少 {day} 数据")
        return None
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)

    if payload.get("count", 0) < MIN_RENDER:
        print(f"  跳过 {day}：仅 {payload.get('count', 0)} 条，非交易日不渲染")
        return None

    items = payload["items"]
    s = payload["stats"]

    # 主题分布（按固定顺序）
    topic_counts = s["topics"]
    ordered = [(t, topic_counts[t]) for t in TOPIC_ORDER if t in topic_counts]
    rest = [(t, c) for t, c in topic_counts.items() if t not in TOPIC_ORDER]
    ordered += sorted(rest, key=lambda x: -x[1])
    tmax = max([c for _, c in ordered] or [1])

    # 重点区：S 级按时间倒序（「最新快递」，不是最强的）
    su = sorted([r for r in items if r["level"] == "S" and r["direction"] > 0],
                key=lambda x: x.get("time", ""), reverse=True)
    sd = sorted([r for r in items if r["level"] == "S" and r["direction"] < 0],
                key=lambda x: x.get("time", ""), reverse=True)
    focus_up, focus_down = su[:14], sd[:14]

    data_json = json.dumps({
        "day": day,
        "items": items,
        "stats": s,
        "generated": payload.get("generated_at", ""),
    }, ensure_ascii=False).replace("</", "<\\/")

    html = TEMPLATE
    html = html.replace("__DATA__", data_json)
    html = html.replace("__DAY__", day)
    html = html.replace("__DAY_LABEL__", f"{day[5:7]}月{day[8:10]}日")
    html = html.replace("__GENERATED__", (payload.get("generated_at", "") or "")[-8:-3])
    html = html.replace("__TOTAL__", str(s["total"]))
    html = html.replace("__UP__", str(s["up"]))
    html = html.replace("__DOWN__", str(s["down"]))
    html = html.replace("__S__", str(s["S"]))
    html = html.replace("__STOCKS__", str(s["stocks"]))
    html = html.replace("__LIMITUP__", str(s["limit_up"]))
    html = html.replace("__ELAPSED__", str(payload.get("elapsed", "")))
    html = html.replace("__TOPIC_BARS__", render_topic_bars(ordered, tmax))
    html = html.replace("__FOCUS_UP_TOTAL__", str(len(su)))
    html = html.replace("__FOCUS_DOWN_TOTAL__", str(len(sd)))
    html = html.replace("__FOCUS_UP__", render_focus(focus_up, "up", "没有 S 级利好公告"))
    html = html.replace("__FOCUS_DOWN__", render_focus(focus_down, "down", "没有 S 级利空公告"))
    html = html.replace("__DAY_OPTIONS__", render_day_options(days, day))

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"{day}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    # 最新有效交易日同时写成 index.html
    if day == (latest_valid or days[-1]):
        with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
            f.write(html)
    print(f"  {out}  ({len(html)/1024:.0f} KB, {len(items)} 条)")
    return out


def render_topic_bars(pairs, tmax):
    rows = []
    for name, cnt in pairs:
        w = max(4, round(cnt / tmax * 100))
        cls = "tb-hot" if cnt >= tmax * 0.5 else ""
        rows.append(
            f'<div class="tb-row" data-topic="{name}">'
            f'<span class="tb-name">{name}</span>'
            f'<span class="tb-track"><i class="{cls}" style="width:{w}%"></i></span>'
            f'<span class="tb-cnt">{cnt}</span></div>'
        )
    return "\n".join(rows)


def render_focus(recs, side, empty):
    if not recs:
        return f'<div class="focus-empty">{empty}</div>'
    out = []
    for r in recs:
        pct = r.get("pct")
        pct_cls = "up" if (pct or 0) > 0 else ("down" if (pct or 0) < 0 else "flat")
        pct_txt = f"{pct:+.2f}%" if pct is not None else "—"
        lim = f'<em class="lim">{r["limit"]}</em>' if r.get("limit") else ""
        summary = r.get("summary") or ""
        sum_html = f'<p class="fc-sum">{esc(summary[:130])}</p>' if summary else ""
        out.append(f"""<a class="fc" href="{r['url']}" target="_blank" rel="noopener">
  <div class="fc-head">
    <span class="fc-code">{r['code']}</span>
    <span class="fc-name">{esc(r['name'])}</span>
    <span class="fc-pct {pct_cls}">{pct_txt}</span>{lim}
    <span class="fc-topic">{r['topic']}</span>
  </div>
  <p class="fc-title">{esc(r['title'])}</p>{sum_html}
</a>""")
    return "\n".join(out)


def render_day_options(days, cur):
    """周末日期加标注 —— 否则用户看不出「周日也有公告」是数据问题还是正常现象"""
    out = []
    for d in reversed(days):
        wd = _weekday(d)
        tag = " · 周末" if wd >= 5 else ""
        out.append(f'<option value="{d}"{" selected" if d == cur else ""}>{d}{tag}</option>')
    return "".join(out)


def esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>沪深公告快递 · __DAY__</title>
<style>
:root{
  --bg:#f4f5f7; --card:#ffffff; --line:#e6e8ec; --line-2:#eef0f3;
  --ink:#101828; --ink-2:#475467; --ink-3:#98a2b3;
  --red:#d92d20; --red-bg:#fef3f2; --red-line:#fecdca;
  --green:#079455; --green-bg:#f0fdf4; --green-line:#bbf7d0;
  --blue:#175cd3; --blue-bg:#eff8ff;
  --amber:#b54708; --amber-bg:#fffaeb;
  --radius:12px;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  background:var(--bg); color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  font-size:13px; line-height:1.55; -webkit-font-smoothing:antialiased;
  padding:22px 24px 60px;
}
a{color:inherit;text-decoration:none}
.wrap{max-width:1560px;margin:0 auto}

/* ---------- header ---------- */
.head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.h-left{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
h1{font-size:22px;font-weight:700;letter-spacing:-.3px}
.h-day{font-size:13px;font-weight:600;color:var(--blue);background:var(--blue-bg);
  padding:3px 10px;border-radius:999px;border:1px solid #d1e9ff}
.h-sub{color:var(--ink-3);font-size:12px}
.h-right{display:flex;align-items:center;gap:8px}
select,input{font-family:inherit;font-size:12.5px;color:var(--ink);background:var(--card);
  border:1px solid var(--line);border-radius:8px;padding:6px 10px;outline:none}
select:focus,input:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(23,92,211,.09)}

/* ---------- kpi ---------- */
.kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-bottom:14px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:13px 16px;position:relative;overflow:hidden}
.kpi::after{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--line)}
.kpi.red::after{background:var(--red)}
.kpi.green::after{background:var(--green)}
.kpi.amber::after{background:#f79009}
.kpi.blue::after{background:var(--blue)}
.kpi-k{font-size:11.5px;color:var(--ink-3);font-weight:500;letter-spacing:.3px}
.kpi-v{font-size:24px;font-weight:700;letter-spacing:-.6px;font-variant-numeric:tabular-nums;margin-top:1px}
.kpi-v small{font-size:12px;font-weight:500;color:var(--ink-3);margin-left:3px;letter-spacing:0}
.kpi.red .kpi-v{color:var(--red)}
.kpi.green .kpi-v{color:var(--green)}

/* ---------- focus ---------- */
.focus{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:14px}
.fcol{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;min-width:0}
.fcol-h{display:flex;align-items:center;justify-content:space-between;
  padding:11px 16px;border-bottom:1px solid var(--line-2);font-weight:600;font-size:13px}
.fcol-h .dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:7px}
.fcol.up .dot{background:var(--red)} .fcol.up .cnt{color:var(--red)}
.fcol.down .dot{background:var(--green)} .fcol.down .cnt{color:var(--green)}
.fcol-h .cnt{font-variant-numeric:tabular-nums;font-size:12px}
.fcol-b{padding:6px 8px 8px;max-height:334px;overflow-y:auto;mask-image:linear-gradient(to bottom,#000 calc(100% - 26px),transparent);-webkit-mask-image:linear-gradient(to bottom,#000 calc(100% - 26px),transparent)}
.fc{display:block;padding:8px 10px;border-radius:8px;transition:background .12s}
.fc:hover{background:#f8f9fb}
.fc+.fc{border-top:1px solid var(--line-2)}
.fc-head{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:2px;min-width:0}
.fc-code{font-size:11.5px;color:var(--ink-3);font-variant-numeric:tabular-nums;letter-spacing:.2px}
.fc-name{font-weight:600;font-size:12.5px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fc-pct{font-size:11.5px;font-variant-numeric:tabular-nums;font-weight:600;margin-left:auto;flex:none}
.fc-pct.up{color:var(--red)} .fc-pct.down{color:var(--green)} .fc-pct.flat{color:var(--ink-3)}
.lim{font-style:normal;font-size:10px;font-weight:700;padding:1px 5px;border-radius:4px;
  background:var(--red);color:#fff;letter-spacing:.5px}
.lim:only-child{}
.fc-topic{font-size:10.5px;color:var(--ink-2);background:#f2f4f7;padding:1px 6px;border-radius:4px}
.fc-title{font-size:12.5px;color:var(--ink);line-height:1.5;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.fc-sum{font-size:11.5px;color:var(--ink-2);margin-top:3px;line-height:1.5;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.focus-empty{padding:28px;text-align:center;color:var(--ink-3);font-size:12px}

/* ---------- topics ---------- */
.topics{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:13px 16px 10px;margin-bottom:14px}
.tp-h{font-size:12px;color:var(--ink-3);font-weight:600;margin-bottom:9px;letter-spacing:.3px}
.tb-grid{display:grid;grid-template-columns:repeat(3,1fr);grid-auto-flow:column;grid-template-rows:repeat(5,auto);gap:6px 26px}
.tb-row{display:flex;align-items:center;gap:8px;cursor:pointer;padding:2px 4px;border-radius:5px}
.tb-row:hover{background:#f8f9fb}
.tb-row.on{background:var(--blue-bg)}
.tb-name{font-size:12px;width:56px;flex:none;color:var(--ink-2)}
.tb-track{flex:1;height:6px;background:#f0f2f5;border-radius:3px;overflow:hidden}
.tb-track i{display:block;height:100%;background:#cfd6e0;border-radius:3px;transition:width .3s}
.tb-track i.tb-hot{background:#94a3b8}
.tb-cnt{font-size:11.5px;color:var(--ink-3);width:34px;text-align:right;
  font-variant-numeric:tabular-nums;flex:none}

/* ---------- list ---------- */
.panel{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
.p-bar{display:flex;align-items:center;gap:8px;padding:11px 14px;border-bottom:1px solid var(--line-2);
  flex-wrap:wrap;position:sticky;top:0;background:var(--card);z-index:5}
.p-title{font-weight:600;font-size:13px;margin-right:4px}
.p-title b{color:var(--blue);font-variant-numeric:tabular-nums}
.seg{display:flex;background:#f2f4f7;border-radius:7px;padding:2px}
.seg button{font-family:inherit;font-size:12px;border:0;background:transparent;color:var(--ink-2);
  padding:4px 11px;border-radius:5px;cursor:pointer;transition:.12s}
.seg button.on{background:var(--card);color:var(--ink);font-weight:600;box-shadow:0 1px 2px rgba(16,24,40,.08)}
.p-search{margin-left:auto;width:190px}
.chk{display:inline-flex;align-items:center;gap:5px;font-size:12px;color:var(--ink-2);cursor:pointer;user-select:none}
.chk input{width:13px;height:13px;accent-color:var(--blue);cursor:pointer}

.rows{max-height:none}
.r-head{display:grid;grid-template-columns:52px 112px 76px 60px 40px 1fr;
  gap:10px;padding:6px 14px;border-bottom:1px solid var(--line);
  font-size:11px;color:var(--ink-3);background:#fafbfc;letter-spacing:.3px}
.ta-r{text-align:right} .ta-c{text-align:center}
.row{display:grid;grid-template-columns:52px 112px 76px 60px 40px 1fr;
  align-items:center;gap:10px;padding:8px 14px;border-bottom:1px solid var(--line-2);
  position:relative;transition:background .1s}
.row:hover{background:#f8f9fb}
.row::before{content:"";position:absolute;left:0;top:6px;bottom:6px;width:2.5px;border-radius:2px}
.row.d1::before{background:var(--red)}
.row.d-1::before{background:var(--green)}
.row.d0::before{background:transparent}
.r-time{font-size:11px;color:var(--ink-3);font-variant-numeric:tabular-nums}
.r-stk{display:flex;flex-direction:column;gap:0;min-width:0}
.r-code{font-size:10.5px;color:var(--ink-3);font-variant-numeric:tabular-nums}
.r-name{font-size:12.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.r-pct{font-size:12px;font-weight:600;font-variant-numeric:tabular-nums;text-align:right}
.r-pct.up{color:var(--red)} .r-pct.down{color:var(--green)} .r-pct.flat{color:var(--ink-3)}
.r-topic{font-size:10.5px;color:var(--ink-2);background:#f2f4f7;padding:1.5px 6px;
  border-radius:4px;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lv{font-size:10.5px;font-weight:700;text-align:center;border-radius:4px;padding:1.5px 0;letter-spacing:.3px}
.lv.S{background:var(--red);color:#fff}
.lv.S.dn{background:var(--green)}
.lv.A{background:var(--red-bg);color:var(--red);border:1px solid var(--red-line)}
.lv.A.dn{background:var(--green-bg);color:var(--green);border-color:var(--green-line)}
.lv.B{color:var(--ink-3);border:1px solid var(--line)}
.lv.N{color:#c4cad3}
.r-title{font-size:12.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.r-title:hover{color:var(--blue)}
.tag-lim{font-size:9.5px;font-weight:700;padding:0 4px;border-radius:3px;margin-left:6px;
  background:var(--red);color:#fff;vertical-align:1px}
.tag-lim.dn{background:var(--green)}
.r-doc{font-size:9.5px;color:var(--ink-3);border:1px solid var(--line);border-radius:3px;
  padding:0 4px;margin-left:6px}

.empty{padding:50px;text-align:center;color:var(--ink-3);font-size:13px}
.foot{padding:14px 4px 0;color:var(--ink-3);font-size:11.5px;line-height:1.7}
.foot b{color:var(--ink-2);font-weight:600}

@media (max-width:1280px){
  .kpis{grid-template-columns:repeat(3,minmax(0,1fr))}
  .tb-grid{grid-template-columns:repeat(2,minmax(0,1fr));grid-auto-flow:row;
    grid-template-rows:none}
  .focus{grid-template-columns:minmax(0,1fr)}
}
@media (max-width:760px){
  body{padding:14px 12px 40px}
  .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
  .tb-grid{grid-template-columns:minmax(0,1fr)}
  .h-sub{display:none}
  .fcol-b{max-height:300px}
  .row{grid-template-columns:0 100px 64px 0 36px 1fr;gap:8px}
  .r-time,.r-topic{display:none}
  .p-search{width:100%;margin-left:0}
}
</style>
</head>
<body>
<div class="wrap">

  <div class="head">
    <div class="h-left">
      <h1>沪深公告快递</h1>
      <span class="h-day">__DAY_LABEL__</span>
      <span class="h-sub">重大事项 · 资产重组 · 风险提示 · 持股变动 · 融资 &nbsp;|&nbsp; 更新 __GENERATED__ &nbsp;|&nbsp; 耗时 __ELAPSED__s</span>
    </div>
    <div class="h-right">
      <select id="daySel">__DAY_OPTIONS__</select>
      <input id="q" class="p-search" placeholder="搜代码 / 简称 / 标题">
    </div>
  </div>

  <div class="kpis">
    <div class="kpi blue"><div class="kpi-k">当日公告总数</div><div class="kpi-v">__TOTAL__<small>条</small></div></div>
    <div class="kpi red"><div class="kpi-k">利好 / 利空</div><div class="kpi-v">__UP__<small>/ __DOWN__</small></div></div>
    <div class="kpi amber"><div class="kpi-k">重大事项 S 级</div><div class="kpi-v">__S__<small>条</small></div></div>
    <div class="kpi"><div class="kpi-k">涉及个股</div><div class="kpi-v">__STOCKS__<small>只</small></div></div>
    <div class="kpi red"><div class="kpi-k">公告股中涨停</div><div class="kpi-v">__LIMITUP__<small>只</small></div></div>
  </div>

  <div class="focus">
    <div class="fcol up">
      <div class="fcol-h"><span><span class="dot"></span>重大利好 <span class="cnt">S 级</span></span>
        <span class="cnt">共 __FOCUS_UP_TOTAL__ 条 · 查看全部 →</span></div>
      <div class="fcol-b">__FOCUS_UP__</div>
    </div>
    <div class="fcol down">
      <div class="fcol-h"><span><span class="dot"></span>重大利空 / 风险 <span class="cnt">S 级</span></span>
        <span class="cnt">共 __FOCUS_DOWN_TOTAL__ 条 · 查看全部 →</span></div>
      <div class="fcol-b">__FOCUS_DOWN__</div>
    </div>
  </div>

  <div class="topics">
    <div class="tp-h">主题分布（点击筛选）</div>
    <div class="tb-grid">__TOPIC_BARS__</div>
  </div>

  <div class="panel">
    <div class="p-bar">
      <span class="p-title">全部公告 <b id="cnt">0</b></span>
      <div class="seg" id="segDir">
        <button data-v="all" class="on">全部</button>
        <button data-v="up">利好</button>
        <button data-v="down">利空</button>
      </div>
      <div class="seg" id="segLv">
        <button data-v="all" class="on">全部级别</button>
        <button data-v="S">仅 S 级</button>
        <button data-v="A">A 级以上</button>
      </div>
      <label class="chk"><input type="checkbox" id="hideNoise" checked>隐藏无效披露</label>
      <label class="chk"><input type="checkbox" id="onlyLim">仅涨停股</label>
      <input id="q2" class="p-search" placeholder="搜代码 / 简称 / 标题">
    </div>
    <div class="r-head">
      <span>时间</span><span>代码 / 简称</span><span class="ta-r">涨跌幅</span>
      <span class="ta-c">主题</span><span class="ta-c">级</span><span>公告标题</span>
    </div>
    <div class="rows" id="rows"></div>
  </div>

  <div class="foot">
    <b>口径</b>：覆盖沪深两市（含科创板、创业板）每日「重大事项 / 资产重组 / 风险提示 / 持股变动 / 融资公告」五个分类，
    已按公告发布时间过滤当日，同一公告跨分类自动去重。<br>
    <b>定级</b>：S = 硬信号白名单（重大合同·中标·控股股东增持·并购重组·业绩预增 / 减持·处罚·立案·退市风险·业绩预亏）；
    A = 中等影响；B = 弱影响或配套文件；N = 中性。<br>
    <b>方向</b>：由公告分类字段（结构化）主导，标题关键词仅作修正 —— 每条的判定依据都存在数据里，可回查。<br>
    <b>行情</b>：腾讯财经实时快照，涨跌停按交易所涨跌停价直接比对，不按「涨幅≈10%」估算。<br>
    <b>注意</b>：「公告股中涨停」指当日发了公告的个股里涨停的数量，<b>不是</b>全市场涨停家数；
    「涉及个股 604 只」是去重后的股票数，同一公司多条公告只算一只。
  </div>
</div>

<script>
const DATA = __DATA__;
const ITEMS = DATA.items || [];

// 每只股票的 S 级利好计数（用于判断「公告+涨停」共振）
const state = {dir:'all', lv:'all', topic:'', noise:true, lim:false, q:''};
const rowsEl = document.getElementById('rows');
const cntEl = document.getElementById('cnt');

function pctHtml(v){
  if(v===null||v===undefined) return '<span class="r-pct flat">—</span>';
  const c = v>0?'up':(v<0?'down':'flat');
  return `<span class="r-pct ${c}">${v>0?'+':''}${v.toFixed(2)}%</span>`;
}
function esc(s){return (s||'').replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}

function render(){
  const q = state.q.trim().toLowerCase();
  const out = [];
  for(const r of ITEMS){
    if(state.dir==='up' && r.direction<=0) continue;
    if(state.dir==='down' && r.direction>=0) continue;
    if(state.lv==='S' && r.level!=='S') continue;
    if(state.lv==='A' && !(r.level==='S'||r.level==='A')) continue;
    if(state.noise && r.is_noise) continue;
    if(state.lim && r.limit!=='涨停') continue;
    if(state.topic && r.topic!==state.topic) continue;
    if(q && !(r.code.includes(q) || r.name.toLowerCase().includes(q) || r.title.toLowerCase().includes(q))) continue;
    const dcls = r.direction>0?'d1':(r.direction<0?'d-1':'d0');
    const lvcls = 'lv '+r.level+(r.direction<0?' dn':'');
    let limTag = '';
    if(r.limit==='涨停') limTag = '<span class="tag-lim">涨停</span>';
    else if(r.limit==='跌停') limTag = '<span class="tag-lim dn">跌停</span>';
    const docTag = r.is_doc ? '<span class="r-doc">配套文件</span>' : '';
    out.push(`<div class="row ${dcls}">
  <span class="r-time">${(r.time||'').slice(11,16)}</span>
  <span class="r-stk"><span class="r-code">${r.market}${r.code}</span><span class="r-name">${esc(r.name)}</span></span>
  ${pctHtml(r.pct)}
  <span class="r-topic">${r.topic}</span>
  <span class="${lvcls}">${r.level}</span>
  <a class="r-title" href="${r.url}" target="_blank" rel="noopener" title="${esc(r.title)}">${esc(r.title)}${limTag}${docTag}</a>
</div>`);
  }
  rowsEl.innerHTML = out.length ? out.join('') : '<div class="empty">没有符合条件的公告</div>';
  cntEl.textContent = out.length;
}

document.getElementById('segDir').addEventListener('click', e=>{
  const b = e.target.closest('button'); if(!b) return;
  [...e.currentTarget.children].forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); state.dir = b.dataset.v; render();
});
document.getElementById('segLv').addEventListener('click', e=>{
  const b = e.target.closest('button'); if(!b) return;
  [...e.currentTarget.children].forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); state.lv = b.dataset.v; render();
});
document.getElementById('hideNoise').addEventListener('change', e=>{state.noise=e.target.checked; render();});
document.getElementById('onlyLim').addEventListener('change', e=>{state.lim=e.target.checked; render();});

function bindSearch(id){
  document.getElementById(id).addEventListener('input', e=>{
    state.q = e.target.value;
    document.getElementById('q').value = state.q;
    document.getElementById('q2').value = state.q;
    render();
  });
}
bindSearch('q'); bindSearch('q2');

document.querySelectorAll('.tb-row').forEach(el=>{
  el.addEventListener('click', ()=>{
    const t = el.dataset.topic;
    if(state.topic===t){ state.topic=''; el.classList.remove('on'); }
    else { state.topic=t; document.querySelectorAll('.tb-row').forEach(x=>x.classList.remove('on')); el.classList.add('on'); }
    render();
    document.querySelector('.panel').scrollIntoView({behavior:'smooth', block:'start'});
  });
});

document.querySelectorAll('.fcol-h').forEach(h=>{
  h.style.cursor='pointer';
  h.title='点击查看该方向全部 S 级公告';
  h.addEventListener('click', ()=>{
    const side = h.closest('.fcol').classList.contains('up') ? 'up' : 'down';
    document.querySelector(`#segDir button[data-v="${side}"]`).click();
    document.querySelector('#segLv button[data-v="S"]').click();
  });
});

document.getElementById('daySel').addEventListener('change', e=>{
  location.href = e.target.value + '.html';
});

render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    build(arg)
