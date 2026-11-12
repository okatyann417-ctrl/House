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
    # 2026-11-12 追加（丘陵地。ゆるい坂は許容し、駅との高低差は内見で確認する）
    "少路": "18560",
    "千里山": "21590",
    "南千里": "37610",
    "山田": ["40330", "40333"],  # モノレール・千里線
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
    "千里山": "丘陵地（駅との高低差を内見で確認）",
    "津雲台": "丘陵地（駅との高低差を内見で確認）",
    "高野台": "丘陵地（駅との高低差を内見で確認）",
    "佐竹台": "丘陵地（駅との高低差を内見で確認）",
    "竹見台": "丘陵地（駅との高低差を内見で確認）",
    "山田": "丘陵地（駅との高低差を内見で確認）",
    "少路": "丘陵地（駅との高低差を内見で確認）",
    "西緑丘": "丘陵地（駅との高低差を内見で確認）",
    "東豊中": "丘陵地（駅との高低差を内見で確認）",
    "螢池": "ハザード未評価（騒音対策区域外）",
    "蛍池": "ハザード未評価（騒音対策区域外）",
}
# 伊丹空港の航空機騒音対策区域（第1種区域、Lden 62dB以上）。豊中市の早見表（2026年度）より
# 町名: (区域内の丁目, 一部だけ区域内の丁目)。丁目の集合が None なら町全体が区域内
NOISE_ZONE = {
    "箕輪": ({3}, set()), "走井": ({1, 2, 3}, set()), "勝部": ({1, 2, 3}, set()),
    "原田西町": (None, set()), "原田中": ({1, 2}, set()), "原田元町": ({2, 3}, {1}),
    "原田南": ({1, 2}, set()), "曽根西町": (set(), {2}), "曽根南町": ({2, 3}, {1}),
    "利倉東": ({1, 2}, set()), "利倉": ({1, 2, 3}, set()), "服部豊町": (set(), {2}),
    "服部西町": ({3, 4, 5}, {1, 2}), "服部南町": (set(), {3, 4, 5}),
    "服部寿町": ({1, 2, 3, 4, 5}, set()), "上津島": (set(), {1, 3}), "浜": (set(), {3}),
    "名神口": (set(), {1}), "稲津町": ({1, 2, 3}, set()), "穂積": ({1, 2}, set()),
    "野田町": (None, set()), "庄内栄町": (set(), {1}), "庄内幸町": (set(), {1, 2}),
    "豊南町東": ({3, 4}, {1, 2}), "豊南町西": ({1, 2, 3, 4, 5}, set()),
    "庄内西町": ({1, 2}, {3}), "庄内東町": ({1, 3, 4, 5}, {2, 6}),
    "豊南町南": ({1, 2, 3, 4, 5, 6}, set()),
}
# 豊中市の中学校区（豊中市の通学区域表、2026年4月1日時点）。町名の先頭一致で判定し、長い町名から照合する
TOYONAKA_JHS = {
    "第五中（除外）": ["岡町", "勝部", "末広町", "立花町", "玉井町", "走井", "宝山町", "箕輪", "山ノ上町"],
    "第三中": ["赤阪", "岡上の町", "北桜塚", "栗ヶ丘町", "曽根東町1", "中桜塚", "南桜塚", "夕日丘"],
    "第四中": ["稲津町", "城山町", "曽根東町", "長興寺南", "服部寿町1", "服部寿町2", "服部寿町3",
               "服部西町1", "服部西町2", "服部西町3", "服部本町", "服部南町", "服部元町", "穂積"],
    "第一中": ["今在家町", "上津島", "曽根西町", "曽根南町", "利倉", "服部寿町", "服部西町", "服部豊町",
               "原田", "名神口1", "南空港町"],
    "第十八中": ["石橋麻田町", "清風荘", "蛍池", "螢池"],
    "第十三中": ["柴原町1", "柴原町2", "千里園", "刀根山", "本町"],
    "第十一中": ["上野坂", "上野西2", "上野西3", "上野東2", "上野東3", "少路", "西緑丘1", "西緑丘2",
                 "東豊中町1", "東豊中町2", "東豊中町3", "東豊中町4", "緑丘"],
    "第十五中": ["熊野町", "東豊中町5", "東豊中町6"],
    "第十四中": ["永楽荘", "春日町3", "春日町4", "春日町5", "北緑丘", "向丘2", "向丘3"],
    "第二中": ["春日町1", "春日町2", "桜の町", "柴原町", "宮山町", "向丘1"],
    "要確認（番地で第三中か第十一中）": ["上野西1"],
    "要確認（番地で第十一中か第二中）": ["上野西4"],
    "要確認（番地で第十一中か第十四中）": ["西緑丘3"],
}
# 吹田市の通学区域（吹田市「市立小・中学校通学区域表」令和7年4月1日改正、対象駅の周辺だけ）
# (町名, 丁目の集合 or None=全域, 小学校, 中学校)。同じ町・丁目に複数行あるときは番地で分かれる
SUITA_SCHOOLS = [
    ("江坂町", {1}, "豊津第一小", "豊津中"), ("江坂町", {2}, "江坂大池小", "豊津西中"),
    ("江坂町", {2}, "豊津第二小", "豊津西中"), ("江坂町", {3, 4}, "江坂大池小", "豊津西中"),
    ("江坂町", {5}, "千里第三小", "第一中"), ("江の木町", None, "豊津第二小", "豊津西中"),
    ("垂水町", None, "豊津第一小", "豊津中"), ("豊津町", None, "豊津第二小", "豊津西中"),
    ("広芝町", None, "豊津第一小", "豊津中"), ("芳野町", None, "豊津第二小", "豊津西中"),
    ("円山町", None, "千里第三小", "第一中"),
    ("千里山霧が丘", None, "千里第二小", "第一中"), ("千里山高塚", None, "千里第二小", "第一中"),
    ("千里山竹園", {1}, "千里新田小", "南千里中"), ("千里山竹園", {1}, "千里第三小", "第一中"),
    ("千里山竹園", {2}, "桃山台小", "竹見台中"), ("千里山竹園", {2}, "千里新田小", "南千里中"),
    ("千里山月が丘", None, "千里第二小", "第一中"), ("千里山西", {1, 2, 3, 4, 5}, "千里第三小", "第一中"),
    ("千里山西", {6}, "桃山台小", "竹見台中"), ("千里山西", {6}, "千里新田小", "南千里中"),
    ("千里山虹が丘", None, "千里第二小", "第一中"), ("千里山東", {1, 2, 4}, "千里第二小", "第一中"),
    ("千里山東", {3}, "千里第二小", "第一中"), ("千里山東", {3}, "山手小", "豊津中"),
    ("千里山星が丘", None, "千里第二小", "第一中"), ("千里山松が丘", None, "千里第二小", "第一中"),
    ("佐井寺", {1, 2, 3}, "佐井寺小", "佐井寺中"), ("佐井寺", {4}, "佐竹台小", "高野台中"),
    ("佐竹台", None, "佐竹台小", "高野台中"), ("高野台", None, "高野台小", "高野台中"),
    ("竹見台", None, "千里たけみ小", "竹見台中"), ("桃山台", None, "桃山台小", "竹見台中"),
    ("津雲台", {1}, "高野台小", "高野台中"), ("津雲台", {2, 3, 4, 5, 6, 7}, "津雲台小", "古江台中"),
    ("古江台", None, "古江台小", "古江台中"),
    ("春日", {1, 2}, "千里新田小", "南千里中"), ("春日", {3, 4}, "千里新田小", "南千里中"),
    ("春日", {3, 4}, "桃山台小", "竹見台中"),
    ("山田市場", None, "南山田小", "山田中"), ("山田市場", None, "山田第三小", "西山田中"),
    ("山田丘", None, "北山田小", "山田東中"), ("山田北", None, "北山田小", "山田東中"),
    ("山田西", {1}, "山田第三小", "西山田中"), ("山田西", {2, 3}, "西山田小", "西山田中"),
    ("山田西", {4}, "北山田小", "山田東中"), ("山田西", {4}, "津雲台小", "古江台中"),
    ("山田東", {1, 2, 3}, "山田第一小", "山田東中"), ("山田東", {2, 4}, "北山田小", "山田東中"),
    ("山田南", None, "山田第三小", "西山田中"), ("千里丘西", None, "南山田小", "山田中"),
    ("樫切山", None, "南山田小", "山田中"), ("長野東", None, "南山田小", "山田中"),
    ("長野西", None, "南山田小", "山田中"), ("尺谷", None, "南山田小", "山田中"),
    ("山手町", {1, 2}, "山手小", "豊津中"), ("山手町", {3}, "千里第二小", "第一中"),
    ("山手町", {3}, "山手小", "豊津中"), ("山手町", {4}, "片山小", "片山中"),
]
EXCLUDED_JHS = "第五中（除外）"
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


def noise_of(address):
    """住所が航空機騒音対策区域に入るかを返す（豊中市のみ）。"""
    if "豊中市" not in address:
        return ""
    a = address.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    # 長い町名から照合する（「利倉東」を「利倉」より先に）
    for town in sorted(NOISE_ZONE, key=len, reverse=True):
        m = re.search(re.escape(town) + r"(\d*)", a)
        if not m:
            continue
        full, part = NOISE_ZONE[town]
        if full is None:
            return "騒音対策区域内"
        chome = int(m.group(1)) if m.group(1) else None
        if chome in full:
            return "騒音対策区域内"
        if chome in part:
            return "騒音対策区域に一部かかる（番地で要確認）"
        if chome is None:
            return "騒音対策区域にかかる町（丁目不明）"
        return ""
    return ""


def jhs_of(address):
    """豊中市の住所から中学校区を返す。分からなければ空文字。"""
    m = re.search(r"豊中市(.+)", address)
    if not m:
        return ""
    town = m.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    pairs = sorted(((p, j) for j, ps in TOYONAKA_JHS.items() for p in ps), key=lambda x: -len(x[0]))
    return next((j for p, j in pairs if town.startswith(p)), "")


def suita_school_of(address):
    """吹田市の住所から「小学校→中学校」を返す。分からなければ空文字。"""
    m = re.search(r"吹田市(.+)", address)
    if not m:
        return ""
    a = m.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    towns = sorted({t for t, *_ in SUITA_SCHOOLS}, key=len, reverse=True)
    town = next((t for t in towns if a.startswith(t)), None)
    if not town:
        return ""
    cm = re.match(re.escape(town) + r"(\d+)", a)
    chome = int(cm.group(1)) if cm else None
    hits = {f"{e}→{j}" for t, ch, e, j in SUITA_SCHOOLS
            if t == town and (ch is None or chome is None or chome in ch)}
    if len(hits) == 1:
        return hits.pop()
    return "／".join(sorted(hits)) + "（番地で要確認）" if hits else ""


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
    for st, codes in STATIONS.items():
      for code in ([codes] if isinstance(codes, str) else codes):
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
    m = re.search(r"所在階/構造・階建\s*(\d+)階/\D*?(\d+)階建", t)
    if m:
        r["floor"], r["stories"] = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"所在階\s*(\d+)階", t)
        r["floor"] = int(m.group(1)) if m else None
        m = re.search(r"構造・階建て?\s*\D*?(\d+)階建", t)
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
        m = re.search(label + r"\s*([\d万,]+円／月)", t)
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
        r["jhs"] = jhs_of(r["address"]) or suita_school_of(r["address"])
        if r["jhs"] == EXCLUDED_JHS:
            continue  # 豊中市の第五中校区は除外
        flags = [next((v for k, v in AREA_FLAGS.items() if k in r["address"]), ""), noise_of(r["address"])]
        r["area_flag"] = " / ".join(f for f in flags if f)
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

    # 同じ部屋を複数の会社が掲載しているものは1行にまとめる（面積・築年月・所在階・町丁目が同じもの）
    groups = {}
    for h in hits:
        town = re.sub(r"[-－].*$", "", h["address"])
        key = (area_of(h), h["built"], h.get("floor"), town)
        groups.setdefault(key, []).append(h)
    merged = []
    for g in groups.values():
        g.sort(key=lambda x: (x["change"] != "新着", x["url"]))
        top = dict(g[0])
        prices = sorted({x["price"] for x in g})
        if len(g) > 1:
            top["price"] = "〜".join([prices[0], prices[-1]]) if len(prices) > 1 else prices[0]
            top["name"] += f"（{len(g)}社が掲載）"
        if any(x.get("autolock") == "あり" for x in g):
            top["autolock"] = "あり"
        merged.append(top)

    print(f"取得件数: {len(rows)}件 / 該当: {len(hits)}件（重複をまとめて{len(merged)}件）")
    for h in sorted(merged, key=lambda x: (x["rank"], -area_of(x))):
        print("\t".join([h["rank"], h["change"], h["name"], h["price"], h["address"],
                         h["access"], h["area_text"], h["built"],
                         "権利:" + h.get("rights", "?"), "オートロック:" + h.get("autolock", "?"),
                         f"{h.get('floor')}階/{h.get('stories')}階建", "EV:" + h.get("elevator", "?"),
                         "管理費:" + h.get("kanri", "?"), "修繕:" + h.get("shuzen", "?"),
                         "中学:" + (h.get("jhs") or "要確認"), h.get("area_flag", ""), h["url"]]))
    for p in ended:
        print("\t".join(["掲載終了", "", p["name"], p["price"], p["url"]]))


if __name__ == "__main__":
    sys.exit(main())
