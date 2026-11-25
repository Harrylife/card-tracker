import datetime, glob, json, os, re, shutil, subprocess, sys, time, traceback, unicodedata, urllib.error, urllib.parse, urllib.request

UA = {"User-Agent": "CardTrackerSync/1.0 (personal binder app)"}
ROOT = "https://tcgcsv.com/tcgplayer"
ORDER = ["Normal", "Holofoil", "1st Edition Normal", "1st Edition Holofoil",
         "Unlimited Normal", "Unlimited Holofoil", "Reverse Holofoil"]
FIX = {"mr-mime": "Mr. Mime", "mime-jr": "Mime Jr.", "mr-rime": "Mr. Rime", "farfetchd": "Farfetch'd",
       "sirfetchd": "Sirfetch'd", "nidoran-f": "Nidoran♀", "nidoran-m": "Nidoran♂", "type-null": "Type: Null",
       "ho-oh": "Ho-Oh", "porygon-z": "Porygon-Z", "jangmo-o": "Jangmo-o", "hakamo-o": "Hakamo-o",
       "kommo-o": "Kommo-o", "flabebe": "Flabébé", "wo-chien": "Wo-Chien", "chien-pao": "Chien-Pao",
       "ting-lu": "Ting-Lu", "chi-yu": "Chi-Yu"}


class Missing(Exception):
    """The page does not exist (a day tcgcsv has no archive for)."""


def get(u, raw=False, tries=5, quick404=False):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=90) as r:
                b = r.read()
            return b if raw else json.loads(b)
        except urllib.error.HTTPError as e:
            if quick404 and e.code in (403, 404):
                raise Missing(u)
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))
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


ARCH0 = datetime.date(2024, 2, 8)  # the first day tcgcsv keeps a price archive for: nothing older exists
RECENT = 120                       # the last 120 days are kept day by day; older history is kept once a week (Sundays)


def keep_day(ds, today_dt):
    d = datetime.date.fromisoformat(ds)
    return d >= today_dt - datetime.timedelta(days=RECENT) or d.weekday() == 6 or d == ARCH0


def save_hist(base, gid, days, today_dt=None):
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
    if today_dt:
        D = {k: v for k, v in D.items() if keep_day(k, today_dt)}
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


DONE_F = "data/backfill_days.json"


def load_done():
    """Days whose archive has already been loaded: the marker file, plus every day found in a few saved history files."""
    done = set()
    try:
        done.update(json.load(open(DONE_F, encoding="utf-8")))
    except Exception:
        pass
    for f in sorted(glob.glob("data/h/*.json"))[:6]:
        try:
            done.update(json.load(open(f, encoding="utf-8"))["d"])
        except Exception:
            pass
    return done


def backfill_dates(spec, today_dt, done):
    """Days still to load, newest first: every day of the last RECENT days and one Sunday a week before that,
    back to ARCH0 ('all') or as far as `spec` days."""
    spec = str(spec).strip().lower()
    start = ARCH0 if spec in ("all", "max") else max(ARCH0, today_dt - datetime.timedelta(days=int(spec)))
    out, d = [], start
    while d < today_dt:
        ds = d.isoformat()
        if ds not in done and keep_day(ds, today_dt):
            out.append(ds)
        d += datetime.timedelta(days=1)
    return out[::-1]


def extract(ds):
    """Unpack one archive, only the Pokemon categories if 7z accepts the filter, everything if not."""
    ids = list(CAT_IDS) or [3, 85]
    flt = [f"-ir!{ds}/{c}/*" for c in ids]
    try:
        subprocess.run(["7z", "x", "-y", "-o/tmp/arc", "/tmp/a.7z"] + flt, check=True, stdout=subprocess.DEVNULL)
        if glob.glob(f"/tmp/arc/{ds}/*/*/prices"):
            return
    except Exception:
        pass
    shutil.rmtree("/tmp/arc", ignore_errors=True)
    subprocess.run(["7z", "x", "-y", "-o/tmp/arc", "/tmp/a.7z"], check=True, stdout=subprocess.DEVNULL)


CAT_IDS = set()


def flush_hist(chunk, today_dt):
    for (cat, gid), days in chunk.items():
        save_hist("data" if cat == 3 else "data/jp", gid, days, today_dt)
    chunk.clear()


def backfill(spec, today_dt, cats, budget_s=270 * 60):
    """Loads past prices from the tcgcsv archive. Safe to run again: days already loaded are skipped, the work is
    saved every 30 days, and it stops cleanly when the time budget is used up. Returns (days loaded, days left)."""
    CAT_IDS.clear()
    CAT_IDS.update(cats)
    done = load_done()
    todo = backfill_dates(spec, today_dt, done)
    print(f"history: {len(todo)} days to load", flush=True)
    t0, chunk, since, got = time.time(), {}, 0, 0
    for ds in todo:
        if time.time() - t0 > budget_s:
            print("history: time budget used up, will continue next run", flush=True)
            break
        try:
            try:
                open("/tmp/a.7z", "wb").write(get(f"https://tcgcsv.com/archive/tcgplayer/prices-{ds}.ppmd.7z", raw=True, quick404=True))
            except Missing:
                done.add(ds)
                print("backfill: no archive for", ds, flush=True)
                continue
            extract(ds)
            for cat, st in cats.items():
                for f in glob.glob(f"/tmp/arc/{ds}/{cat}/*/prices"):
                    gid = int(f.split("/")[-2])
                    rmap = st["rmap"].get(gid)
                    if not rmap:
                        continue
                    out = chunk.setdefault((cat, gid), {}).setdefault(ds, {})
                    for r in json.load(open(f, encoding="utf-8"))["results"] or []:
                        rid = rmap.get((r.get("productId"), r.get("subTypeName") or "Normal"))
                        v = price_of(r)
                        if rid and v is not None:
                            out[rid] = v
            done.add(ds)
            got += 1
            since += 1
            print("backfilled", ds, flush=True)
        except Exception as e:
            print("backfill skipped", ds, repr(e), flush=True)
        shutil.rmtree("/tmp/arc", ignore_errors=True)
        if since >= 30:
            flush_hist(chunk, today_dt)
            dump(DONE_F, sorted(done))
            since = 0
        time.sleep(1)
    flush_hist(chunk, today_dt)
    dump(DONE_F, sorted(done))
    left = len([d for d in todo if d not in done])
    print(f"history: {got} days loaded, {left} still to do", flush=True)
    return got, left


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


# Every Korean and Chinese set, grouped. TCGdex only has card data for some of them, so the rest are built from the
# Japanese release with the same name (same cards, Japanese pictures, estimated prices) or listed empty.
# Korean: (era, years, names) in release order. Shown newest first.
KR_ERAS = [
    ("Original Era", "1999–2004", "Base Set|ADV Expansion Pack|Treecko Constructed Starter Deck|Torchic Constructed Starter Deck|Mudkip Constructed Starter Deck"),
    ("Diamond & Pearl", "2010–2011", "Start of an Adventure|Burning Confrontation|Collapse of Time & Space|Another World|Mystical Seven|Dark Crescent|Hidden Powers|Colorful Legends|Miracle of the Lake|Guardians of Ancient Times|DP-P Black Star Promos"),
    ("Black & White", "2011–2013", "White Collection|Black Collection|Red Collection|Psycho Drive|Hail Blizzard|Dark Rush|Dragon Blast|Dragon Blade|Freeze Bolt|Cold Flare|Plasma Gale|Thunder Knuckle|Spiral Force|Shiny Collection|Megalo Cannon|EX Battle Boost|BW Black Star Promos"),
    ("XY", "2014–2016", "Collection X|Wild Blaze|Rising Fist|Phantom Gate|Tidal Storm|Gaia Volcano|Double Crisis|Emerald Break|Bandit Ring|Red Flash|Blue Shock|Legendary Shine Collection|Rage of the Broken Heavens|Awakening Psychic King|PokéKyun Collection|Premium Champion Pack|Fever-Burst Fighter|Cruel Traitor|Mythical & Legendary Dream Shine Collection|Expansion Pack 20th Anniversary|XY-P Black Star Promos"),
    ("Sun & Moon", "2016–2019", "Collection Sun|Collection Moon|Islands Await You|Alolan Moonlight|Facing a New Trial|To Have Seen the Battle Rainbow|Darkness that Consumes Light|Ultradimensional Beasts|Awakened Heroes|Shining Legends|GX Battle Boost|Ultra Sun|Ultra Moon|Solgaleo-GX & Lunala-GX Legendary Starter Set|Ultra Force|Forbidden Light|Dragon Storm|Champion Road|Sky-Splitting Charisma|Electric Starter Set Raikou-GX|Fairy Rise|Super-Burst Impact|Thunderclap Spark|Flareon-GX, Vaporeon-GX & Jolteon-GX Starter Sets|Dark Order|Tag Bolt|Trainer Battle Decks|GX Ultra Shiny|Night Unison|Full Metal Wall|Great Detective Pikachu|Sun & Moon Family Pokémon Card Game|Double Blaze|Tag Team GX Deck Build Box|GG End|Sky Legend|Tag Team GX Starter Sets|Miracle Twin|Remix Bout|Dream League|Alter Genesis|TAG TEAM GX: Tag All Stars|SM-P Promotional Cards|Random 30-Card Decks"),
    ("Sword & Shield", "2020–2023", "Sword|Shield|VMAX Rising|Rebellion Crash|Sword & Shield Premium Trainer Box|Zacian + Zamazenta Box|Explosive Walker|Infinity Zone|Legendary Heartbeat|Amazing Volt Tackle|VMAX Special Set|Shiny Star V|Single Strike Master|Rapid Strike Master|Peerless Fighters|Silver Lance|Jet-Black Spirit|Silver Lance & Jet-Black Spirit Jumbo Pack Set|Inteleon VMAX High-Class Deck|Gengar VMAX High-Class Deck|Eevee Heroes VMAX Special Set|Eevee Heroes|Skyscraping Perfection|Blue Sky Stream|V-UNION Special Card Sets|25th Anniversary Collection|25th Anniversary Golden Box|Promo Card Pack 25th Anniversary Edition|Sword & Shield Family Pokémon Card Game|Fusion Arts|Zacian & Zamazenta vs Eternatus Special Deck Set|VMAX Climax|Star Birth|Start Deck 100|Start Deck 100 CoroCoro Comic Version|Battle Region|Lucario Starter Set VSTAR|Darkrai Starter Set VSTAR|Time Gazer|Space Juggler|Pokémon GO|Dark Phantasma|Deoxys VSTAR & VMAX High-Class Deck|Zeraora VSTAR & VMAX High-Class Deck|Lost Abyss|VSTAR Special Set|Incandescent Arcana|Paradigm Trigger|Charizard VSTAR vs Rayquaza VMAX Special Deck Set|VSTAR Universe|S-P Promotional Cards"),
    ("Scarlet & Violet", "2023–2025", "Scarlet ex|Violet ex|Premium Trainer Box ex|Fuecoco & Ampharos ex Starter Set|Quaxly & Mimikyu ex Starter Set|Sprigatito & Lucario ex Starter Set|Pikachu ex & Pawmot Starter Set|Triplet Beat|Snow Hazard|Clay Burst|ex Special Set|Pokémon Card 151|Ruler of the Black Flame|Ruler of the Black Flame Deck Build Box|ex Start Decks|Mewtwo ex Terastal Starter Set|Skeledirge ex Terastal Starter Set|Raging Surf|Ancient Roar|Future Flash|Venusaur, Charizard & Blastoise Special Deck Set|Shiny Treasure ex|Ancient Koraidon ex Starter Deck & Build Set|Future Miraidon ex Starter Deck & Build Set|Wild Force|Cyber Judge|Battle Academy|Crimson Haze|Mask of Change|Chien-Pao ex Battle Master Deck|Terastal Charizard ex Battle Master Deck|Night Wanderer|Stellar Miracle|Stellar Miracle Deck Build Box|Ceruledge ex Stellar Tera Type Starter Set|Sylveon ex Stellar Tera Type Starter Set|Paradise Dragona|Super Electric Breaker|Generations Start Decks|Terastal Festival ex|Battle Partners|Battle Partners Deck Build Box|Steven's Beldum & Metagross ex Starter Set|Marnie's Morpeko & Grimmsnarl ex Starter Set|Hot Air Arena|Glory of Team Rocket|White Flare|Black Bolt|SV-P Promotional Cards"),
    ("Mega Evolution", "2025–2026", "Mega Symphonia|Mega Brave|MEGA Premium Trainer Box|Mega Gengar ex Starter Set|Mega Diancie ex Starter Set|Inferno X|MEGA Dream ex|Start Deck 100 Battle Collection|Nihil Zero|Mega Special Card Sets|Ninja Spinner|Abyss Eye|Storm Emeralda|30th Celebration|M-P Promotional Cards"),
]

# Chinese: (heading, names, flags) in display order. Flags: r = list the names newest first, m = Misc. (A-Z).
CN_SECTIONS = [
    ("Scarlet & Violet \u00b7 2025\u20132026", "Ancient Times, Future Progress (CSV1)|Miracle Journey (CSV2)|Fearless Terastal (CSV3)|Bonus Round (CSV4)|Ardent Obsidian (CSV5)|Arcane Truth (CSV6)|Blade Awakening (CSV7)|Sparkling Fable (CSV8)|Stellar Crystal (CSV9)|Terastal Gathering (CSV9.5)|Together in Pursuit of Glory (CSV10)|Black Bolt (CSV10.5)|White Flare (CSV10.6)", "r"),
    ("Sword & Shield \u00b7 2023\u20132024", "Dynamax Clash (CS1a)|Dynamax Clash (CS1b)|Dynamax Tactics (CS1.5)|Vivid Portrayals (CS2a)|Vivid Portrayals (CS2b)|Brilliant Counterattack (CS2.5)|Primordial Arts (CS3a)|Primordial Arts (CS3b)|Scorching Skies (CS3.5)|Polychromatic Gathering (CS4a)|Polychromatic Gathering (CS4b)|Flame Dance of the End (CS4.5)|Gallant Galaxy (CS5a)|Gallant Galaxy (CS5b)|Overshadowed Radiance (CS5.5)|Marine Shadow (CS6a)|Marine Shadow (CS6b)|Victory Lodestar (CS6.5)", "r"),
    ("Sun & Moon \u00b7 2022\u20132023", "Storming Emergence (CSM1a)|Storming Emergence (CSM1b)|Storming Emergence (CSM1c)|Battle Elite (CSM1.5)|Shining Synergy (CSM2a)|Shining Synergy (CSM2b)|Shining Synergy (CSM2c)|Striking Competition (CSM2.5)", "r"),
    ("Gem Pack Series", "Gem Pack Vol. 1 (CBB1)|Gem Pack Vol. 2 (CBB2)|Gem Pack Vol. 3 (CBB3)|Gem Pack Vol. 4 (CBB4)|Gem Pack Vol. 5 (CBB5)|Gem Pack Vol. 6 (CBB6)|Gem Pack Vol. 7 (CBB7)", "r"),
    ("Special Collections", "Collect 151|30th Celebration|30th Celebration Basic Energy Collection|Journey Theme Pack|Travel Theme Pack|Kitakami Theme Pack", ""),
    ("Battle Party", "Battle Party: Shared Dream|Battle Party: Shared Dream \u2013 King Reward Pack|Battle Party: Shining Dream|Battle Party: Shining Dream \u2013 King Reward Pack", ""),
    ("Happy Sets", "Happy Set 1 \u2013 Pikachu, Clefairy, Turtwig & Gimmighoul|Happy Set 1 \u2013 Modification Pack|Happy Set 1 \u2013 Reward Pack|Happy Set 2 \u2013 Lucario, Greninja, Zamazenta & Mabosstiff|Happy Set 2 \u2013 Modification Pack|Happy Set 2 \u2013 Reward Pack|Happy Set 3 \u2013 Altaria, Latios, Infernape & Maushold|Happy Set 3 \u2013 Modification Pack|Happy Set 3 \u2013 Reward Pack|Happy Set 4 \u2013 Decidueye, Melmetal, Koraidon & Miraidon|Happy Set 4 \u2013 Modification Pack|Happy Set 4 \u2013 Reward Pack|Happy Set 4 \u2013 Happy Pack|Happy Set 5 \u2013 Dragonite, Mewtwo, Camerupt & Sinistcha|Happy Set 5 \u2013 Modification Pack|Happy Set 5 \u2013 Reward Pack|Happy Set 5 \u2013 Happy Pack|Happy Set 6 \u2013 Trainer's Pok\u00e9mon|Happy Set 6 \u2013 Modification Pack|Happy Set 6 \u2013 Reward Pack|Happy Set 6 \u2013 Happy Pack", ""),
    ("Decks & Products", "Storming Emergence GX Starter Deck|Shining Synergy GX Starter Deck|Dynamax Clash V Starter Deck|Primordial Arts V Starter Deck|Gallant Galaxy V Starter Deck|Battle Academy|Start Deck 100|Battle Party Decks|Master Strategy Deck Building Set: Charizard ex|Master Strategy Deck Building Set: Gardevoir ex|Master Strategy Deck Building Set: Miraidon ex|Master Strategy Deck Building Set: Raging Bolt ex|Master Strategy Deck Building Set: Dragapult ex|Master Strategy Deck Building Set: Gholdengo ex", ""),
    ("Misc.", "Sun & Moon Promotional Cards|Sword & Shield Promotional Cards|Scarlet & Violet Promotional Cards (SV-P)|30th Anniversary Promotional Cards|Mega Evolution Promotional Cards", "m"),
]
TW_SECTIONS = [
    ("Mega Evolution \u00b7 2025\u20132026", "Mega Symphonia|Mega Brave|Inferno X|Mega Dream ex|Nihil Zero|Ninja Spinner|Abyss Eye|Storm Emeralda|30th Celebration", "r"),
    ("Scarlet & Violet \u00b7 2023\u20132025", "Scarlet ex|Violet ex|Triplet Beat|Snow Hazard|Clay Burst|Pok\u00e9mon Card 151|Ruler of the Black Flame|Raging Surf|Future Flash|Ancient Roar|Shiny Treasure ex|Wild Force|Cyber Judge|Crimson Haze|Mask of Change|Night Wanderer|Stellar Miracle|Paradise Dragona|Super Electric Breaker|Terastal Festival ex|Battle Partners|Hot Air Arena|Glory of Team Rocket|Black Bolt|White Flare", "r"),
    ("Sword & Shield \u00b7 2020\u20132023", "Sword & Shield \u2013 Set A|Sword & Shield \u2013 Set B|Infinite Power \u2013 Set A|Infinite Power \u2013 Set B|Amazing Volt Tackle|Shiny Star V|Single Strike Master|Rapid Strike Master|Peerless Fighters|Silver Lance|Jet-Black Spirit|Eevee Heroes|Skyscraping Perfection|Blue Sky Stream|Fusion Arts|25th Anniversary Collection|VMAX Climax|Star Birth|Battle Region|Time Gazer|Space Juggler|Dark Phantasma|Pok\u00e9mon GO|Lost Abyss|Incandescent Arcana|Paradigm Trigger|VSTAR Universe", "r"),
    ("Sun & Moon \u00b7 2019\u20132020", "All Stars Collection \u2013 Set A|All Stars Collection \u2013 Set B|Dreams Come True Collection \u2013 Set A|Dreams Come True Collection \u2013 Set B|Double Burst \u2013 Set A|Double Burst \u2013 Set B|Legendary Clash \u2013 Set A|Legendary Clash \u2013 Set B", "r"),
    ("Original Era \u00b7 1999\u20132006", "Base Set (1999)|EX Legend Maker (2006)|EX Trainer Kit 2 (2006)|Pikachu World Collection (2000)", "r"),
    ("Anniversary & Special Collections", "25th Anniversary Promo Card Pack|25th Anniversary Collection|Pok\u00e9mon GO Special Collections|VSTAR Special Set|Start Deck 100|Start Deck 100 CoroCoro Comic Version|Generations Start Decks|Trainers Camp|30th Celebration Premium Deck Set: Espeon & Umbreon", ""),
    ("Starter & Challenge Decks", "Sword & Shield V Starter Deck|Infinite Power V Starter Deck|Partners V Starter Deck|Challenge V Starter Deck|Evolution V Starter Deck|Strength V Starter Deck|Charizard V Starter Deck|Mewtwo V Starter Deck|Pikachu V Starter Deck|Lucario VSTAR Starter Set|Darkrai VSTAR Starter Set|Mewtwo ex Terastal Starter Set|Skeledirge ex Terastal Starter Set|Ancient Koraidon ex Starter Deck|Future Miraidon ex Starter Deck|Charizard ex Starter Deck|Pikachu ex Starter Deck", ""),
    ("Tactics Decks", "Gardevoir ex Tactics Deck|Meowscarada ex Tactics Deck|Miraidon ex Tactics Deck|Scizor ex Tactics Deck|Terastal Charizard ex Tactics Deck|Terastal Garchomp ex Tactics Deck|Mega Charizard X ex Tactics Deck|Mega Dragonite ex Tactics Deck|Mega Gardevoir ex Tactics Deck|Mega Sharpedo ex Tactics Deck", ""),
    ("Special Deck Sets", "Zacian & Zamazenta vs Eternatus Special Deck Set|Charizard VSTAR vs Rayquaza VMAX Special Deck Set|Deoxys VSTAR & VMAX High-Class Deck|Zeraora VSTAR & VMAX High-Class Deck", ""),
    ("Misc.", "SM-P Promotional Cards|S-P Promotional Cards|SV-P Promotional Cards|M-P Promotional Cards|Pok\u00e9Park Promotional Cards|Pok\u00e9mon Center Promotional Cards|Pok\u00e9mon Championship Promotional Cards|World Championship Promotional Cards|Anniversary Promotional Cards|Prerelease Promotional Cards|Tournament Prize Cards", "m"),
]
LANG_CFG = {
    "kr": {"api": "ko", "env": "KR_API", "dir": "kr", "label": "Korean"},
    "cn": {"api": "zh-cn", "env": "CN_API", "dir": "cn", "label": "Simplified Chinese"},
    "tw": {"api": "zh-tw", "env": "TW_API", "dir": "tw", "label": "Traditional Chinese"},
}


def kr_clean(n):
    """A Japanese set name without its leading code ('SV5K: Wild Force' -> 'Wild Force')."""
    n = (n or "").strip()
    while True:
        m = re.match(r"^[A-Za-z0-9.+\-]{1,9}:\s*(?=\S)", n)
        if not m:
            return n
        n = n[m.end():].strip()


def lang_sections(key):
    """The sets of one language in display order: [(heading, [entry, ...])]. An entry is
    {"text": as listed, "name": without its code or year, "code": e.g. CSM1a or ''}."""
    seen = set()

    def ents(names, rev=False, misc=False):
        out = []
        for t in names.split("|"):
            t = t.strip()
            if not t or t in seen:
                continue
            seen.add(t)
            m = re.match(r"^(.*?)\s*\(([A-Za-z]{1,4}\d[\w.]*)\)$", t)
            name, code = (m.group(1), m.group(2)) if m else (t, "")
            out.append({"text": t, "name": re.sub(r"\s*\(\d{4}\)$", "", name), "code": code})
        if misc:
            out.sort(key=lambda e: e["text"].lower())
        return out[::-1] if rev else out

    if key == "kr":
        # the file order is oldest first; show the newest era first
        secs = [(f"{era} \u00b7 {years}", ents(names, True)) for era, years, names in KR_ERAS]
        return secs[::-1]
    return [(head, ents(names, "r" in fl, "m" in fl)) for head, names, fl in (CN_SECTIONS if key == "cn" else TW_SECTIONS)]


def build_lang(jp, key):
    """Every set of one language. Card data comes from TCGdex where it has any. Every other listed set is built from
    the Japanese set with the same name (same card list, Japanese pictures) when there is exactly one, and is listed
    empty otherwise. These cards have no published prices, so each card borrows the price of the Japanese card with the
    same set code and number (an estimate), and each such set also lists the Japanese sealed products."""
    import difflib
    cfg = LANG_CFG[key]
    label, base = cfg["label"], "data/" + cfg["dir"]
    api = os.environ.get(cfg["env"]) or "https://api.tcgdex.net/v2/" + cfg["api"]
    try:
        listing = get(f"{api}/sets", tries=3)
    except Exception as e:
        print(label, "set list from TCGdex failed:", repr(e), flush=True)
        listing = []
    if not isinstance(listing, list):
        listing = []
    print(label, "sets listed by TCGdex:", len(listing), flush=True)
    jsets, jrows, jkeys = {}, {}, {}
    if jp:
        for s in jp["sets"]:
            jsets.setdefault(norm(s.get("a")), []).append(s)
            jkeys.setdefault(nz(kr_clean(s.get("n"))), []).append(s)
        for r in jp["idx"]:
            jrows.setdefault(r[1], []).append(r)
    os.makedirs(f"{base}/s", exist_ok=True)
    sets, bad, matched, ncards, priced, miss, shown, used = [], 0, 0, 0, 0, [], False, set()
    td = []   # (set entry, Japanese set it matched, TCGdex id)

    def jp_card_rows(js, g):
        """Rows built from a Japanese set: cards (with their real product ids, so the usual picture address
        works) and the sealed products."""
        rows, nums = [], set()
        for r in jrows.get(js["g"], []):
            if r[3]:
                nums.add(kr_numkey(r[3]))
                rows.append([f"k-{g}-{r[0]}", kr_en(r[2]) or r[2], r[3], r[4] or "", r[5], r[6], r[7], None])
        for r in jrows.get(js["g"], []):
            if not r[3]:
                rows.append([f"k-{g}-s{r[7]}", r[2], "", "Sealed", r[5], "", r[7], None])
        return rows, len(nums)

    for ls in listing:
        sid = ls.get("id")
        if not sid:
            continue
        try:
            d = get(f"{api}/sets/{urllib.parse.quote(str(sid))}", tries=3)
        except Exception as e:
            bad += 1
            print("skipped", label, "set", sid, repr(e), flush=True)
            continue
        time.sleep(0.08)
        if not shown:
            shown = True
            print("sample", label, "set:", json.dumps(d, ensure_ascii=False)[:500], flush=True)
        cards = [c for c in (d.get("cards") or []) if isinstance(c, dict)]
        if not cards:
            continue
        g = slug(str(sid))
        used.add(g)
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
        ent = {"g": g, "n": name, "a": str(sid), "d": kd or (js or {}).get("d") or "", "t": len(cards),
               "l": (logo + ".png") if logo and not re.search(r"\.\w{3,4}$", logo) else logo,
               "est": 1 if js else 0, "src": "td"}
        sets.append(ent)
        td.append((ent, js, str(sid), name))

    # the full list of sets, in display order
    secs = lang_sections(key)
    allents = [e for _, es in secs for e in es]
    by_code = {norm(e["code"]): e for e in allents if e["code"]}
    by_name = {}
    for e in allents:
        by_name.setdefault(nz(e["name"]), []).append(e)
    claimed = {}
    for ent, js, sid, name in td:
        le = by_code.get(norm(sid))
        if not le and js:
            c = by_name.get(nz(kr_clean(js["n"]))) or []
            le = c[0] if len(c) == 1 else None
        if le and id(le) not in claimed:
            claimed[id(le)] = ent
            ent["n"] = f"{le['text']} \u2014 {name}" if nz(le["text"]) != nz(name) else name
        elif js and nz(kr_clean(js["n"])) != nz(name):
            ent["n"] = f"{kr_clean(js['n'])} \u2014 {name}"
    order, fuzzy, empty, derived = 0, [], [], 0
    for head, es in secs:
        for e in es:
            order += 1
            ent = claimed.get(id(e))
            if ent:
                ent["e"], ent["o"] = head, order
                continue
            key_n = nz(e["name"])
            cands = jkeys.get(key_n) or []
            if len(by_name.get(key_n, [])) > 1:
                cands = []                      # several sets share this name (a, b, c parts): can't tell which is which
            elif not cands and jkeys and not e["code"]:
                close = difflib.get_close_matches(key_n, list(jkeys), n=1, cutoff=0.92)
                if close:
                    cands = jkeys[close[0]]
                    fuzzy.append(f"{e['name']} = {kr_clean(cands[0].get('n'))}")
            js = max(cands, key=lambda x: x.get("t") or 0) if cands else None
            g = slug(e["code"]) if e["code"] else (slug(js.get("a") or "") if js and js.get("a") else "")
            if not g or g in used:
                g = slug(e["text"])
            while g in used:
                g += "x"
            used.add(g)
            rows, nc = (jp_card_rows(js, g) if js else ([], 0))
            if js:
                derived += 1
                matched += 1
                ncards += nc
                priced += sum(1 for r in rows if r[4])
            else:
                empty.append(e["text"])
            dump(f"{base}/s/{g}.json", {"c": rows})
            sets.append({"g": g, "n": e["text"], "a": e["code"] or (js or {}).get("a") or "", "d": "", "t": nc, "l": None,
                         "est": 1 if js else 0, "src": "jp" if js else "none", "e": head, "o": order})
    if not any(s["t"] for s in sets):
        raise RuntimeError(f"no {label} cards could be built (skipped {bad})")
    sets.sort(key=lambda s: s["d"], reverse=True)          # sets that are not on the list: newest first, at the end
    sets.sort(key=lambda s: s.get("o", 10 ** 6))
    dump(f"{base}/sets.json", sets)
    ntd = sum(1 for s in sets if s["src"] == "td")
    dump(f"{base}/meta.json", {"sets": len(sets), "from_tcgdex": ntd, "from_japanese": derived, "empty": len(empty),
                               "matched": matched, "cards": ncards, "priced_rows": priced,
                               "updated": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    print(label + ":", len(sets), "sets,", ncards, "cards,", ntd, "sets from TCGdex,", derived,
          "built from the Japanese release,", len(empty), "listed without cards, skipped", bad, flush=True)
    if fuzzy:
        print(label, "sets matched by similar name:", " | ".join(fuzzy[:40]), flush=True)
    if empty:
        print(label, "sets with no card data (listed empty):", " | ".join(empty[:120]), flush=True)
    if miss:
        print("TCGdex", label, "sets without a Japanese match (no price estimate):", ", ".join(miss[:60]), flush=True)


def build_korean(jp):
    build_lang(jp, "kr")


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
    for lk in ("kr", "cn", "tw"):
        try:
            build_lang(cats.get(jp), lk)
        except Exception as e:
            print(LANG_CFG[lk]["label"], "data skipped:", repr(e), flush=True)
    bf = (os.environ.get("BACKFILL_DAYS") or "0").strip() or "0"
    if bf.lower() in ("all", "max") or (bf.isdigit() and int(bf) > 0):
        got, left = backfill(bf, today_dt, cats)
        if got and left and os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as o:
                o.write("more=1\n")
    week = (today_dt - datetime.timedelta(days=7)).isoformat()
    chg = {}
    for cat, st in cats.items():
        base = "data" if cat == 3 else "data/jp"
        for gid, days in st["H"].items():
            dates, pm = save_hist(base, gid, days, today_dt)
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
