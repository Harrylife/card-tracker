import datetime, glob, json, os, re, shutil, subprocess, sys, time, traceback, unicodedata, urllib.parse, urllib.request

UA = {"User-Agent": "CardTrackerSync/1.0 (personal binder app)"}
ROOT = "https://tcgcsv.com/tcgplayer"
ORDER = ["Normal", "Holofoil", "1st Edition Normal", "1st Edition Holofoil",
         "Unlimited Normal", "Unlimited Holofoil", "Reverse Holofoil"]
FIX = {"mr-mime": "Mr. Mime", "mime-jr": "Mime Jr.", "mr-rime": "Mr. Rime", "farfetchd": "Farfetch'd",
       "sirfetchd": "Sirfetch'd", "nidoran-f": "Nidoran♀", "nidoran-m": "Nidoran♂", "type-null": "Type: Null",
       "ho-oh": "Ho-Oh", "porygon-z": "Porygon-Z", "jangmo-o": "Jangmo-o", "hakamo-o": "Hakamo-o",
       "kommo-o": "Kommo-o", "flabebe": "Flabébé", "wo-chien": "Wo-Chien", "chien-pao": "Chien-Pao",
       "ting-lu": "Ting-Lu", "chi-yu": "Chi-Yu"}


def get(u, raw=False, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=90) as r:
                b = r.read()
            return b if raw else json.loads(b)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def nz(s):
    s = (s or "").replace("♀", " f").replace("♂", " m")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


SYM_SRC = "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master/sets/en.json"


def sym_key(s):
    s = re.sub(r"^[A-Za-z]{1,5}\d*(?:\.\d+)?[a-z]?\s*[:\-\u2013\u2014]\s*", "", s or "")
    k = nz(s)
    for suf in ("baseset", "set"):
        if k.endswith(suf) and len(k) > len(suf) + 2:
            k = k[: -len(suf)]
    return k or "base"


def day_gap(a, b):
    try:
        f = lambda x: datetime.date.fromisoformat(x.replace("/", "-")[:10])
        return abs((f(a) - f(b)).days)
    except Exception:
        return 0


def add_symbols(sets):
    """Save each set's emblem (the little symbol printed on the card) so the camera scanner can compare it.
    Best effort: any failure just means fewer sets have a symbol."""
    import difflib
    base = "data/sym"
    os.makedirs(base, exist_ok=True)
    try:
        src = get(SYM_SRC, tries=2)
    except Exception as e:
        print("symbols: list unavailable:", repr(e), flush=True)
        src = []
    by_key = {}
    for x in src:
        by_key.setdefault(sym_key(x.get("name")), []).append(x)
    fails = got = 0
    for s in sets:
        f = f"{base}/{s['g']}.png"
        if os.path.exists(f):
            s["y"] = 1
            continue
        if fails >= 5 or not by_key:
            continue
        key = sym_key(s["n"])
        cand, gap = by_key.get(key, []), 400
        if not cand:
            m = difflib.get_close_matches(key, list(by_key), n=1, cutoff=0.9)
            cand, gap = (by_key[m[0]] if m else []), 120
        cand = [x for x in cand if day_gap(s.get("d") or "", x.get("releaseDate") or "") <= gap]
        if not cand:
            continue
        pick = min(cand, key=lambda x: day_gap(s.get("d") or "", x.get("releaseDate") or ""))
        url = (pick.get("images") or {}).get("symbol") or f"https://images.pokemontcg.io/{pick['id']}/symbol.png"
        try:
            b = get(url, raw=True, tries=1)
            if b[:4] != b"\x89PNG":
                raise ValueError("not a png")
            with open(f, "wb") as h:
                h.write(b)
            s["y"] = 1
            got += 1
            fails = 0
        except Exception as e:
            fails += 1
            print("symbol skipped:", s["n"], repr(e), flush=True)
        time.sleep(0.05)
    print("symbols: new", got, "total", sum(1 for s in sets if s.get("y")), flush=True)


def price_of(r):
    v = r.get("marketPrice")
    if v is None:
        v = r.get("midPrice")
    return None if v is None else round(float(v), 2)


def sub_key(s):
    return ORDER.index(s) if s in ORDER else 99


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


def dump(f, o):
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "w", encoding="utf-8") as h:
        json.dump(o, h, ensure_ascii=False, separators=(",", ":"))


def save_hist(base, gid, days):
    f = f"{base}/h/{gid}.json"
    D = {}
    try:
        h = json.load(open(f, encoding="utf-8"))
        for pid, arr in h["p"].items():
            for ds, v in zip(h["d"], arr):
                if v is not None:
                    D.setdefault(ds, {})[pid] = v
    except Exception:
        pass
    for ds, vals in days.items():
        D.setdefault(ds, {}).update({str(k): v for k, v in vals.items()})
    dates = sorted(D)
    pids = sorted({p for v in D.values() for p in v})
    pm = {p: [D[d].get(p) for d in dates] for p in pids}
    dump(f, {"d": dates, "p": pm})
    return dates, pm


def load_group(cat, base, g, logos, old):
    gid = g["groupId"]
    prods = get(f"{ROOT}/{cat}/{gid}/products")["results"] or []
    prices = get(f"{ROOT}/{cat}/{gid}/prices")["results"] or []
    subs = {}
    for r in prices:
        if r.get("productId") is not None:
            subs.setdefault(r["productId"], {}).setdefault(r.get("subTypeName") or "Normal", price_of(r))
    rows, rmap, ncards = [], {}, 0
    for p in prods:
        pid = p["productId"]
        ed = {x.get("name"): x.get("value") for x in (p.get("extendedData") or [])}
        num = ed.get("Number") or ""
        vs = sorted((subs.get(pid) or {"Normal": None}).items(), key=lambda kv: (sub_key(kv[0]), kv[0]))
        if num:
            ncards += 1
        else:
            vs = vs[:1]
        for k, (s, v) in enumerate(vs):
            rid = str(pid) if k == 0 else f"{pid}-{slug(s)}"
            rmap[(pid, s)] = rid
            rows.append([rid, p.get("name") or "", num, (ed.get("Rarity") or "") if num else "Sealed", v,
                         s if num else "", pid])
    if not rows:
        return None
    name = re.sub(r"^[A-Z]{1,5}\d*(?:\.\d+)?[a-z]?:\s*", "", g.get("name") or str(gid))
    entry = {"g": gid, "n": name, "a": g.get("abbreviation"), "d": (g.get("publishedOn") or "")[:10],
             "t": ncards, "l": logos.get(norm(name)) or old.get(gid, {}).get("l")}
    return entry, rows, rmap


def run_category(cat, base, logos, today):
    os.makedirs(f"{base}/s", exist_ok=True)
    os.makedirs(f"{base}/h", exist_ok=True)
    try:
        old = {s["g"]: s for s in json.load(open(f"{base}/sets.json", encoding="utf-8"))}
    except Exception:
        old = {}
    st = {"sets": [], "idx": [], "rmap": {}, "H": {}}
    groups = get(f"{ROOT}/{cat}/groups")["results"]
    print("category", cat, "groups:", len(groups), flush=True)
    bad = 0
    for i, g in enumerate(groups):
        try:
            res = load_group(cat, base, g, logos, old)
            if res:
                entry, rows, rmap = res
                gid = entry["g"]
                dump(f"{base}/s/{gid}.json", {"c": rows})
                st["sets"].append(entry)
                st["idx"].extend([r[0], gid, r[1], r[2], r[3], r[4], r[5], r[6]] for r in rows)
                st["rmap"][gid] = rmap
                st["H"].setdefault(gid, {})[today] = {r[0]: r[4] for r in rows if r[4] is not None}
        except Exception as e:
            bad += 1
            print("skipped group", g.get("groupId"), g.get("name"), repr(e), flush=True)
        time.sleep(0.15)
        if i % 50 == 0:
            print("progress", cat, i, "of", len(groups), flush=True)
    if not st["sets"]:
        raise RuntimeError(f"No sets downloaded for category {cat} (skipped {bad})")
    st["bad"] = bad
    return st


def backfill(bf, today_dt, cats):
    d = max(datetime.date(2024, 2, 8), today_dt - datetime.timedelta(days=bf))
    while d < today_dt:
        ds = d.isoformat()
        try:
            open("/tmp/a.7z", "wb").write(get(f"https://tcgcsv.com/archive/tcgplayer/prices-{ds}.ppmd.7z", raw=True))
            subprocess.run(["7z", "x", "-y", "-o/tmp/arc", "/tmp/a.7z"], check=True, stdout=subprocess.DEVNULL)
            for cat, st in cats.items():
                for f in glob.glob(f"/tmp/arc/{ds}/{cat}/*/prices"):
                    gid = int(f.split("/")[-2])
                    rmap = st["rmap"].get(gid)
                    if not rmap:
                        continue
                    out = st["H"].setdefault(gid, {}).setdefault(ds, {})
                    for r in json.load(open(f, encoding="utf-8"))["results"] or []:
                        rid = rmap.get((r.get("productId"), r.get("subTypeName") or "Normal"))
                        v = price_of(r)
                        if rid and v is not None:
                            out[rid] = v
            print("backfilled", ds, flush=True)
        except Exception as e:
            print("backfill skipped", ds, repr(e), flush=True)
        shutil.rmtree("/tmp/arc", ignore_errors=True)
        d += datetime.timedelta(days=1)
        time.sleep(1)


def pretty(slugname):
    return FIX.get(slugname) or slugname.replace("-", " ").title()


def tokens(name):
    n = re.sub(r"\(.*?\)|\[.*?\]", " ", name or "")
    n = re.sub(r"\s+-\s+\S.*$", "", n)
    n = re.sub(r"(?i)[-–](ex|gx|v|vmax|vstar|vunion|break|star)\b", r" \1", n)
    n = n.replace("♀", " f").replace("♂", " m")
    return [w for w in re.split(r"[\s,:&/+]+", n) if w]


def build_dex(idx):
    try:
        sp = get("https://pokeapi.co/api/v2/pokemon-species?limit=2000", tries=3)["results"]
    except Exception as e:
        print("dex skipped:", repr(e), flush=True)
        return None
    names, look = [], {}
    for e in sp:
        did = int(e["url"].rstrip("/").split("/")[-1])
        nm = pretty(e["name"])
        names.append((did, nm))
        look[nz(nm)] = did
        look[nz(e["name"])] = did
    names.sort()
    m = {}
    for r in idx:
        if not r[3] or str(r[0]) != str(r[7]):
            continue
        w = [nz(t) for t in tokens(r[2])]
        i = 0
        while i < len(w):
            for k in (3, 2, 1):
                key = "".join(w[i:i + k])
                if i + k <= len(w) and key in look:
                    m.setdefault(look[key], set()).add(r[7])
                    i += k
                    break
            else:
                i += 1
    return {"n": [[d, n, len(m.get(d, ()))] for d, n in names],
            "m": {str(d): sorted(v) for d, v in m.items()}}


def trending(idx, chg, sets, today_dt):
    cand = []
    for r in idx:
        c = chg.get(r[0])
        if r[3] and c and c[0] and c[1] and c[0] >= 5 and c[1] >= 5 and c[0] != c[1]:
            cand.append((c[0] / c[1] - 1, r))
    if len(cand) >= 10:
        cand.sort(key=lambda x: -x[0])
        return {"b": "7-day change", "t": [r[:1] + r[1:8] + [round(p * 100, 1)] for p, r in cand[:40]]}
    cut = (today_dt - datetime.timedelta(days=540)).isoformat()
    recent = {s["g"] for s in sets if s["d"] >= cut}
    top = sorted((r for r in idx if r[3] and r[1] in recent and r[5]), key=lambda r: -r[5])[:40]
    return {"b": "highest value, recent sets", "t": [r[:8] + [None] for r in top]}


def kr_numkey(n):
    """Card number reduced to a comparable key: '001/071' -> '1', 'TG01/TG30' -> 'tg1'."""
    s = re.sub(r"\s+", "", (n or "").split("/")[0].lower())
    return re.sub(r"(?<!\d)0+(?=\d)", "", s)


def kr_en(name):
    """English name of a Japanese product with the trailing card number removed."""
    n = re.sub(r"\s*[-\u2013]\s*[A-Za-z]*\d+(?:/[A-Za-z]*\d+)?\s*$", "", name or "")
    n = re.sub(r"\s*\(\s*[A-Za-z]*\d+(?:/[A-Za-z]*\d+)?\s*\)\s*$", "", n)
    return n.strip()


def build_korean(jp):
    """Korean sets and cards from TCGdex. Prices are not published for Korean cards, so each card
    borrows the price of the Japanese card with the same set code and number (an estimate, flagged
    with est=1 on the set), and each matched set also lists the Japanese sealed products."""
    api = os.environ.get("KR_API") or "https://api.tcgdex.net/v2/ko"
    listing = get(f"{api}/sets", tries=3)
    if not isinstance(listing, list) or not listing:
        raise RuntimeError("the Korean set list came back empty")
    print("Korean sets listed:", len(listing), flush=True)
    jsets, jrows = {}, {}
    if jp:
        for s in jp["sets"]:
            jsets.setdefault(norm(s.get("a")), []).append(s)
        for r in jp["idx"]:
            jrows.setdefault(r[1], []).append(r)
    base = "data/kr"
    os.makedirs(f"{base}/s", exist_ok=True)
    sets, bad, matched, ncards, priced, miss, shown = [], 0, 0, 0, 0, [], False
    for ls in listing:
        sid = ls.get("id")
        if not sid:
            continue
        try:
            d = get(f"{api}/sets/{urllib.parse.quote(str(sid))}", tries=3)
        except Exception as e:
            bad += 1
            print("skipped Korean set", sid, repr(e), flush=True)
            continue
        time.sleep(0.08)
        if not shown:
            shown = True
            print("sample Korean set:", json.dumps(d, ensure_ascii=False)[:500], flush=True)
        cards = [c for c in (d.get("cards") or []) if isinstance(c, dict)]
        if not cards:
            continue
        g = slug(str(sid))
        name = (d.get("name") or ls.get("name") or str(sid)).strip()
        kd = str(d.get("releaseDate") or "")[:10]
        cands = jsets.get(norm(sid)) or []
        js = min(cands, key=lambda x: day_gap(x.get("d") or "", kd) if kd else 0) if cands else None
        byn, sealed = {}, []
        if js:
            matched += 1
            for r in jrows.get(js["g"], []):
                if r[3]:
                    byn.setdefault(kr_numkey(r[3]), []).append(r)
                else:
                    sealed.append(r)
        else:
            miss.append(str(sid))
        rows, seen = [], set()
        for c in cards:
            num = str(c.get("localId") or "")
            rid = "k-" + slug(str(c.get("id") or f"{sid}-{num}"))
            while rid in seen:
                rid += "x"
            seen.add(rid)
            ko = (c.get("name") or "").strip()
            img = c.get("image") or None
            jr = sorted(byn.get(kr_numkey(num), []), key=lambda r: (sub_key(r[6]), r[6]))
            if jr:
                en = kr_en(jr[0][2])
                nm = f"{ko} \u00b7 {en}" if en and ko and en.lower() != ko.lower() else (ko or en)
                for j, r in enumerate(jr):
                    rows.append([rid if j == 0 else f"{rid}-{slug(r[6])}", nm, num, r[4] or "", r[5], r[6], rid, img])
                    priced += 1 if r[5] else 0
            else:
                rows.append([rid, ko, num, "", None, "", rid, img])
        ncards += len(cards)
        for r in sealed:
            rows.append([f"k-{g}-s{r[7]}", r[2], "", "Sealed", r[5], "", r[7], None])
        dump(f"{base}/s/{g}.json", {"c": rows})
        logo = d.get("logo") or ls.get("logo")
        en_name = js["n"] if js else ""
        sets.append({"g": g, "n": f"{en_name} \u2014 {name}" if en_name and en_name != name else name,
                     "a": str(sid), "d": kd or (js or {}).get("d") or "", "t": len(cards),
                     "l": (logo + ".png") if logo and not re.search(r"\.\w{3,4}$", logo) else logo,
                     "est": 1 if js else 0})
    if not sets:
        raise RuntimeError(f"no Korean sets could be loaded (skipped {bad})")
    sets.sort(key=lambda s: s["d"], reverse=True)
    dump(f"{base}/sets.json", sets)
    dump(f"{base}/meta.json", {"sets": len(sets), "matched": matched, "cards": ncards, "priced_rows": priced,
                               "updated": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    print("Korean:", len(sets), "sets,", ncards, "cards,", matched, "sets matched to Japanese,", priced,
          "priced rows, skipped", bad, flush=True)
    if miss:
        print("Korean sets without a Japanese match (no price estimate):", ", ".join(miss[:60]),
              "..." if len(miss) > 60 else "", flush=True)


def main():
    today_dt = datetime.datetime.now(datetime.timezone.utc).date()
    today = today_dt.isoformat()
    logos = {}
    try:
        for s in get("https://api.pokemontcg.io/v2/sets?select=id,name,images", tries=2)["data"]:
            logos[norm(s["name"])] = s["images"]["logo"]
    except Exception as e:
        print("logos from api skipped:", repr(e), flush=True)
        try:
            for s in get(SYM_SRC, tries=2):
                logos[norm(s["name"])] = s["images"]["logo"]
        except Exception as e2:
            print("logos skipped:", repr(e2), flush=True)
    jp = 85
    try:
        for c in get(f"{ROOT}/categories")["results"]:
            if (c.get("name") or "").strip().lower() == "pokemon japan":
                jp = c["categoryId"]
    except Exception as e:
        print("categories lookup skipped:", repr(e), flush=True)
    cats = {3: run_category(3, "data", logos, today)}
    try:
        cats[jp] = run_category(jp, "data/jp", {}, today)
    except Exception as e:
        print("Japanese data skipped:", repr(e), flush=True)
    try:
        build_korean(cats.get(jp))
    except Exception as e:
        print("Korean data skipped:", repr(e), flush=True)
    bf = int(os.environ.get("BACKFILL_DAYS") or 0)
    if bf > 0:
        backfill(bf, today_dt, cats)
    week = (today_dt - datetime.timedelta(days=7)).isoformat()
    chg = {}
    for cat, st in cats.items():
        base = "data" if cat == 3 else "data/jp"
        for gid, days in st["H"].items():
            dates, pm = save_hist(base, gid, days)
            if cat == 3:
                old_i = max([i for i, d in enumerate(dates) if d <= week] or [-1])
                for rid, arr in pm.items():
                    last = next((v for v in reversed(arr) if v is not None), None)
                    old = None
                    if old_i >= 0:
                        old = next((v for v in reversed(arr[:old_i + 1]) if v is not None), None)
                    chg[rid] = (last, old)
        st["sets"].sort(key=lambda s: s["d"], reverse=True)
        if cat == 3:
            try:
                add_symbols(st["sets"])
            except Exception as e:
                print("symbols skipped:", repr(e), flush=True)
        dump(f"{base}/sets.json", st["sets"])
        dump(f"{base}/idx.json", st["idx"])
    en = cats[3]
    dx = build_dex(en["idx"])
    if dx:
        dump("data/dex.json", dx)
    dump("data/trending.json", trending(en["idx"], chg, en["sets"], today_dt))
    dump("data/meta.json", {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    print("DONE", {c: (len(s["sets"]), len(s["idx"]), s["bad"]) for c, s in cats.items()}, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        print("::error::sync.py failed: " + repr(e))
        sys.exit(1)
