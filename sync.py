import datetime, glob, json, os, re, shutil, subprocess, sys, time, urllib.request

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
    return re.sub(r"[^a-z0-9]", "", s.lower())

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
    return None if best is None else round(best[1], 2)

def prim(res):
    by = {}
    for r in res:
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
        for s in get("https://api.pokemontcg.io/v2/sets?select=id,name,images")["data"]:
            logos[norm(s["name"])] = s["images"]["logo"]
    except Exception as e:
        print("logos skipped:", e)
    sets, idx, valid, H = [], [], {}, {}
    for g in get(B + "/groups")["results"]:
        gid = g["groupId"]
        try:
            prods = get(f"{B}/{gid}/products")["results"]
            pm = prim(get(f"{B}/{gid}/prices")["results"])
        except Exception as e:
            print("skip", gid, e)
            continue
        finally:
            time.sleep(0.15)
        cards, sealed = [], []
        for p in prods:
            ed = {x["name"]: x["value"] for x in p.get("extendedData", [])}
            if "Number" in ed:
                cards.append([p["productId"], p["name"], ed["Number"], ed.get("Rarity", ""), pm.get(p["productId"])])
            else:
                sealed.append([p["productId"], p["name"], "", "Sealed", pm.get(p["productId"])])
        if not cards and not sealed:
            continue
        everything = cards + sealed
        name = re.sub(r"^[A-Z]{1,5}\d*(?:\.\d+)?[a-z]?:\s*", "", g["name"])
        dump(f"data/s/{gid}.json", {"c": everything})
        sets.append({"g": gid, "n": name, "a": g.get("abbreviation"), "d": (g.get("publishedOn") or "")[:10],
                     "t": len(cards), "l": logos.get(norm(name)) or old.get(gid, {}).get("l")})
        idx.extend([c[0], gid, c[1], c[2], c[3], c[4]] for c in everything)
        valid[gid] = {c[0] for c in everything}
        H.setdefault(gid, {})[today] = {c[0]: c[4] for c in everything if c[4] is not None}
    if not sets:
        sys.exit("No data downloaded")
    bf = int(os.environ.get("BACKFILL_DAYS") or 0)
    if bf > 0:
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
            except Exception as e:
                print("backfill skipped", ds, e)
            shutil.rmtree("/tmp/arc", ignore_errors=True)
            d += datetime.timedelta(days=1)
            time.sleep(1)
    for gid, days in H.items():
        save_hist(gid, days)
    sets.sort(key=lambda s: s["d"], reverse=True)
    dump("data/sets.json", sets)
    dump("data/idx.json", idx)
    dump("data/meta.json", {"updated": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    print("sets", len(sets), "cards", len(idx))

if __name__ == "__main__":
    main()
