#!/usr/bin/env python3
"""Sweden Deals – check each deal's "tidigare pris" against real price history.

Adds a `pricecheck` object to every deal in deals.json (in place, atomic write):

  status            ok | raised_before_sale | inflated | suspect | unknown
  store_low_30d     the shop's lowest price in the 30 days before the sale started (Prisjakt)
  pre_hike_price    the shop's lowest price 31–90 days before the sale (only when raised_before_sale)
  pre_hike_month    month (Swedish) the pre-hike price was last in effect, e.g. "oktober"
  hike_date         date the shop raised the price to roughly the claimed level
  sale_start        date the price cut started (campaign start or first cut in history)
  market_low_30d    lowest price across all shops in the same window
  real_discount_pct price vs pre_hike_price (amber) or vs store_low_30d (others); null if unknown
  confidence        high | medium | low (product match)
  history_url       Prisjakt price-history page
  reason            short machine-readable reason (for unknown/suspect)
  checked_at        ISO timestamp

Rules (prisinformationslagen 7 §: previous price = shop's lowest price in the 30 days before the cut):
  inflated  (red)   claimed was > store_low_30d * 1.03 AND high-confidence match AND the history
                    covers the whole 30-day window AND the cheaper price belongs to the same offer
                    (Prisjakt's shop history is the shop's cheapest variant, i.e. a lower bound).
                    Anything weaker becomes `suspect`.
  raised_before_sale (amber)   legal per the 30-day rule, but 31–90 days before the sale the shop's
                    price (same offer, high confidence) was at/near today's price (<= today * 1.25)
                    and was raised to ~the claimed level more than 30 days before the sale.
  ok                claim matches the 30-day history.
  unknown           no match / no history / endpoint failure. Never guessed.

Source: Prisjakt's public GraphQL endpoint (no login), plus Webhallen's public product API for
campaign start dates and POWER campaign periods from the feed caveat. ~0.4 s between requests.
Results are cached per deal for the day in scripts/.pricecheck-cache.json, so a rerun is fast.
Always exits 0 (publishing must not break); on failures deals get status "unknown".

Usage:  python3 scripts/pricecheck.py deals.json [--no-cache] [--limit N]
"""
from __future__ import annotations

import argparse, json, re, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Stockholm")
PJ = "https://www.prisjakt.nu/_internal/graphql?release=2024-01-01&version=1&main=swedendeals"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
SHOP_IDS = {"POWER": 177, "Webhallen": 113, "NetOnNet": 2, "Kjell & Company": 261,
            "Elgiganten": 54, "Inet": 141, "Komplett": 33, "Komplett.se": 33, "Clas Ohlson": 167}
DELAY = 0.4
TOL = 0.03          # 3 % tolerance on the 30-day rule
NEAR = 1.25         # pre-hike price "near" today's price
MONTHS = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti",
          "september", "oktober", "november", "december"]
CACHE_DAYS = 3


class Net:
    """HTTP with polite delay and a circuit breaker (stop after repeated failures)."""
    def __init__(self):
        self.fail_streak = 0
        self.dead = False
        self.requests = 0

    def get_json(self, url, data=None):
        if self.dead:
            return None
        for attempt in range(2):
            time.sleep(DELAY)
            self.requests += 1
            try:
                req = urllib.request.Request(
                    url, data=json.dumps(data).encode() if data is not None else None,
                    headers={"User-Agent": UA, "content-type": "application/json", "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    out = json.load(r)
                self.fail_streak = 0
                return out
            except Exception:
                time.sleep(1.5)
        self.fail_streak += 1
        if self.fail_streak >= 6:
            self.dead = True
            print("pricecheck: endpoint keeps failing – remaining deals marked unknown", file=sys.stderr)
        return None

    def pj(self, query, variables=None):
        r = self.get_json(PJ, {"query": query, "variables": variables or {}})
        if not isinstance(r, dict) or r.get("errors") and not r.get("data"):
            return None
        return r.get("data")


NET = Net()

# ---------------------------------------------------------------- matching
STOP = set("och med för till the and of i på hos svart vit white black vita svarta - / , true wireless".split())


def toks(s):
    return [t for t in re.findall(r"[a-z0-9åäöéü\.]+", (s or "").lower()) if t not in STOP and t != "."]


def sim(a, b):
    A, B = set(toks(a)), set(toks(b))
    if not A or not B:
        return 0.0
    da = {t for t in A if re.search(r"\d", t)}
    miss = [t for t in da if t not in B and not any(t in x or x in t for x in B)]
    s = 0.5 * len(A & B) / len(A | B) + 0.5 * len(A & B) / len(A)
    if da:
        s *= (1 - len(miss) / len(da)) ** 1.5
    return round(s, 3)


def norm_offer(x):
    return re.sub(r"[^a-z0-9åäö]", "", (x or "").lower())


def queries_for(name):
    base = name.split(" / ")[0]
    base = re.sub(r"\([^)]*\)", " ", base)
    base = re.sub(r"\s+–\s+.*$", "", base).strip()
    t = base.replace(",", " ").split()
    out = []
    for q in [base, " ".join(t[:5]) if len(t) > 5 else "", " ".join(t[:3]) if len(t) > 3 else ""]:
        if q and q not in out:
            out.append(q)
    return out


def search(q):
    d = NET.pj('query($q:String!){ newSearch(query:$q){ results{ products{ nodes{ ... on Product { id name } } } } } }', {"q": q})
    try:
        return [n for n in d["newSearch"]["results"]["products"]["nodes"] if n and n.get("id")][:6]
    except Exception:
        return None


def offers(ids):
    d = NET.pj('query($ids:[Int!]!){ products(ids:$ids){ id name prices{ nodes{ name price{ exclShipping } store{ id } } } } }', {"ids": ids})
    try:
        return d["products"] or []
    except Exception:
        return None


def match(deal, shop_id):
    best, failed = None, False
    for q in queries_for(deal.get("name", "")):
        cands = search(q)
        if cands is None:
            failed = True
            continue
        if not cands:
            continue
        prods = offers([c["id"] for c in cands])
        if prods is None:
            failed = True
            continue
        for prod in prods:
            for o in ((prod or {}).get("prices") or {}).get("nodes") or []:
                if (o.get("store") or {}).get("id") != shop_id:
                    continue
                op = (o.get("price") or {}).get("exclShipping")
                s = max(sim(deal["name"], o.get("name")), sim(deal["name"], prod.get("name")))
                pm = op is not None and abs(op - deal["price_sek"]) <= max(2, 0.01 * deal["price_sek"])
                score = s + (0.4 if pm else 0)
                if not best or score > best["score"]:
                    best = {"pid": prod["id"], "offer_name": o.get("name") or "", "offer_price": op,
                            "sim": s, "price_match": pm, "score": score}
        if best and best["price_match"] and best["sim"] >= 0.5:
            break
    if not best:
        return None, failed
    if best["price_match"] and best["sim"] >= 0.5:
        best["confidence"] = "high"
    elif (best["price_match"] and best["sim"] >= 0.3) or best["sim"] >= 0.55:
        best["confidence"] = "medium"
    else:
        best["confidence"] = "low"
    return best, failed


def history(pid, shop_id):
    d = NET.pj('''query($id:Int!,$s:[Int!]!){ product(id:$id){
      shopHistory(shopIds:$s, timeRange:ThreeMonths){ productHistory{ historyItems{ date price name isMembershipPrice } } }
      historyV2(timeRange:ThreeMonths){ historyItems{ date price shopName isMembershipPrice } } } }''', {"id": pid, "s": [shop_id]})
    try:
        p = d["product"]
        sh = p["shopHistory"][0]["productHistory"]["historyItems"] if p["shopHistory"] else []
        return [i for i in sh if not i.get("isMembershipPrice")], \
               [i for i in (p["historyV2"]["historyItems"] or []) if not i.get("isMembershipPrice")]
    except Exception:
        return None, None

# ---------------------------------------------------------------- time series helpers


def pdate(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def in_effect(items, start, end):
    """Change points -> list of (item, effective_from, effective_to) overlapping [start, end)."""
    it = sorted(items, key=lambda i: i["date"])
    out = []
    for k, i in enumerate(it):
        a = pdate(i["date"])
        b = pdate(it[k + 1]["date"]) if k + 1 < len(it) else datetime.max.replace(tzinfo=timezone.utc)
        if b > start and a < end:
            out.append((i, max(a, start), min(b, end)))
    return out


def covered(items, start):
    return any(pdate(i["date"]) <= start for i in items)


def chain_start(items, price):
    it = sorted(items, key=lambda i: i["date"])
    if not it or abs(it[-1]["price"] - price) > max(2, 0.02 * price):
        return None
    k = len(it) - 1
    while k > 0 and it[k - 1]["price"] > it[k]["price"]:
        k -= 1
    return pdate(it[k + 1]["date"]) if k < len(it) - 1 else None


def campaign_start(deal, wh):
    shop = deal.get("shop")
    if shop == "POWER":
        m = re.search(r"campaign (\d{1,2})/(\d{1,2})-", deal.get("caveat") or "")
        if m:
            now = datetime.now(TZ)
            mo, d = int(m.group(2)), int(m.group(1))
            y = now.year - (1 if mo > now.month + 1 else 0)
            try:
                return datetime(y, mo, d, tzinfo=TZ)
            except ValueError:
                return None
    if shop == "Webhallen" and wh and wh.get("startAt"):
        try:
            return datetime.fromisoformat(wh["startAt"]).replace(tzinfo=TZ)
        except ValueError:
            return None
    return None


def webhallen(url):
    m = re.search(r"webhallen\.com/se/product/(\d+)", url or "")
    if not m:
        return None
    d = NET.get_json(f"https://www.webhallen.com/api/product/{m.group(1)}")
    p = (d or {}).get("product") if isinstance(d, dict) else None
    if not p:
        return None
    return {"startAt": (p.get("price") or {}).get("startAt"), "lowestPrice": (p.get("lowestPrice") or {}).get("price")}


def local_date(dt):
    return dt.astimezone(TZ).date().isoformat()

# ---------------------------------------------------------------- core


def check(deal, now):
    pc = {"status": "unknown", "store_low_30d": None, "market_low_30d": None, "pre_hike_price": None,
          "pre_hike_month": None, "hike_date": None, "sale_start": None, "real_discount_pct": None,
          "confidence": None, "history_url": None, "reason": None, "source": "prisjakt"}
    price, was, shop = deal.get("price_sek"), deal.get("was_price_sek"), deal.get("shop")
    if not isinstance(price, (int, float)) or not isinstance(was, (int, float)) or was <= 0 or price <= 0:
        pc["reason"] = "no_previous_price"; return pc
    shop_id = SHOP_IDS.get(shop)
    if not shop_id:
        pc["reason"] = "shop_not_supported"; return pc
    if NET.dead:
        pc["reason"] = "endpoint_unavailable"; return pc
    m, failed = match(deal, shop_id)
    if not m:
        pc["reason"] = "endpoint_unavailable" if failed and NET.dead else "no_match"; return pc
    pc["confidence"] = m["confidence"]
    if m["confidence"] == "low":
        pc["reason"] = "uncertain_match"; return pc
    pc["history_url"] = f"https://www.prisjakt.nu/produkt.php?p={m['pid']}#statistics"
    sh, mk = history(m["pid"], shop_id)
    if sh == []:  # Prisjakt occasionally returns an empty shop history – retry once
        time.sleep(2); sh, mk = history(m["pid"], shop_id)
    if sh is None:
        pc["reason"] = "endpoint_unavailable"; return pc
    if not sh:
        pc["reason"] = "no_shop_history"; return pc
    wh = webhallen(deal.get("url")) if shop == "Webhallen" else None
    cstart, hstart = campaign_start(deal, wh), chain_start(sh, price)
    if cstart and hstart and hstart < cstart - timedelta(days=14):
        hstart = None
    start = min([d for d in (cstart, hstart) if d], default=None)
    if not start:
        pc["reason"] = "no_sale_start"; return pc
    pc["sale_start"] = local_date(start)
    w0 = start - timedelta(days=30)
    win = in_effect(sh, w0, start)
    if not win:
        pc["reason"] = "no_price_in_window"; return pc
    lo = min(i["price"] for i, _, _ in win)
    full = covered(sh, w0)
    pc["store_low_30d"] = lo
    mwin = in_effect(mk or [], w0, start)
    if mwin:
        pc["market_low_30d"] = min(i["price"] for i, _, _ in mwin)

    if was > lo * (1 + TOL):
        cheap = [i for i, _, _ in win if i["price"] * (1 + TOL) < was]
        same = all(norm_offer(i.get("name")) == norm_offer(m["offer_name"]) for i in cheap)
        if m["confidence"] == "high" and full and same:
            pc["status"] = "inflated"
        else:
            pc["status"] = "suspect"
            pc["reason"] = "other_variant" if not same else ("partial_history" if not full else "medium_match")
        pc["real_discount_pct"] = round(100 * (1 - price / lo), 1) if pc["status"] == "inflated" else None
        return pc
    if not full:
        pc["reason"] = "partial_history"; return pc

    pc["status"] = "ok"
    pc["real_discount_pct"] = round(100 * (1 - price / lo), 1)
    # pre-sale hike: 31–90 days before the sale
    if m["confidence"] == "high":
        pre = in_effect(sh, start - timedelta(days=90), w0)
        pre_same = [(i, a, b) for i, a, b in pre if norm_offer(i.get("name")) == norm_offer(m["offer_name"])]
        if pre_same:
            plo = min(i["price"] for i, _, _ in pre_same)
            if plo * (1 + TOL) < was and plo <= price * NEAR:
                last = max((x for x in pre_same if x[0]["price"] == plo), key=lambda x: x[2])
                end_local = (last[2] - timedelta(seconds=1)).astimezone(TZ)
                hikes = [i for i in sorted(sh, key=lambda i: i["date"])
                         if pdate(i["date"]) >= last[2] and pdate(i["date"]) < start and i["price"] * (1 + TOL) >= was]
                if hikes and pdate(hikes[0]["date"]) <= w0 + timedelta(days=1):
                    pc.update(status="raised_before_sale", pre_hike_price=plo,
                              pre_hike_month=MONTHS[end_local.month - 1],
                              hike_date=local_date(pdate(hikes[0]["date"])),
                              real_discount_pct=round(100 * (1 - price / plo), 1))
    return pc


def cache_key(d):
    return "|".join(str(d.get(k)) for k in ("url", "price_sek", "was_price_sek", "caveat"))


def main():
    ap = argparse.ArgumentParser(description="Add pricecheck info to deals.json")
    ap.add_argument("deals_json", nargs="?", default=str(Path(__file__).resolve().parent.parent / "deals.json"))
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    path = Path(a.deals_json)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        deals = data["deals"]
        assert isinstance(deals, list)
    except Exception as e:
        print(f"pricecheck: could not read {path}: {e} – nothing changed", file=sys.stderr)
        return 0
    cache_path = Path(__file__).resolve().parent / ".pricecheck-cache.json"
    cache = {}
    if not a.no_cache and cache_path.is_file():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    now = datetime.now(TZ)
    today = now.date().isoformat()
    cache = {k: v for k, v in cache.items()
             if v.get("day") and (now.date() - datetime.fromisoformat(v["day"]).date()).days < CACHE_DAYS}
    t0, hits = time.time(), 0
    for n, d in enumerate(deals, 1):
        if not isinstance(d, dict):
            continue
        if a.limit and n > a.limit:
            break
        key = cache_key(d)
        c = cache.get(key)
        if c and c.get("day") == today and c["pc"].get("reason") != "endpoint_unavailable":
            pc = dict(c["pc"]); hits += 1
        else:
            try:
                pc = check(d, now)
            except Exception as e:
                pc = {"status": "unknown", "reason": "error: %s" % type(e).__name__}
            pc["checked_at"] = now.isoformat(timespec="seconds")
            if pc.get("reason") != "endpoint_unavailable":
                cache[key] = {"day": today, "pc": pc}
        d["pricecheck"] = pc
        print(f"[{n}/{len(deals)}] {pc['status']:18} {str(d.get('shop'))[:10]:10} {str(d.get('name'))[:60]}", flush=True)
    counts = {}
    for d in deals:
        s = (d.get("pricecheck") or {}).get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    data["pricecheck"] = {"checked_at": now.isoformat(timespec="seconds"), "source": "Prisjakt prishistorik",
                          "rule": "lägsta pris hos butiken 30 dagar före prissänkningen (3 % tolerans)",
                          "counts": counts, "runtime_s": round(time.time() - t0), "requests": NET.requests,
                          "cache_hits": hits}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    try:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    print("pricecheck:", json.dumps(data["pricecheck"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # never break publishing
        print(f"pricecheck: unexpected error {e!r} – deals.json left as is", file=sys.stderr)
        sys.exit(0)
