#!/usr/bin/env python3
"""SUUMOの駅別一覧から、search-criteria.md の条件に合う中古マンションを抜き出す。

使い方: python3 tools/suumo_check.py
結果は data/suumo_latest.json に保存し、前回の結果との差分を表示する。
"""
import html
import json
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "data" / "suumo_latest.json"

# 対象の駅（SUUMOの駅コード）
STATIONS = {
    "豊中": "26600",
    "岡町": "06570",
    "曽根": "21780",
    "服部天神": "30850",
    "江坂": "04840",
    "蛍池": "35083",
}
MIN_AREA = 80.0
NEAR_AREA = 75.0
MAX_WALK = 10
MAX_AGE = 25  # 築25年以内（実行日から数える）
MIN_FLOOR = 2
CITIES = ("豊中市", "吹田市")
# エリア検討で除外・要注意とした地域（住所に含まれる文字で判定）
AREA_FLAGS = {
    "南吹田": "除外エリア（水害）",
    "西の庄": "除外エリア（水害）",
    "庄内": "除外エリア（水害）",
    "広芝町": "江坂の南側（水害リスク要確認）",
    "江の木町": "江坂の南側（水害リスク要確認）",
    "江坂町": "垂水町・豊津町の北側ではない（ハザード要確認）",
    "服部": "天竺川沿いの低地でないか要確認",
    "螢池": "ハザード未評価・空港騒音要確認",
    "蛍池": "ハザード未評価・空港騒音要確認",
}
UA = "Mozilla/5.0"


def fetch(url):
    r = subprocess.run(
        ["curl", "-sS", "-m", "30", "-A", UA, url],
        capture_output=True, text=True, errors="ignore",
    )
    if r.returncode != 0:
        raise RuntimeError(f"{url}: {r.stderr.strip()}")
    return r.stdout


def text_of(fragment):
    t = re.sub(r"<[^>]+>", " ", fragment)
    t = html.unescape(re.sub(r"\s+", " ", t)).replace(" ヒント ", " ")
    return t.strip()


def field(txt, label):
    m = re.search(
        label + r"\s*(.{0,60}?)\s*(?:販売価格|所在地|沿線・駅|専有面積|間取り|バルコニー|築年月|お気に入り|掲載写真|$)",
        txt,
    )
    return m.group(1).strip() if m else ""


def parse_list(page):
    rows = []
    for b in re.split(r'class="property_unit ', page)[1:]:
        txt = text_of(b)
        link = re.search(r'href="(/ms/chuko/[^"]*nc_(\d+)/)"', b)
        if not link:
            continue
        rows.append({
            "id": link.group(2),
            "url": "https://suumo.jp" + link.group(1),
            "name": field(txt, "物件名"),
            "price": field(txt, "販売価格"),
            "address": field(txt, "所在地"),
            "access": field(txt, "沿線・駅"),
            "area_text": field(txt, "専有面積"),
            "built": field(txt, "築年月"),
        })
    return rows


def area_of(r):
    m = re.search(r"([\d.]+)\s*(?:m|㎡|平米)", r["area_text"])
    return float(m.group(1)) if m else 0.0


def walk_of(r):
    m = re.search(r"徒歩(\d+)分", r["access"])
    return int(m.group(1)) if m and "バス" not in r["access"] else 99


def built_of(r):
    m = re.search(r"(\d{4})年(\d{1,2})月", r["built"])
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def collect():
    found = {}
    for st, code in STATIONS.items():
        for page_no in range(1, 30):
            url = f"https://suumo.jp/ms/chuko/osaka/ek_{code}/"
            if page_no > 1:
                url += f"?page={page_no}"
            rows = parse_list(fetch(url))
            if not rows:
                break
            for r in rows:
                r["search_station"] = st
                found.setdefault(r["id"], r)
            time.sleep(1)
    return list(found.values())


def check_detail(r):
    """詳細ページで権利形態とオートロックを確認する。"""
    try:
        t = text_of(fetch(r["url"]))
    except RuntimeError:
        return
    m = re.search(r"権利形態\s*(\S+)", t)
    r["rights"] = m.group(1) if m else "不明"
    r["autolock"] = "あり" if "オートロック" in t else "記載なし（要確認）"
    m = re.search(r"所在階(?:/構造・階建)?\s*(\d+)階", t)
    r["floor"] = int(m.group(1)) if m else None
    m = re.search(r"(\d+)階建", t)
    r["stories"] = int(m.group(1)) if m else None
    if re.search(r"エレベー[タ夕]ー?\s*(?:なし|無)", t):
        r["elevator"] = "なし"
    elif "エレベーター" in t or "エレベータ" in t:
        r["elevator"] = "あり"
    elif r["stories"] and r["stories"] >= 6:
        r["elevator"] = "あり（6階建以上のため推定）"
    else:
        r["elevator"] = "要確認"
    for key, label in (("kanri", "管理費"), ("shuzen", "修繕積立金")):
        m = re.search(label + r"\s*(\S+円／月|\S+)", t)
        r[key] = m.group(1) if m else "不明"
    time.sleep(1)


def main():
    today = date.today()
    min_built = (today.year - MAX_AGE, today.month)
    rows = collect()
    hits = []
    for r in rows:
        if not any(c in r["address"] for c in CITIES):
            continue
        if walk_of(r) > MAX_WALK or built_of(r) < min_built or area_of(r) < NEAR_AREA:
            continue
        if not any(f"「{s}」" in r["access"] for s in STATIONS):
            continue
        r["area_flag"] = next((v for k, v in AREA_FLAGS.items() if k in r["address"]), "")
        r["rank"] = "条件を満たす" if area_of(r) >= MIN_AREA else "惜しい（75〜79㎡）"
        check_detail(r)
        if "借地" in r.get("rights", ""):
            continue  # 条件1：所有権のみ
        if r.get("floor") is not None and r["floor"] < MIN_FLOOR:
            continue  # 2階以上
        if r.get("elevator") == "なし":
            continue  # エレベーターあり
        hits.append(r)

    prev = {}
    if SNAPSHOT.exists():
        prev = {h["id"]: h for h in json.loads(SNAPSHOT.read_text())["hits"]}
    cur = {h["id"]: h for h in hits}
    for h in hits:
        if h["id"] not in prev:
            h["change"] = "新着"
        elif prev[h["id"]]["price"] != h["price"]:
            h["change"] = f"価格変更（{prev[h['id']]['price']}→{h['price']}）"
        else:
            h["change"] = ""
    scanned_ids = {r["id"] for r in rows}
    # 一覧から消えたものだけを掲載終了とする（条件変更で外れたものは含めない）
    ended = [p for i, p in prev.items() if i not in scanned_ids]

    SNAPSHOT.parent.mkdir(exist_ok=True)
    SNAPSHOT.write_text(json.dumps(
        {"date": date.today().isoformat(), "scanned": len(rows), "hits": hits},
        ensure_ascii=False, indent=1,
    ))

    print(f"取得件数: {len(rows)}件 / 該当: {len(hits)}件")
    for h in sorted(hits, key=lambda x: (x["rank"], -area_of(x))):
        print("\t".join([h["rank"], h["change"], h["name"], h["price"], h["address"],
                         h["access"], h["area_text"], h["built"],
                         "権利:" + h.get("rights", "?"), "オートロック:" + h.get("autolock", "?"),
                         f"{h.get('floor')}階/{h.get('stories')}階建", "EV:" + h.get("elevator", "?"),
                         "管理費:" + h.get("kanri", "?"), "修繕:" + h.get("shuzen", "?"),
                         h.get("area_flag", ""), h["url"]]))
    for p in ended:
        print("\t".join(["掲載終了", "", p["name"], p["price"], p["url"]]))


if __name__ == "__main__":
    sys.exit(main())
