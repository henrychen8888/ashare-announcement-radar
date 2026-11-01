# -*- coding: utf-8 -*-
"""
每日一键：抓取 -> 打标 -> 生成看板 -> 清理过期归档

用法:
    python run.py                 # 抓今天并生成看板
    python run.py 2026-10-28      # 补抓指定日期
    python run.py --no-content    # 跳过正文摘要（快，约 8 秒）
"""
import glob
import os
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import build as board  # noqa: E402
import fetch  # noqa: E402

KEEP_DAYS = 120        # 归档保留天数
MIN_EXPECT = 30        # 低于这个条数，判定为非交易日 / 数据未发布


def cleanup():
    files = sorted(glob.glob(os.path.join(fetch.DATA_DIR, "*.json")))
    if len(files) <= KEEP_DAYS:
        return
    for f in files[:-KEEP_DAYS]:
        d = os.path.basename(f)[:-5]
        try:
            os.remove(f)
            h = os.path.join(board.OUT_DIR, f"{d}.html")
            if os.path.exists(h):
                os.remove(h)
        except OSError:
            pass


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    day = args[0] if args else datetime.now().strftime("%Y-%m-%d")
    with_content = "--no-content" not in sys.argv

    print(f"=== 沪深公告快递 {day} ===")
    payload = fetch.run(day, with_content=with_content)

    n = payload["count"]
    if n < MIN_EXPECT:
        print(f"!! 仅 {n} 条，判定为非交易日或数据尚未发布 —— 不生成看板，"
              f"保留上一交易日的结果")
        return None

    out = board.build(day)
    cleanup()
    s = payload["stats"]
    print(f"=== 完成 S级{s['S']} 利好{s['up']} 利空{s['down']} 涨停{s['limit_up']} ===")
    return out


if __name__ == "__main__":
    main()
