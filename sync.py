import datetime, glob, json, os, re, shutil, subprocess, sys, time, traceback, urllib.request

UA = {"User-Agent": "CardTrackerSync/1.0 (personal binder app)"}
B = "https://tcgcsv.com/tcgplayer/3"
ORDER = ["Normal", "Holofoil", "1st Edition Normal", "1st Edition Holofoil",
         "Unlimited Normal", "Unlimited Holofoil", "Reverse Holofoil"]


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


def pick(rows):
    best = None
    for r in rows:
        v = r.get("marketPrice")
        if v is None:
            v = r.get("midPrice")
        if v is None:
            continue
        k = ORDER.index(r.get("subTypeName")) if r.get("subTypeName") in ORDER else 99
        if best is None or k < best[0]:
            best = (k, v)
    return None if best is None else round(float(best[1]), 2)


def prim(res):
    by = {}
    for r in res or []:
        if r.get("productId") is not None:
            by.setdefault(r["productId"], []).append(r)
    return {pid: pick(rows) for pid, rows in by.items()}


def dump(f, o):
    with open(f, "w", encoding="utf-8") as h:
        json.dump(o, h, ensure_ascii=False, separators=(",", ":"))


def save_hist(gid, days):
    f = f"data/h/{gid}.json"
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
    dump(f, {"d": dates, "p": {p: [D[d].get(p) for d in dates] for p in pids}})


def load_group(g, logos, old):
    gid = g["groupId"]
    prods = get(f"{B}/{gid}/products")["results"] or []
    pm = prim(get(f"{B}/{gid}/prices")["results"])
    cards, sealed = [], []
    for p in prods:
        ed = {x.get("name"): x.get("value") for x in (p.get("extendedData") or [])}
        row = [p["productId"], p.get("name") or "", ed.get("Number") or "", ed.get("Rarity") or "", pm.get(p["productId"])]
        if ed.get("Number"):
            cards.append(row)
        else:
            row[3] = "Sealed"
            sealed.append(row)
    if not cards and not sealed:
        return None
    name = re.sub(r"^[A-Z]{1,5}\d*(?:\.\d+)?[a-z]?:\s*", "", g.get("name") or str(gid))
    entry = {"g": gid, "n": name, "a": g.get("abbreviation"), "d": (g.get("publishedOn") or "")[:10],
             "t": len(cards), "l": logos.get(norm(name)) or old.get(gid, {}).get("l")}
    return entry, cards + sealed


def backfill(bf, today_dt, valid, H):
    d = max(datetime.date(2024, 2, 8), today_dt - datetime.timedelta(days=bf))
    while d < today_dt:
        ds = d.isoformat()
        try:
            open("/tmp/a.7z", "wb").write(get(f"https://tcgcsv.com/archive/tcgplayer/prices-{ds}.ppmd.7z", raw=True))
            subprocess.run(["7z", "x", "-y", "-o/tmp/arc", "/tmp/a.7z"], check=True, stdout=subprocess.DEVNULL)
            for f in glob.glob(f"/tmp/arc/{ds}/3/*/prices"):
                gid = int(f.split("/")[-2])
                if gid in valid:
                    pm = prim(json.load(open(f, encoding="utf-8"))["results"])
                    H.setdefault(gid, {}).setdefault(ds, {}).update(
                        {k: v for k, v in pm.items() if k in valid[gid] and v is not None})
            print("backfilled", ds, flush=True)
        except Exception as e:
            print("backfill skipped", ds, repr(e), flush=True)
        shutil.rmtree("/tmp/arc", ignore_errors=True)
        d += datetime.timedelta(days=1)
        time.sleep(1)


def main():
    os.makedirs("data/s", exist_ok=True)
    os.makedirs("data/h", exist_ok=True)
    today_dt = datetime.datetime.now(datetime.timezone.utc).date()
    today = today_dt.isoformat()
    try:
        old = {s["g"]: s for s in json.load(open("data/sets.json", encoding="utf-8"))}
    except Exception:
        old = {}
    logos = {}
    try:
        for s in get("https://api.pokemontcg.io/v2/sets?select=id,name,images", tries=2)["data"]:
            logos[norm(s["name"])] = s["images"]["logo"]
    except Exception as e:
        print("logos skipped:", repr(e), flush=True)
    sets, idx, valid, H, bad = [], [], {}, {}, 0
    groups = get(B + "/groups")["results"]
    print("groups:", len(groups), flush=True)
    for i, g in enumerate(groups):
        try:
            res = load_group(g, logos, old)
            if res:
                entry, rows = res
                gid = entry["g"]
                dump(f"data/s/{gid}.json", {"c": rows})
                sets.append(entry)
                idx.extend([r[0], gid, r[1], r[2], r[3], r[4]] for r in rows)
                valid[gid] = {r[0] for r in rows}
                H.setdefault(gid, {})[today] = {r[0]: r[4] for r in rows if r[4] is not None}
        except Exception as e:
            bad += 1
            print("skipped group", g.get("groupId"), g.get("name"), repr(e), flush=True)
        time.sleep(0.15)
        if i % 25 == 0:
            print("progress", i, "of", len(groups), flush=True)
    if not sets:
        raise RuntimeError("No sets were downloaded from tcgcsv.com (skipped %d groups)" % bad)
    bf = int(os.environ.get("BACKFILL_DAYS") or 0)
    if bf > 0:
        backfill(bf, today_dt, valid, H)
    for gid, days in H.items():
        save_hist(gid, days)
    sets.sort(key=lambda s: s["d"], reverse=True)
    dump("data/sets.json", sets)
    dump("data/idx.json", idx)
    dump("data/meta.json", {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    print("DONE sets", len(sets), "products", len(idx), "skipped groups", bad, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        print("::error::sync.py failed: " + repr(e))
        sys.exit(1)
