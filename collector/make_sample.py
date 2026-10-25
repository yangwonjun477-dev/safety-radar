#!/usr/bin/env python3
"""디자인 확인용 '샘플' 데이터 생성기. 실제 시장 데이터가 아닙니다 (meta.sample = true 로 표시됨).
API 키를 등록하고 첫 수집이 실행되면 실제 데이터로 자동 교체됩니다."""
import datetime as dt
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from collect import CONFIG, HISTORY, KST, LATEST, price_stats, save_json, score_items  # noqa: E402

random.seed(7)
BASE = {  # 샘플용 가상 기준가 / 가상 검색량 / 계절성(peak 월)
    "coated-glove": (12000, 60000, None), "safety-shoes": (48000, 120000, None), "helmet": (9000, 35000, None),
    "dust-mask": (16000, 30000, 3), "goggles": (6000, 20000, None), "earplug": (5000, 15000, None),
    "harness": (65000, 12000, None), "vest": (8000, 25000, None), "cone": (6000, 18000, None),
    "fence": (40000, 9000, None), "sign": (9000, 14000, None), "beacon": (25000, 16000, None),
    "antislip": (9000, 22000, 12), "extinguisher": (26000, 110000, 1), "throw-ext": (22000, 15000, None),
    "car-ext": (25000, 45000, None), "detector": (9000, 20000, 1), "escape-mask": (15000, 8000, None),
    "fire-blanket": (16000, 12000, None), "descender": (160000, 4000, None), "emergency-light": (22000, 6000, None),
    "first-aid": (32000, 18000, None), "eyewash": (55000, 2500, None), "neck-cooler": (18000, 90000, 7),
    "arm-cooler": (6000, 70000, 7), "ice-vest": (45000, 15000, 7), "hot-pack": (15000, 150000, 1),
    "oil-pad": (42000, 3000, None), "spill-kit": (130000, 1200, None), "boom": (85000, 900, None),
    "recycle-bin": (32000, 40000, None),
}


def main():
    cfg = json.loads(Path(CONFIG).read_text(encoding="utf-8"))
    now = dt.datetime.now(KST)
    weeks = [(now.date() - dt.timedelta(weeks=26 - i)) for i in range(26)]
    items = []
    for c in cfg["items"]:
        base, vol, peak = BASE.get(c["id"], (20000, 10000, None))
        prices = [max(500, round(random.lognormvariate(math.log(base), 0.45), -1)) for _ in range(190)]
        st = price_stats(prices)
        series = []
        for w in weeks:
            season = 1 + (0.9 * math.cos((w.month - peak) / 12 * 2 * math.pi) if peak else 0)
            series.append({"p": w.isoformat(), "r": round(vol / 1100 * max(season, 0.15) * random.uniform(0.85, 1.15), 1)})
        vals = [s["r"] for s in series]
        it = {k: c.get(k) for k in ("id", "group", "sub", "name", "keyword", "cert", "memo")}
        it["naver"] = {**st, "total": int(vol * random.uniform(0.8, 12)), "sellers": random.randint(60, 150),
                       "categories": [{"path": f"생활/건강 > 공구 > 안전용품 > {c['name']}", "share": 70}],
                       "top": [{"title": f"[샘플] {c['name']} 상품 {i + 1}", "price": round(st['median'] * random.uniform(.7, 1.3), -1),
                                "mall": f"샘플스토어{i + 1}", "link": "", "image": ""} for i in range(10)]}
        it["trend"] = {"rel": round(sum(vals) / len(vals), 1),
                       "growth": round((sum(vals[-4:]) / sum(vals[-8:-4]) - 1) * 100, 1), "series": series}
        it["search"] = {"pc": int(vol * .25), "mobile": int(vol * .75), "total": vol, "adComp": random.choice(["높음", "중간", "낮음"])}
        if random.random() < 0.75:
            cps = [round(st["median"] * random.uniform(.75, 1.25), -1) for _ in range(10)]
            it["coupang"] = {"n": 10, "min": min(cps), "median": sorted(cps)[5], "avg": round(sum(cps) / 10), "max": max(cps),
                             "rocket": random.choice([30, 50, 60, 80]),
                             "top": [{"title": f"[샘플] 쿠팡 {c['name']} {i + 1}", "price": p, "rocket": i % 2 == 0, "link": "", "image": ""}
                                     for i, p in enumerate(cps)], "updated_at": now.isoformat(timespec="seconds")}
        else:
            it["coupang"] = None
        items.append(it)
    score_items(items)
    save_json(LATEST, {"meta": {"updated_at": now.isoformat(timespec="seconds"),
                                "next_update": (now + dt.timedelta(hours=72)).isoformat(timespec="seconds"),
                                "sample": True, "anchor": cfg["anchor"],
                                "sources": {"naver_shop": False, "datalab": False, "searchad": False, "coupang": False},
                                "errors": []}, "items": items})
    dates = [(now.date() - dt.timedelta(days=3 * (7 - i))).isoformat() for i in range(8)]
    hist = {"sample": True, "dates": dates, "items": {}}
    for it in items:
        a = it["naver"]["avg"]
        avgs = [round(a * random.uniform(.93, 1.07)) for _ in dates[:-1]] + [a]
        hist["items"][it["id"]] = {"min": [round(x * .45) for x in avgs], "avg": avgs, "median": [round(x * .95) for x in avgs],
                                   "score": [it["score"]] * len(dates),
                                   "cp_avg": [round(x * 1.02) for x in avgs] if it["coupang"] else [None] * len(dates)}
    save_json(HISTORY, hist)
    print("샘플 데이터 생성 완료")


if __name__ == "__main__":
    main()
