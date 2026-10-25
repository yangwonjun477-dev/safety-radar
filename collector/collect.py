#!/usr/bin/env python3
"""
안전·소방·보건·환경 용품 시장 데이터 수집기

- 네이버 검색 API(쇼핑)      : 가격(최저/평균/중앙값/분포), 등록 상품 수, 판매처 수, 상위 상품
- 네이버 데이터랩(검색어 트렌드): 6개월 주간 관심도 추이, 상승률 (기준 키워드로 품목 간 비교 가능하게 보정)
- NAVER API HUB (네이버 클라우드) : 검색어트렌드, 뉴스 검색, 쇼핑인사이트(분야별 클릭 추이)
  → NAVER_HUB_CLIENT_ID / NAVER_HUB_CLIENT_SECRET 이 있으면 검색어트렌드는 API HUB로 호출합니다.
  → 네이버 쇼핑 검색 API는 2026-07-31 종료(API HUB 미이관). 상품 순위·가격은 쿠팡 파트너스 API로 수집합니다.
- 네이버 검색광고 API (선택)  : 월간 검색량(PC+모바일), 광고 경쟁정도
- 쿠팡 파트너스 API (선택)    : 쿠팡 상위 상품 가격, 로켓배송 비율 (호출 제한 때문에 실행마다 일부 품목씩 순환)

키는 모두 환경변수(GitHub Secrets)로 받습니다. 없는 API는 건너뜁니다.
외부 라이브러리 없이 파이썬 표준 라이브러리만 사용합니다.
"""
import base64
import datetime as dt
import html
import hashlib
import hmac
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "collector" / "keywords.json"
DATA_DIR = ROOT / "docs" / "data"
LATEST = DATA_DIR / "latest.json"
HISTORY = DATA_DIR / "history.json"
KST = dt.timezone(dt.timedelta(hours=9))

INTERVAL_HOURS = float(os.getenv("INTERVAL_HOURS", "70"))  # 3일 주기 (매일 체크, 70시간 지나면 수집)
COUPANG_MAX = int(os.getenv("COUPANG_MAX_PER_RUN", "10"))  # 쿠팡 검색 API는 시간당 호출 제한이 엄격함 → 1시간에 10개씩
COUPANG_ROUNDS = int(os.getenv("COUPANG_ROUNDS", "4"))      # 한 번 실행에서 최대 4회(=40개) 순환, 회차 사이 1시간 대기
COUPANG_WAIT = int(os.getenv("COUPANG_WAIT_SEC", "3600"))
HISTORY_KEEP = 120
NEWS_PER_ITEM = int(os.getenv("NEWS_PER_ITEM", "5"))

DEV_BASE = "https://openapi.naver.com"
HUB_BASE = "https://naverapihub.apigw.ntruss.com"
DATALAB_URL = {"dev": DEV_BASE + "/v1/datalab/search", "hub": HUB_BASE + "/search-trend/v1/search"}


def dev_headers(cid, csec):
    return {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec}


def hub_headers(cid, csec):
    return {"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": csec}


def clean(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def env(key):
    v = os.getenv(key, "").strip()
    return v or None


def log(*a):
    print(*a, flush=True)


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def save_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def request(url, headers=None, data=None, retries=3):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    h = dict(headers or {})
    if body is not None:
        h["Content-Type"] = "application/json"
    for i in range(retries):
        req = urllib.request.Request(url, data=body, headers=h, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")[:300]
            if e.code in (429, 500, 502, 503, 504) and i < retries - 1:
                time.sleep((i + 1) * (10 if e.code == 429 else 2))
                continue
            raise RuntimeError(f"HTTP {e.code}: {msg}")
        except urllib.error.URLError:
            if i < retries - 1:
                time.sleep(2)
                continue
            raise


# ---------------------------------------------------------------- 통계
def pct(v, q):
    if not v:
        return None
    k = (len(v) - 1) * q
    f, c = math.floor(k), math.ceil(k)
    return v[f] + (v[c] - v[f]) * (k - f)


def histogram(vals, bins=10):
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return {"edges": [lo, hi], "counts": [len(vals)]}
    w = (hi - lo) / bins
    counts = [0] * bins
    for x in vals:
        counts[min(int((x - lo) / w), bins - 1)] += 1
    return {"edges": [round(lo + w * i) for i in range(bins + 1)], "counts": counts}


def price_stats(prices):
    v = sorted(p for p in prices if p and p > 0)
    if not v:
        return None
    lo, hi = pct(v, 0.05), pct(v, 0.95)
    trimmed = [p for p in v if lo <= p <= hi] or v  # 평균·분포는 상하위 5% 이상치(부속품·대량세트) 제외
    return {
        "n": len(v),
        "min": v[0],
        "p10": round(pct(v, 0.10)),
        "median": round(statistics.median(v)),
        "avg": round(statistics.mean(trimmed)),
        "p90": round(pct(v, 0.90)),
        "max": v[-1],
        "hist": histogram(trimmed),
    }


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


# ---------------------------------------------------------------- 네이버 쇼핑
def naver_shop(item, headers):
    excl = [w.lower() for w in item.get("exclude", []) if w]
    prods, total = [], 0
    for start in (1, 101):
        q = urllib.parse.urlencode({
            "query": item["keyword"], "display": 100, "start": start,
            "sort": "sim", "exclude": "used:rental:cbshop",
        })
        r = request(DEV_BASE + "/v1/search/shop.json?" + q, headers)
        total = r.get("total", total)
        for it in r.get("items", []):
            title = re.sub(r"<[^>]+>", "", it.get("title", ""))
            if any(w in title.lower() for w in excl):
                continue
            if str(it.get("productType", "1")) not in ("1", "2", "3"):  # 일반상품만 (중고/단종/판매예정 제외)
                continue
            try:
                price = int(it.get("lprice") or 0)
            except ValueError:
                price = 0
            if price <= 0:
                continue
            cats = [it.get(f"category{i}") for i in range(1, 5) if it.get(f"category{i}")]
            prods.append({
                "title": title, "price": price, "mall": it.get("mallName", ""),
                "brand": it.get("brand", ""), "link": it.get("link", ""),
                "image": it.get("image", ""), "cat": " > ".join(cats),
            })
        time.sleep(0.15)
    if not prods:
        return None
    stats = price_stats([p["price"] for p in prods])
    cat_top = Counter(p["cat"] for p in prods if p["cat"]).most_common(3)
    return {
        **stats,
        "total": total,
        "sellers": len({p["mall"] for p in prods if p["mall"]}),
        "categories": [{"path": c, "share": round(n / len(prods) * 100)} for c, n in cat_top],
        "top": [{k: p[k] for k in ("title", "price", "mall", "link", "image")} for p in prods[:10]],
        **{k: v for k, v in (analyze_products([dict(p) for p in prods[:10]]) or {}).items()
           if k in ("min_item", "real_min", "real_min_item", "top")},
    }


# ---------------------------------------------------------------- 네이버 데이터랩
def datalab(keywords, anchor, url, headers):
    """기준(anchor) 키워드를 매 요청에 함께 넣어 요청 간 비율을 보정 → 품목 간 관심도 비교 가능"""
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(weeks=26)
    others = [k for k in dict.fromkeys(keywords) if k != anchor]
    out = {}
    for i in range(0, max(len(others), 1), 4):
        batch = [anchor] + others[i:i + 4]
        body = {
            "startDate": start.isoformat(), "endDate": end.isoformat(), "timeUnit": "week",
            "keywordGroups": [{"groupName": k, "keywords": [k]} for k in batch],
        }
        r = request(url, headers, body)
        res = {g["title"]: g.get("data", []) for g in r.get("results", [])}
        a_mean = mean([d["ratio"] for d in res.get(anchor, [])]) or 1
        for k in batch:
            if k == anchor and k in out:
                continue
            data = res.get(k, [])
            series = [{"p": d["period"], "r": round(d["ratio"] * 100 / a_mean, 1)} for d in data]
            vals = [s["r"] for s in series]
            last4, prev4 = mean(vals[-4:]), mean(vals[-8:-4])
            out[k] = {
                "rel": round(mean(vals) or 0, 1),  # 기준 키워드 평균 = 100
                "growth": round((last4 / prev4 - 1) * 100, 1) if last4 is not None and prev4 else None,
                "series": series,
            }
        time.sleep(0.2)
    return out


def datalab_monthly(keywords, anchor, url, headers, years=3):
    """최근 N년 월간 관심도 (기준 키워드 평균=100). 계절성 분석용."""
    today = dt.date.today()
    end = today.replace(day=1) - dt.timedelta(days=1)          # 지난달 말일 (완결된 달만)
    start = dt.date(end.year - years, end.month, 1) + dt.timedelta(days=32)
    start = start.replace(day=1)
    others = [k for k in dict.fromkeys(keywords) if k != anchor]
    out = {}
    for i in range(0, max(len(others), 1), 4):
        batch = [anchor] + others[i:i + 4]
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "timeUnit": "month",
                "keywordGroups": [{"groupName": k, "keywords": [k]} for k in batch]}
        r = request(url, headers, body)
        res = {g["title"]: g.get("data", []) for g in r.get("results", [])}
        a_mean = mean([d["ratio"] for d in res.get(anchor, [])]) or 1
        for k in batch:
            if k == anchor and k in out:
                continue
            out[k] = [{"p": d["period"][:7], "r": round(d["ratio"] * 100 / a_mean, 2)} for d in res.get(k, [])]
        time.sleep(0.2)
    return out


# ---------------------------------------------------------------- 수요 예측 모델 (계절성 × 추세 × 언론 관심도)
def _add_month(ym, n):
    y, m = int(ym[:4]), int(ym[5:7]) - 1 + n
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def forecast_item(monthly, buzz, base_month, horizons=(1, 2, 3)):
    """
    monthly : [{"p":"YYYY-MM","r":값}] 최근 3년 월간 관심도
    base_month : 이번 달 "YYYY-MM" (데이터는 지난달까지)
    반환: 월별 계절지수, 성수기, 신뢰도, 전년 대비 추세, 1·2·3개월 후 예측치
    """
    pts = [(x["p"], x["r"]) for x in monthly or [] if x.get("r") is not None]
    if len(pts) < 15 or sum(v for _, v in pts) <= 0:
        return None
    months = [p for p, _ in pts]
    vals = [max(v, 0.01) for _, v in pts]
    n = len(vals)
    # 1) 계절지수: 각 달 값 ÷ 그 달을 중심으로 한 12개월 이동평균
    ratios = {m: [] for m in range(1, 13)}
    for i in range(6, n - 6):
        cma = (sum(vals[i - 6:i + 6]) + sum(vals[i - 5:i + 7])) / 24
        if cma > 0:
            ratios[int(months[i][5:7])].append(vals[i] / cma)
    for i in list(range(0, 6)) + list(range(n - 6, n)):           # 가장자리: 연평균 대비 비율로 보완
        lo, hi = max(0, i - 11), min(n, i + 12)
        base = mean(vals[lo:hi])
        if base:
            m = int(months[i][5:7])
            if len(ratios[m]) < 2:
                ratios[m].append(vals[i] / base)
    si = {m: (mean(v) if v else 1.0) for m, v in ratios.items()}
    norm_k = mean(list(si.values())) or 1
    si = {m: v / norm_k for m, v in si.items()}
    spread = {m: (statistics.pstdev(v) if len(v) > 1 else 0.35) for m, v in ratios.items()}

    # 2) 계절성 제거 후 수준과 전년 대비 추세
    des = [v / si[int(p[5:7])] for p, v in zip(months, vals)]
    level = mean(des[-3:])
    yoy = None
    if n >= 18:
        last6, prev6 = mean(des[-6:]), mean(des[-18:-12])
        if prev6:
            yoy = max(-0.5, min(0.8, last6 / prev6 - 1))
    drift = (1 + (yoy or 0)) ** (1 / 12) - 1

    # 3) 이번 달 추정치(기준)와 h개월 후 예측치
    gap = 1  # 데이터 마지막 달 → 이번 달
    cur = level * (1 + drift) ** gap * si[int(base_month[5:7])]
    buzz_boost = min(buzz or 0, 60) / 60 * 0.08                 # 최근 1주 뉴스가 많으면 최대 +8%
    out = []
    for h in horizons:
        tm = _add_month(base_month, h)
        mi = int(tm[5:7])
        f = level * (1 + drift) ** (gap + h) * si[mi] * (1 + buzz_boost * (0.6 ** (h - 1)))
        out.append({"h": h, "month": tm, "value": round(f, 2), "si": round(si[mi], 3),
                    "change": round((f / cur - 1) * 100, 1) if cur else None,
                    "spread": round(spread[mi], 3)})
    peak = max(si, key=si.get)
    low = min(si, key=si.get)
    seasonality = max(si.values()) / max(min(si.values()), 0.05)
    return {"si": {str(m): round(v, 3) for m, v in si.items()}, "peak": peak, "low": low,
            "seasonality": round(seasonality, 2), "yoy": round(yoy * 100, 1) if yoy is not None else None,
            "current": round(cur, 2), "buzz": buzz, "years": round(n / 12, 1), "h": out}


def rank_forecasts(items):
    """horizon별 추천 점수(0~100) = 예상 수요 규모 55% + 현재 대비 증가율 35% + 언론 관심 10%, 근거 문장 생성"""
    for h in (1, 2, 3):
        rows = [(it, next(x for x in it["forecast"]["h"] if x["h"] == h)) for it in items if it.get("forecast")]
        if not rows:
            continue
        size = norm([math.log10(x["value"] + 1) for _, x in rows])
        chg = norm([max(-60, min(150, x["change"] or 0)) for _, x in rows])
        bz = norm([math.log10((it["forecast"].get("buzz") or 0) + 1) for it, _ in rows])
        for (it, x), a, b, c in zip(rows, size, chg, bz):
            fc = it["forecast"]
            x["score"] = round(100 * (0.55 * a + 0.35 * b + 0.10 * (c or 0)))
            yrs = fc["years"]
            x["conf"] = "높음" if x["spread"] < 0.15 and yrs >= 2.5 else ("보통" if x["spread"] < 0.3 else "낮음")
            mi = int(x["month"][5:7])
            r = []
            if x["si"] >= 1.12:
                r.append(f"예년 {mi}월 관심도가 연평균보다 {round((x['si'] - 1) * 100)}% 높은 성수기")
            elif x["si"] <= 0.88:
                r.append(f"예년 {mi}월은 연평균보다 {round((1 - x['si']) * 100)}% 낮은 비수기")
            if x["change"] is not None and abs(x["change"]) >= 5:
                r.append(f"이번 달 대비 예상 수요 {'+' if x['change'] > 0 else ''}{x['change']:.0f}%")
            if fc.get("yoy") is not None and abs(fc["yoy"]) >= 8:
                r.append(f"전년 같은 시기보다 관심도 {'+' if fc['yoy'] > 0 else ''}{fc['yoy']:.0f}%")
            if (fc.get("buzz") or 0) >= 10:
                r.append(f"최근 1주 관련 뉴스 {fc['buzz']}건")
            if fc["peak"] == mi:
                r.append(f"연중 최대 성수기({mi}월)")
            x["reasons"] = r or ["연중 수요가 고른 품목 (계절 변동 작음)"]


# ---------------------------------------------------------------- NAVER API HUB: 뉴스 / 쇼핑인사이트
def hub_news(keyword, headers, n=NEWS_PER_ITEM):
    """최신 뉴스 n건 + 최근 7일 기사 수(언론 관심도, 최대 100)"""
    q = urllib.parse.urlencode({"query": keyword, "display": 100, "start": 1, "sort": "date"})
    r = request(f"{HUB_BASE}/search/v1/news?{q}", headers)
    now = dt.datetime.now(KST)
    out, week = [], 0
    for it in r.get("items", []):
        try:
            d = dt.datetime.strptime(it.get("pubDate", ""), "%a, %d %b %Y %H:%M:%S %z").astimezone(KST)
        except ValueError:
            d = None
        if d and (now - d).days < 7:
            week += 1
        if len(out) < n:
            out.append({"title": clean(it.get("title")), "desc": clean(it.get("description"))[:140],
                        "link": it.get("originallink") or it.get("link", ""),
                        "date": d.isoformat(timespec="minutes") if d else None})
    return out, week


def shop_insight(categories, headers):
    """쇼핑인사이트 분야별 클릭 추이 (최근 6개월, 주간). 요청당 최대 3개 분야, 요청마다 최댓값=100 기준."""
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(weeks=26)
    out = []
    for i in range(0, len(categories), 3):
        batch = categories[i:i + 3]
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "timeUnit": "week",
                "category": [{"name": c["name"], "param": [str(c["code"])]} for c in batch]}
        r = request(f"{HUB_BASE}/shopping/v1/categories", headers, body)
        for g in r.get("results", []):
            series = [{"p": d["period"], "r": round(d["ratio"], 1)} for d in g.get("data", [])]
            vals = [s["r"] for s in series]
            last4, prev4 = mean(vals[-4:]), mean(vals[-8:-4])
            out.append({"name": g.get("title"), "series": series,
                        "growth": round((last4 / prev4 - 1) * 100, 1) if last4 is not None and prev4 else None})
        time.sleep(0.2)
    return out


# ---------------------------------------------------------------- 네이버 검색광고 (월간 검색량)
def _num(v):
    if isinstance(v, (int, float)):
        return int(v)
    return 5 if "<" in str(v) else int(re.sub(r"\D", "", str(v)) or 0)


def searchad(keywords, key, secret, customer):
    out = {}
    uniq = list(dict.fromkeys(keywords))
    for i in range(0, len(uniq), 5):
        batch = uniq[i:i + 5]
        uri = "/keywordstool"
        ts = str(int(time.time() * 1000))
        sig = base64.b64encode(hmac.new(secret.encode(), f"{ts}.GET.{uri}".encode(), hashlib.sha256).digest()).decode()
        q = urllib.parse.urlencode({"hintKeywords": ",".join(k.replace(" ", "") for k in batch), "showDetail": 1})
        r = request(f"https://api.searchad.naver.com{uri}?{q}",
                    {"X-Timestamp": ts, "X-API-KEY": key, "X-Customer": str(customer), "X-Signature": sig})
        lst = {x["relKeyword"].upper(): x for x in r.get("keywordList", [])}
        for k in batch:
            x = lst.get(k.replace(" ", "").upper())
            if x:
                pc, mo = _num(x.get("monthlyPcQcCnt")), _num(x.get("monthlyMobileQcCnt"))
                out[k] = {"pc": pc, "mobile": mo, "total": pc + mo, "adComp": x.get("compIdx")}
        time.sleep(0.3)
    return out


# ---------------------------------------------------------------- 쿠팡 파트너스
def analyze_products(prods):
    """
    랭킹순 상위 상품 목록 → 가격 분석
    - 중앙값의 35% 미만 가격은 부속품·소량 구성 의심으로 표시하고 실질 최저가·평균에서 제외
    - 각 상품에 평균 대비 가격 차이(%)와 순위를 붙임
    """
    prods = [p for p in prods if p.get("price", 0) > 0]
    if not prods:
        return None
    prices = sorted(p["price"] for p in prods)
    med = statistics.median(prices)
    for i, p in enumerate(prods, 1):
        p["rank"] = i
        p["suspect"] = p["price"] < med * 0.35
    core = [p for p in prods if not p["suspect"]] or prods
    avg_core = mean([p["price"] for p in core])
    for p in prods:
        p["vs_avg"] = round((p["price"] / avg_core - 1) * 100, 1) if avg_core else None
    lo = min(prods, key=lambda p: p["price"])
    real_lo = min(core, key=lambda p: p["price"])
    pick = lambda p: {k: p.get(k) for k in ("rank", "title", "price", "link", "mall", "rocket")}
    return {
        "n": len(prods), "min": lo["price"], "min_item": pick(lo),
        "real_min": real_lo["price"], "real_min_item": pick(real_lo),
        "avg": round(avg_core), "avg_all": round(mean(prices)), "median": round(med), "max": prices[-1],
        "top": prods[:10],
    }


def coupang(keyword, ak, sk):
    """쿠팡 파트너스 상품 검색: 쿠팡 랭킹순 상위 10개 (판매실적·리뷰 등이 반영된 순서)"""
    path = "/v2/providers/affiliate_open_api/apis/openapi/products/search"
    query = urllib.parse.urlencode({"keyword": keyword, "limit": 10})
    signed = time.strftime("%y%m%dT%H%M%SZ", time.gmtime())
    sig = hmac.new(sk.encode(), (signed + "GET" + path + query).encode(), hashlib.sha256).hexdigest()
    auth = f"CEA algorithm=HmacSHA256, access-key={ak}, signed-date={signed}, signature={sig}"
    r = request(f"https://api-gateway.coupang.com{path}?{query}", {"Authorization": auth})
    data = r.get("data") or {}
    raw = data.get("productData", []) if isinstance(data, dict) else []
    prods = [{"title": p.get("productName", ""), "price": int(p.get("productPrice") or 0),
              "rocket": bool(p.get("isRocket")), "free_ship": bool(p.get("isFreeShipping")),
              "link": p.get("productUrl", ""), "image": p.get("productImage", ""), "mall": "쿠팡"} for p in raw]
    res = analyze_products(prods)
    if not res:
        return None
    res["rocket"] = round(sum(1 for p in res["top"] if p["rocket"]) / len(res["top"]) * 100)
    res["updated_at"] = dt.datetime.now(KST).isoformat(timespec="seconds")
    return res


# ---------------------------------------------------------------- 점수
def norm(vals):
    xs = [v for v in vals if v is not None]
    if not xs:
        return [None] * len(vals)
    lo, hi = min(xs), max(xs)
    return [None if v is None else (0.5 if hi == lo else (v - lo) / (hi - lo)) for v in vals]


def score_items(items):
    vol = [math.log10(it["search"]["total"] + 1) if it.get("search") else None for it in items]
    rel = [math.log10(it["trend"]["rel"] + 1) if it.get("trend") else None for it in items]
    nv, nr = norm(vol), norm(rel)
    for it, a, b in zip(items, nv, nr):
        parts = [(a, 0.6), (b, 0.4)]
        parts = [(x, w) for x, w in parts if x is not None]
        it["score"] = round(100 * sum(x * w for x, w in parts) / sum(w for _, w in parts)) if parts else None
        # 경쟁강도 = 등록 상품 수 ÷ 월간 검색량 (낮을수록 수요 대비 공급이 적음)
        s, n = it.get("search"), it.get("naver")
        if s and n and s["total"] > 0:
            ratio = n["total"] / s["total"]
            it["competition"] = {"ratio": round(ratio, 1),
                                 "level": "낮음" if ratio < 3 else ("보통" if ratio < 15 else "높음")}
        else:
            it["competition"] = None


# ---------------------------------------------------------------- 메인
def main():
    now = dt.datetime.now(KST)
    force = os.getenv("FORCE", "").lower() in ("true", "1", "yes")
    prev = load_json(LATEST)
    is_sample = bool(prev and prev.get("meta", {}).get("sample"))

    if prev and not is_sample and not force:
        last = dt.datetime.fromisoformat(prev["meta"]["updated_at"])
        hours = (now - last).total_seconds() / 3600
        if hours < INTERVAL_HOURS:
            log(f"마지막 수집 후 {hours:.1f}시간 경과 — {INTERVAL_HOURS:.0f}시간 주기가 아니므로 건너뜁니다.")
            return 0

    cid, csec = env("NAVER_CLIENT_ID"), env("NAVER_CLIENT_SECRET")        # 네이버 개발자센터 (쇼핑 검색 가격)
    hid, hsec = env("NAVER_HUB_CLIENT_ID"), env("NAVER_HUB_CLIENT_SECRET")  # NAVER API HUB (트렌드·뉴스·쇼핑인사이트)
    dev = dev_headers(cid, csec) if cid and csec else None
    hub = hub_headers(hid, hsec) if hid and hsec else None
    if not (dev or hub):
        log("::warning::네이버 API 키(NAVER_HUB_CLIENT_ID/SECRET 또는 NAVER_CLIENT_ID/SECRET)가 없어 수집을 건너뜁니다. (샘플 데이터 유지)")
        return 0
    log(f"키 확인: API HUB {'O' if hub else 'X'} / 개발자센터(쇼핑 가격) {'O' if dev else 'X'}")

    cfg = load_json(CONFIG)
    conf_items = cfg["items"]
    anchor = cfg.get("anchor") or conf_items[0]["keyword"]
    prev_items = {} if is_sample or not prev else {it["id"]: it for it in prev.get("items", [])}
    keywords = [c["keyword"] for c in conf_items]
    sources = {"naver_shop": False, "datalab": False, "searchad": False, "coupang": False,
               "news": False, "shop_insight": False}
    errors = []

    # 1) 검색어 트렌드 (API HUB 우선, 실패하면 개발자센터 데이터랩)
    trends = {}
    for kind, headers in (("hub", hub), ("dev", dev)):
        if not headers or trends:
            continue
        try:
            trends = datalab(keywords, anchor, DATALAB_URL[kind], headers)
            sources["datalab"] = True
            log(f"검색어트렌드({'API HUB' if kind == 'hub' else '개발자센터'}): {len(trends)}개 키워드")
        except Exception as e:
            errors.append(f"검색어트렌드({kind}): {e}")

    # 1-1) 월간 3년 추이 (계절성 예측용)
    monthly = {}
    for kind, headers in (("hub", hub), ("dev", dev)):
        if not headers or monthly:
            continue
        try:
            monthly = datalab_monthly(keywords, anchor, DATALAB_URL[kind], headers)
            log(f"월간 3년 추이: {len(monthly)}개 키워드")
        except Exception as e:
            errors.append(f"월간 추이({kind}): {e}")

    # 1-2) 쇼핑인사이트 (keywords.json 의 shop_categories 에 카테고리 코드를 넣은 경우만)
    insight = (prev or {}).get("meta", {}).get("shop_insight") if not is_sample else None
    cats = [c for c in cfg.get("shop_categories", []) if str(c.get("code", "")).strip()]
    if hub and cats:
        try:
            insight = shop_insight(cats, hub)
            sources["shop_insight"] = True
            log(f"쇼핑인사이트: {len(insight)}개 분야")
        except Exception as e:
            errors.append(f"쇼핑인사이트: {e}")

    # 2) 검색광고
    volumes = {}
    sk, ss, sc = env("NAVER_AD_API_KEY"), env("NAVER_AD_SECRET_KEY"), env("NAVER_AD_CUSTOMER_ID")
    if sk and ss and sc:
        try:
            volumes = searchad(keywords, sk, ss, sc)
            sources["searchad"] = True
            log(f"검색광고: {len(volumes)}개 키워드")
        except Exception as e:
            errors.append(f"검색광고: {e}")

    # 3) 쿠팡 대상 선정 (가장 오래된 것부터 COUPANG_MAX개)
    ca, cs = env("COUPANG_ACCESS_KEY"), env("COUPANG_SECRET_KEY")
    coupang_order = []
    if ca and cs:
        coupang_order = sorted(conf_items, key=lambda c: ((prev_items.get(c["id"]) or {}).get("coupang") or {}).get("updated_at", ""))

    # 4) 품목별 수집
    items = []
    for c in conf_items:
        p = prev_items.get(c["id"], {})
        it = {k: c.get(k) for k in ("id", "group", "sub", "name", "keyword", "cert", "memo")}
        it["naver"] = p.get("naver")
        if dev:
            try:
                it["naver"] = naver_shop(c, dev) or p.get("naver")
                sources["naver_shop"] = True
            except Exception as e:
                errors.append(f"쇼핑 {c['name']}: {e}")
        it["news"] = p.get("news")
        buzz = p.get("buzz")
        if hub:
            try:
                it["news"], buzz = hub_news(c.get("news_keyword") or c["keyword"], hub)
                sources["news"] = True
            except Exception as e:
                errors.append(f"뉴스 {c['name']}: {e}")
            time.sleep(0.1)
        it["trend"] = trends.get(c["keyword"]) or p.get("trend")
        it["search"] = volumes.get(c["keyword"]) or p.get("search")
        it["buzz"] = buzz
        it["monthly"] = monthly.get(c["keyword"]) or p.get("monthly")
        it["forecast"] = forecast_item(it["monthly"], buzz, now.strftime("%Y-%m"))
        it["coupang"] = p.get("coupang")
        items.append(it)
        log(f"  ✓ {c['name']}")

    # 4-1) 쿠팡 상품 TOP10·가격 (시간당 호출 제한 → 10개씩 회차 나눠 수집)
    by_id = {it["id"]: it for it in items}
    for rnd in range(COUPANG_ROUNDS):
        batch = coupang_order[rnd * COUPANG_MAX:(rnd + 1) * COUPANG_MAX]
        if not batch:
            break
        if rnd:
            log(f"쿠팡 호출 제한 대기 {COUPANG_WAIT // 60}분 후 {rnd + 1}회차 수집…")
            time.sleep(COUPANG_WAIT)
        for c in batch:
            try:
                by_id[c["id"]]["coupang"] = coupang(c["keyword"], ca, cs) or by_id[c["id"]]["coupang"]
                sources["coupang"] = True
                log(f"  쿠팡 ✓ {c['name']}")
            except Exception as e:
                errors.append(f"쿠팡 {c['name']}: {e}")
                if "429" in str(e):
                    break
            time.sleep(1)

    if dev and not sources["naver_shop"] and not hub:
        log("::error::네이버 쇼핑 수집이 전부 실패했습니다. 키를 확인하세요.\n" + "\n".join(errors[:5]))
        return 1
    if not any(sources.values()):
        log("::error::모든 API 호출이 실패했습니다. Secrets에 등록한 키와 API HUB Application의 API 선택을 확인하세요.\n" + "\n".join(errors[:5]))
        return 1

    score_items(items)
    rank_forecasts(items)
    latest = {
        "meta": {
            "updated_at": now.isoformat(timespec="seconds"),
            "next_update": (now + dt.timedelta(hours=72)).isoformat(timespec="seconds"),
            "sample": False, "anchor": anchor, "sources": sources, "errors": errors[:20],
            "shop_insight": insight,
            "forecast": {"base_month": now.strftime("%Y-%m"),
                         "months": [_add_month(now.strftime("%Y-%m"), h) for h in (1, 2, 3)],
                         "model": "계절지수(최근 3년 월간 검색 관심도) × 전년 대비 추세 × 최근 뉴스 관심도"},
        },
        "items": items,
    }
    save_json(LATEST, latest)

    # 5) 이력 누적
    hist = load_json(HISTORY)
    if not hist or hist.get("sample"):
        hist = {"sample": False, "dates": [], "items": {}}
    hist["dates"].append(now.date().isoformat())
    for it in items:
        h = hist["items"].setdefault(it["id"], {"min": [], "avg": [], "median": [], "score": [], "cp_avg": []})
        pad = len(hist["dates"]) - 1 - len(h["avg"])
        for key in h:
            h[key].extend([None] * pad)
        n, cp = it.get("naver") or {}, it.get("coupang") or {}
        h["min"].append(n.get("min")); h["avg"].append(n.get("avg")); h["median"].append(n.get("median"))
        h["score"].append(it.get("score")); h["cp_avg"].append(cp.get("avg"))
    if len(hist["dates"]) > HISTORY_KEEP:
        cut = len(hist["dates"]) - HISTORY_KEEP
        hist["dates"] = hist["dates"][cut:]
        for h in hist["items"].values():
            for key in h:
                h[key] = h[key][cut:]
    save_json(HISTORY, hist)

    log(f"완료: {len(items)}개 품목, 소스 {sources}")
    if errors:
        log("경고:\n  " + "\n  ".join(errors[:20]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
