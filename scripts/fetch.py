# -*- coding: utf-8 -*-
"""
沪深公告快递 —— 抓取 + 打标 + 行情叠加 + 落库

用法:
    python fetch.py                # 抓今天，含 S 级正文摘要
    python fetch.py 2026-10-28     # 补抓指定日期
    python fetch.py --no-content   # 不抓正文（快）

输出: data/YYYY-MM-DD.json
"""
import json
import os
import sys
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tagger import tag  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
# 输出根目录：装成 Skill 后脚本位于 <skills>/xxx/scripts/，数据不能写回技能包。
# 用 AB_HOME 指向工作区，未设置时回退到脚本同级目录（保持原行为）。
HOME = os.environ.get("AB_HOME") or BASE
DATA_DIR = os.path.join(HOME, "data")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Referer": "https://data.eastmoney.com/"}

# 重大事项相关的分类节点
NODES = {
    5: "重大事项",
    6: "资产重组",
    3: "风险提示",
    7: "持股变动",
    2: "融资公告",
}

MAX_PAGES = 45   # 上限只影响补抓历史；抓当日时翻到当天起点就 stop
SLEEP = 0.12


def http_json(url, headers=None, retry=3):
    h = dict(UA)
    if headers:
        h.update(headers)
    last = None
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"请求失败 {url[:80]} -> {last}")


def fetch_node(f_node, day, statistics):
    """按节点翻页抓取，display_time 早于 day 00:00 即停"""
    got, page = [], 1
    while page <= MAX_PAGES:
        q = dict(page_size=100, page_index=page, ann_type="A", client_source="web",
                 f_node=f_node, s_node=0)
        url = "https://np-anotice-stock.eastmoney.com/api/security/ann?" + urllib.parse.urlencode(q)
        try:
            d = http_json(url)
        except RuntimeError as e:
            statistics["errors"].append(str(e))
            break
        lst = (d.get("data") or {}).get("list") or []
        if not lst:
            break
        stop = False
        for it in lst:
            dt = (it.get("display_time") or "")[:10]
            if dt < day:          # 列表按时间倒序，越过当天即可停
                stop = True
                continue
            if dt > day:          # 极端情况：服务器时间超前，保留
                pass
            got.append(it)
        page += 1
        if stop:
            break
        time.sleep(SLEEP)
    return got


def secid_prefix(code):
    """腾讯行情的市场前缀。920 是北交所新代码段，必须排在「9 开头=沪市」之前，
    否则北交所股票会被拼成 sh920xxx，静默取不到行情。"""
    if code.startswith("920") or code.startswith("8") or code.startswith("4"):
        return "bj"
    if code.startswith(("6", "9", "11")):
        return "sh"
    return "sz"


def market_of(code):
    """看板角标：区分子市场，可转债单列（转债公告对正股只是间接影响）"""
    if code.startswith(("11", "12", "13")):
        return "债"
    if code.startswith("920") or code.startswith("8") or code.startswith("4"):
        return "北"
    if code.startswith(("6", "9")):
        return "沪"
    return "深"


def fetch_quotes(codes):
    """
    腾讯行情源（qt.gtimg.cn），GBK 编码，一次可批量 ~60 只。
    关键优势：直接返回【涨停价 / 跌停价】(idx 47/48)，涨停判定不用自己算，
    避免主板 10% / 创业科创 20% / ST 5% / 北交所 30% 混算出错。
    """
    out = {}
    codes = [c for c in dict.fromkeys(codes) if c and c.isdigit() and len(c) == 6]
    for i in range(0, len(codes), 60):
        batch = codes[i:i + 60]
        qs = ",".join(secid_prefix(c) + c for c in batch)
        url = f"https://qt.gtimg.cn/q={qs}"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.qq.com/"})
            with urllib.request.urlopen(req, timeout=20) as r:
                txt = r.read().decode("gbk", "ignore")
        except Exception:  # noqa: BLE001
            continue
        for line in txt.split(";"):
            line = line.strip()
            if "=" not in line or '"' not in line:
                continue
            body = line.split('="', 1)[1].rstrip('"')
            f = body.split("~")
            if len(f) < 50:
                continue
            code = f[2]

            def num(idx):
                try:
                    return float(f[idx])
                except (ValueError, IndexError):
                    return None

            price, prev = num(3), num(4)
            up_limit, dn_limit = num(47), num(48)
            out[code] = {
                "name": f[1].replace(" ", ""),
                "price": price,
                "prev_close": prev,
                "pct": num(32),
                "turnover": num(38),
                "amount": (num(37) or 0) * 1e4,      # 万元 -> 元
                "float_cap": num(44),                 # 亿
                "mktcap": num(45),                    # 亿
                "up_limit": up_limit,
                "dn_limit": dn_limit,
            }
        time.sleep(SLEEP)
    return out


def mark_limit(q, code):
    """用交易所涨停/跌停价直接比对，容差 1 分钱"""
    if not q:
        return ""
    price = q.get("price")
    if price is None or price <= 0:
        return ""
    up, dn = q.get("up_limit"), q.get("dn_limit")
    if up and abs(price - up) < 0.011:
        return "涨停"
    if dn and abs(price - dn) < 0.011:
        return "跌停"
    return ""


CLEAN_RE = re.compile(r"[ \t\u3000]+")
TITLE_PREFIX_RE = re.compile(r"^[^:：]{1,10}[:：]")
CORP_RE = re.compile(r"^.{2,14}?(?:集团)?(?:股份)?有限公司")


def clean_title(t):
    """去掉「证券简称:」「XX股份有限公司」这类前缀，标题里公司名不必念两遍"""
    t = (t or "").strip()
    for _ in range(3):
        m = TITLE_PREFIX_RE.match(t)
        if m and len(m.group(0)) <= 11:
            t = t[m.end():].strip()
            continue
        m2 = CORP_RE.match(t)
        if m2:
            t = t[m2.end():].strip()
            continue
        break
    return t.strip()


CORP_ONLY_RE = re.compile(r"^[\u4e00-\u9fa5（）()]{3,30}(股份有限公司|有限公司|集团有限公司)$")
CORP_PREFIX_RE = re.compile(
    r"^[\u4e00-\u9fa5]{2,20}(?:股份)?有限公司(?:[（(][^）)]{0,20}[）)])?")
TITLE_LINE_RE = re.compile(r"^关于.{4,60}的?(公告|通知|说明|报告)$")


def fetch_content(art_code, title=""):
    """
    抓公告正文，剥掉抬头（证券代码/公告编号/公司全名/重复标题/免责声明），
    只留实质内容。不剥的话首页摘要前 60 字全是废话。
    """
    url = ("https://np-cnotice-stock.eastmoney.com/api/content/ann?"
           f"art_code={art_code}&client_source=web&page_index=1")
    try:
        d = http_json(url, retry=2)
    except RuntimeError:
        return ""
    body = ((d.get("data") or {}).get("notice_content") or "")
    if not body:
        return ""

    kept = []
    for ln in body.split("\n"):
        ln = CLEAN_RE.sub(" ", ln).strip()
        if not ln or len(ln) < 6:
            continue
        if "证券代码" in ln or "公告编号" in ln:
            continue
        if "保证公告内容" in ln or "虚假记载" in ln or "连带责任" in ln:
            continue
        if CORP_ONLY_RE.match(ln):
            continue
        if TITLE_LINE_RE.match(ln):          # 与标题重复的那一行
            continue
        # 行首公司全名（含「（以下简称公司）」）剥掉
        ln = CORP_PREFIX_RE.sub("", ln).strip()
        if len(ln) < 6:
            continue
        kept.append(ln)

    # 标题在 PDF 转换后常被拆成多行，逐行删掉与标题重合的碎片
    tnorm = (title or "").replace(" ", "")
    if tnorm:
        i = 0
        while i < min(6, len(kept)):
            cand = kept[i].replace(" ", "")
            if len(cand) >= 6 and cand in tnorm:
                kept.pop(i)
                continue
            i += 1

    text = " ".join(kept)
    text = re.sub(r"^重要内容提示[:：]?\s*", "", text)
    text = re.sub(r"^(一|二|三|1|2|3)[、.．]\s*", "", text)
    if len(text) > 260:
        cut = text[:260]
        for sep in ("。", "；", "，"):
            idx = cut.rfind(sep)
            if idx > 140:
                cut = cut[:idx + 1]
                break
        text = cut
    return text.strip()


def parse_day(it):
    """返回 (公告日, 发布时间)"""
    nd = (it.get("notice_date") or "")[:10]
    dt = (it.get("display_time") or "").replace(":", " ", 0)
    return nd, (it.get("display_time") or "")


def run(day, with_content=True):
    os.makedirs(DATA_DIR, exist_ok=True)
    t0 = time.time()
    statistics = {"errors": [], "nodes": {}}
    raw = []
    for node, name in NODES.items():
        lst = fetch_node(node, day, statistics)
        statistics["nodes"][name] = len(lst)
        for it in lst:
            it["_node"] = name
        raw.extend(lst)
        print(f"  [{name}] {len(lst)} 条")

    # 去重（同一公告可能在多节点出现）
    seen, merged = set(), []
    for it in raw:
        code = it.get("art_code")
        if not code or code in seen:
            continue
        seen.add(code)
        merged.append(it)
    print(f"  去重后 {len(merged)} 条 / 耗时 {time.time()-t0:.1f}s")

    # 主体信息 + 打标
    records, codes = [], []
    for it in merged:
        cs = it.get("codes") or [{}]
        primary = cs[0]
        stock_code = str(primary.get("stock_code") or "")
        short_name = primary.get("short_name") or ""
        cols = [c.get("column_name", "") for c in (it.get("columns") or [])]
        title = it.get("title") or ""
        notice_date, display_time = parse_day(it)

        # 过滤：只保留属于「公告日 day 当天发布」的
        if display_time[:10] != day:
            continue

        t = tag({"columns": cols, "title": title})
        rec = {
            "art_code": it.get("art_code"),
            "code": stock_code,
            "name": short_name,
            "market": market_of(stock_code),
            "title": clean_title(title),
            "title_raw": title,
            "columns": cols,
            "notice_date": notice_date,
            "time": (it.get("display_time") or "")[:16],
            "node": it.get("_node"),
            "url": f"https://data.eastmoney.com/notices/detail/{stock_code}/{it.get('art_code')}.html",
            **t,
        }
        records.append(rec)
        if stock_code:
            codes.append(stock_code)

    # 行情
    print(f"  拉取行情 {len(set(codes))} 只 ...")
    quotes = fetch_quotes(codes)
    for r in records:
        q = quotes.get(r["code"])
        if q:
            r["pct"] = q.get("pct")
            r["turnover"] = q.get("turnover")
            r["amount"] = q.get("amount")
            r["mktcap"] = q.get("mktcap")
            r["float_cap"] = q.get("float_cap")
            r["limit"] = mark_limit(q, r["code"])
        else:
            r["pct"] = r["turnover"] = r["amount"] = r["mktcap"] = None
            r["float_cap"] = None
            r["limit"] = ""

    # S 级正文摘要
    if with_content:
        targets = [r for r in records if r["level"] == "S" and not r["is_noise"]]
        print(f"  抓取 {len(targets)} 条 S 级正文 ...")
        with ThreadPoolExecutor(max_workers=8) as ex:
            txts = list(ex.map(lambda t: fetch_content(t["art_code"], t.get("title", "")),
                               targets))
        for r, txt in zip(targets, txts):
            r["summary"] = txt
        for r in records:
            r.setdefault("summary", "")

    # 排序：强度降序 → 时间降序
    records.sort(key=lambda x: (abs(x["score"]), x["time"]), reverse=True)

    stats = build_stats(records)
    payload = {
        "day": day,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed": round(time.time() - t0, 1),
        "count": len(records),
        "stats": stats,
        "errors": statistics["errors"],
        "nodes": statistics["nodes"],
        "items": records,
    }
    # 0 条不落盘 —— 周末/节假日空跑会生成一个看似合法的空文件，
    # 之后 build 会把它渲染成 18KB 空壳看板，还会挤进日期下拉里。
    if not records:
        print(f"  当日 0 条，跳过落盘（非交易日或公告尚未发布）  {time.time()-t0:.1f}s")
        return payload

    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{day}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"  写入 {path}  共 {len(records)} 条  {time.time()-t0:.1f}s")
    return payload


def build_stats(records):
    s = {
        "total": len(records),
        "up": 0, "down": 0, "flat": 0,
        "S": 0, "A": 0, "B": 0, "N": 0,
        "limit_up": 0, "limit_down": 0,
        "topics": {}, "nodes": {},
        "stocks": len({r["code"] for r in records if r["code"]}),
        "noise": 0,
    }
    up_stocks, dn_stocks = set(), set()
    for r in records:
        s["up" if r["direction"] > 0 else "down" if r["direction"] < 0 else "flat"] += 1
        s[r["level"]] += 1
        if r.get("limit") == "涨停":
            up_stocks.add(r["code"])
        if r.get("limit") == "跌停":
            dn_stocks.add(r["code"])
        if r.get("is_noise"):
            s["noise"] += 1
        s["topics"][r["topic"]] = s["topics"].get(r["topic"], 0) + 1
        s["nodes"][r["node"]] = s["nodes"].get(r["node"], 0) + 1
    s["limit_up"] = len(up_stocks)      # 去重后的涨停个股数
    s["limit_down"] = len(dn_stocks)
    return s


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    day = args[0] if args else datetime.now().strftime("%Y-%m-%d")
    run(day, with_content="--no-content" not in sys.argv)
