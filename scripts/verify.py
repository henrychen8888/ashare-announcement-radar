# -*- coding: utf-8 -*-
"""交付前自检：占位符残留、内嵌数据完整性、字段缺失、链接合法性。"""
import glob
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("AB_HOME") or BASE   # 与 fetch.py / build.py 保持一致
DATA_DIR = os.path.join(HOME, "data")
BOARD_DIR = os.path.join(HOME, "board")

PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z_]{2,}__")
REQUIRED = ["code", "name", "title", "url", "level", "direction", "topic",
            "score", "level", "is_noise", "time"]

problems = []


def check_html(path):
    html = open(path, encoding="utf-8").read()
    ph = PLACEHOLDER_RE.findall(html)
    if ph:
        problems.append(f"{os.path.basename(path)}: 残留占位符 {set(ph)}")

    m = re.search(r"const DATA = (\{.*?\});\nconst ITEMS", html, re.S)
    if not m:
        problems.append(f"{os.path.basename(path)}: 找不到内嵌 DATA")
        return
    try:
        data = json.loads(m.group(1))
    except ValueError as e:
        problems.append(f"{os.path.basename(path)}: 内嵌 DATA 非法 JSON -> {e}")
        return

    items = data["items"]
    if len(items) != data["stats"]["total"]:
        problems.append(f"{os.path.basename(path)}: items({len(items)}) != stats.total({data['stats']['total']})")

    try:
        with open(os.path.join(DATA_DIR, data["day"] + ".json"), encoding="utf-8") as f:
            src = json.load(f)
        if len(src["items"]) != len(items):
            problems.append(f"{os.path.basename(path)}: 与源 JSON 条数不一致 ({len(items)} vs {len(src['items'])})")
    except OSError:
        problems.append(f"{os.path.basename(path)}: 找不到源 JSON")

    bad_field, bad_url, bad_pct = 0, 0, 0
    for r in items:
        for k in REQUIRED:
            if k not in r or r[k] is None and k != "":
                bad_field += 1
                break
        if not str(r.get("url", "")).startswith("https://data.eastmoney.com/notices/detail/"):
            bad_url += 1
        # 阈值不能设成 ±100：北交所/创业板新股上市首日不设涨跌幅，
        # 实测 920306 天元重工上市首日 +120.54%，这是真实数据不是脏数据。
        p = r.get("pct")
        if p is not None and (p != p or p < -100 or p > 900):
            bad_pct += 1
    if bad_field:
        problems.append(f"{os.path.basename(path)}: {bad_field} 条缺必填字段")
    if bad_url:
        problems.append(f"{os.path.basename(path)}: {bad_url} 条链接非法")
    if bad_pct:
        problems.append(f"{os.path.basename(path)}: {bad_pct} 条涨跌幅越界")

    # 日期下拉项数 == 数据文件数
    opts = html.count('<option value=')
    nfiles = len(glob.glob(os.path.join(DATA_DIR, "*.json")))
    if opts != nfiles:
        problems.append(f"{os.path.basename(path)}: 日期选项 {opts} != 数据文件 {nfiles}")

    print(f"  {os.path.basename(path):<18} {len(items):>4} 条  "
          f"S={data['stats']['S']:<3} 利好={data['stats']['up']:<3} 利空={data['stats']['down']:<3} "
          f"涨停={data['stats']['limit_up']:<3} days={opts}")


def main():
    files = sorted(glob.glob(os.path.join(BOARD_DIR, "*.html")))
    if not files:
        print("没有看板文件")
        return 1
    print("窗口检查:")
    for f in files:
        check_html(f)

    # 一致性：index.html 应与其指向的日期文件内容一致
    idx = os.path.join(BOARD_DIR, "index.html")
    if os.path.exists(idx):
        ih = open(idx, encoding="utf-8").read()
        m = re.search(r"const DATA = \{.*?\"day\": \"([\d-]+)\"", ih, re.S)
        if m:
            day = m.group(1)
            target = os.path.join(BOARD_DIR, f"{day}.html")
            if os.path.exists(target):
                if open(target, encoding="utf-8").read() != ih:
                    problems.append(f"index.html 与 {day}.html 内容不一致")
                else:
                    print(f"  index.html -> {day}.html 一致 OK")

    print()
    if problems:
        print(f"发现 {len(problems)} 个问题:")
        for p in problems:
            print("  ✗", p)
        return 1
    print("自检通过：无占位符残留、字段完整、链接合法、多日数据一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
