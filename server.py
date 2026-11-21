#!/usr/bin/env python3
"""Food log: photo/text -> AI nutrition draft -> you edit -> Save.

Self-hosted, single user, Python standard library only. Data lives in one SQLite file plus a photos folder
under FOOD_DATA_DIR; monthly CSVs are one-way exports rewritten after every change. The model only proposes
food items; every total is summed in code and nothing is saved until you press Save.

Configuration is environment variables (see README.md) plus an optional profile.json in the data folder.
"""
import base64, csv, glob, hashlib, hmac, io, json, os, re, secrets, sqlite3, sys, threading, time, urllib.error, urllib.request
import datetime as dt
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path):
    """Minimal .env support (KEY=value lines); real environment variables win."""
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return
    for line in lines:
        k, sep, v = line.strip().partition("=")
        if sep and k and not k.startswith("#"):
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(os.environ.get("FOOD_ENV_FILE", os.path.join(ROOT, ".env")))
DATA = os.path.abspath(os.environ.get("FOOD_DATA_DIR", os.path.join(ROOT, "data")))
DB = os.environ.get("FOOD_DB", os.path.join(DATA, "food.db"))
PHOTOS = os.path.join(DATA, "photos")
EXPORTS = os.path.join(DATA, "csv")
STATIC = os.path.join(ROOT, "static")
HOST = os.environ.get("FOOD_HOST", "127.0.0.1")
PORT = int(os.environ.get("FOOD_PORT", "8800"))
ORIGIN = os.environ.get("FOOD_ORIGIN", "").rstrip("/")   # e.g. https://food.example.com; enables strict Origin check

# auth: a password (session cookie) and/or an identity header set by a trusted reverse proxy
PASSWORD = os.environ.get("FOOD_PASSWORD", "")
TRUST_HEADER = os.environ.get("FOOD_TRUST_HEADER", "")    # e.g. Tailscale-User-Login (only behind that proxy!)
ALLOWED = os.environ.get("FOOD_ALLOWED_USER", "")
SESSION_DAYS = 90

# AI: any OpenAI-compatible /v1 endpoint (OpenAI, OpenRouter, Ollama, LiteLLM, a local proxy ...)
AI_BASE = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
AI_KEY = os.environ.get("AI_API_KEY", "")
DEFAULT_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")
AI_MODELS = [m.strip() for m in os.environ.get("AI_MODELS", "").split(",") if m.strip()]
MAX_BODY = 12 * 1024 * 1024

NUM = ["kcal", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "cholesterol_mg", "fiber_g",
       "soluble_fiber_g", "sugar_g", "sodium_mg"]
CSV_FIELDS = ["date", "time", "meal", "foods", *NUM, "estimated", "note"]
MEALS = ("breakfast", "lunch", "dinner", "snack", "drink", "supplement")


def _load_profile():
    """profile.json (optional): who the estimates are for, local cuisine, daily targets. See profile.example.json."""
    path = os.environ.get("FOOD_PROFILE", os.path.join(DATA, "profile.json"))
    try:
        p = json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        p = {}
    except ValueError as e:
        sys.exit(f"food-log: {path} is not valid JSON: {e}")
    t = {"kcal": 2200, "protein_g": 100, "carbs_g": 275, "fat_g": 70, "sat_fat_g": 20,
         "cholesterol_mg": 300, "fiber_g": 30, "soluble_fiber_g": 8, "sodium_mg": 2000}
    t.update({k: float(v) for k, v in (p.get("targets") or {}).items() if k in NUM and v})
    return {"about": str(p.get("about") or "").strip(), "cuisine": str(p.get("cuisine") or "").strip(),
            "notes": str(p.get("notes") or "").strip(), "targets": t}


def _tz():
    name = os.environ.get("FOOD_TZ", "")
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            sys.exit(f"food-log: unknown FOOD_TZ {name!r} (use an IANA name like Europe/Berlin)")
    return dt.datetime.now().astimezone().tzinfo


PROFILE = _load_profile()
TARGETS = PROFILE["targets"]
TZ = _tz()
TZ_NAME = os.environ.get("FOOD_TZ", "") or "local time"
SCHEMA = """CREATE TABLE IF NOT EXISTS meals (id INTEGER PRIMARY KEY AUTOINCREMENT,
 log_date TEXT NOT NULL, meal TEXT NOT NULL, foods TEXT NOT NULL, kcal REAL, protein_g REAL, carbs_g REAL,
 fat_g REAL, sat_fat_g REAL, cholesterol_mg REAL, fiber_g REAL, soluble_fiber_g REAL, sugar_g REAL,
 sodium_mg REAL, estimated INTEGER NOT NULL DEFAULT 1, note TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
 UNIQUE(log_date, meal, foods))"""

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/manifest.json": ("manifest.json", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/icon-180.png": ("icon-180.png", "image/png"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/favicon.png": ("favicon.png", "image/png"),
    "/login.js": ("login.js", "text/javascript; charset=utf-8"),
}
for _f in glob.glob(os.path.join(STATIC, "*.woff2")):
    STATIC_FILES["/" + os.path.basename(_f)] = (os.path.basename(_f), "font/woff2")
PUBLIC = {"/manifest.json", "/icon.svg", "/icon-180.png", "/icon-192.png", "/icon-512.png", "/favicon.png", "/app.css",
          "/login.js", *(p for p in STATIC_FILES if p.endswith(".woff2"))}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; font-src 'self'; "
       "connect-src 'self'; manifest-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
WRITE_LOCK = threading.Lock()


class Bad(ValueError):
    pass


# ---------- store ----------
def db():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    return c


ITEMS_SCHEMA = f"""CREATE TABLE IF NOT EXISTS meal_items (id INTEGER PRIMARY KEY AUTOINCREMENT,
 meal_id INTEGER NOT NULL, pos INTEGER NOT NULL DEFAULT 0, name TEXT NOT NULL, portion TEXT, grams REAL,
 {', '.join(k + ' REAL' for k in NUM)}, confidence TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')))"""
ITEM_COLS = ["name", "portion", "grams", *NUM, "confidence"]
SAVED_SCHEMA = f"""CREATE TABLE IF NOT EXISTS saved_foods (id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL UNIQUE COLLATE NOCASE, portion TEXT, grams REAL, {', '.join(k + ' REAL' for k in NUM)},
 uses INTEGER NOT NULL DEFAULT 0, last_used TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')))"""
SAVED_COLS = ["name", "portion", "grams", *NUM]


def init_db():
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    with db() as c:
        c.execute(SCHEMA)
        # per-food breakdown of a meal row (meal_id -> meals.id); the meal row keeps the totals
        c.execute(ITEMS_SCHEMA)
        c.execute("CREATE INDEX IF NOT EXISTS meal_items_meal ON meal_items(meal_id)")
        # reusable foods ("My foods"): one serving each
        c.execute(SAVED_SCHEMA)


def _row_out(r):
    d = dict(r)
    d["date"] = d.pop("log_date")
    for k in NUM:
        if d.get(k) is not None:
            d[k] = round(d[k], 1)
    return d


def _clock(r):
    m = r["meal"]
    if m in ("breakfast", "lunch", "dinner"):
        return {"breakfast": "morning", "lunch": "afternoon", "dinner": "evening"}[m]
    try:
        h = (dt.datetime.fromisoformat(r["created_at"]).replace(tzinfo=dt.timezone.utc).astimezone(TZ)).hour
    except (TypeError, ValueError):
        return ""
    return "morning" if h < 12 else "afternoon" if h < 17 else "evening" if h < 21 else "night"


def _fmt(v):
    if v is None:
        return ""
    v = round(float(v), 1)
    return str(int(v)) if v == int(v) else str(v)


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{secrets.token_hex(4)}"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def export_month(month):
    """Rewrite the CSV export for one month from the DB (only when content changed)."""
    with db() as c:
        rows = c.execute("select * from meals where substr(log_date,1,7)=? order by log_date, id",
                         (month,)).fetchall()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({"date": r["log_date"], "time": _clock(r), "meal": r["meal"], "foods": r["foods"],
                    **{k: _fmt(r[k]) for k in NUM}, "estimated": r["estimated"], "note": r["note"] or ""})
    text = buf.getvalue()
    for path in (f"{EXPORTS}/{month}.csv",):
        if not rows and not os.path.exists(path):
            continue
        try:
            if open(path, encoding="utf-8").read() == text:
                continue
        except OSError:
            pass
        try:
            _atomic_write(path, text)
        except OSError:
            pass   # an export being unavailable must never block the log


MAX_PHOTOS = 4


def photos_of(v):
    """index.json values: one name (older rows) or a list of names."""
    return [v] if isinstance(v, str) else [x for x in (v or []) if isinstance(x, str)]


def images_of(b):
    imgs = b.get("images") or ([b["image"]] if b.get("image") else [])
    if not isinstance(imgs, list) or len(imgs) > MAX_PHOTOS:
        raise Bad(f"up to {MAX_PHOTOS} photos per meal")
    for im in imgs:
        if not isinstance(im, str):
            raise Bad("bad photo")
        _check_jpeg(im)
    return imgs


def photo_index():
    try:
        return json.load(open(f"{PHOTOS}/index.json", encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_index(idx):
    os.makedirs(PHOTOS, exist_ok=True)
    _atomic_write(f"{PHOTOS}/index.json", json.dumps(idx, ensure_ascii=False, indent=1))


def clean_meal(b):
    date = str(b.get("date") or "")
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        raise Bad("bad date")
    meal = str(b.get("meal") or "").lower()
    if meal not in MEALS:
        raise Bad("bad meal")
    foods = re.sub(r"\s+", " ", str(b.get("foods") or "")).strip()[:600]
    if not foods:
        raise Bad("describe the food")
    out = {"log_date": date, "meal": meal, "foods": foods, "estimated": 1 if b.get("estimated", True) else 0,
           "note": re.sub(r"\s+", " ", str(b.get("note") or "")).strip()[:600] or None}
    for k in NUM:
        v = b.get(k)
        if v in (None, ""):
            out[k] = None
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise Bad(f"bad {k}")
        if not 0 <= f <= 20000:
            raise Bad(f"bad {k}")
        out[k] = round(f, 1)
    return out


def clean_items(items):
    if not isinstance(items, list):
        raise Bad("bad items")
    out = []
    for it in items[:30]:
        if not isinstance(it, dict):
            continue
        name = re.sub(r"\s+", " ", str(it.get("name") or "")).strip()[:120]
        if not name:
            continue
        d = {"name": name, "portion": re.sub(r"\s+", " ", str(it.get("portion") or "")).strip()[:80] or None,
             "confidence": it.get("confidence") if it.get("confidence") in ("high", "medium", "low") else None}
        for k in ("grams", *NUM):
            try:
                f = float(it.get(k) or 0)
            except (TypeError, ValueError):
                raise Bad(f"bad {k} for {name}")
            if not 0 <= f <= 20000:
                raise Bad(f"bad {k} for {name}")
            d[k] = round(f, 1)
        out.append(d)
    if not out:
        raise Bad("add at least one food")
    return out


def _sum_items(items):
    return {k: round(sum(i[k] for i in items), 1) for k in NUM}


def _ensure_items(c):
    # cheap; survives the DB file being replaced/recreated while we run
    c.execute(ITEMS_SCHEMA)
    c.execute("CREATE INDEX IF NOT EXISTS meal_items_meal ON meal_items(meal_id)")


def _write_items(c, rid, items):
    _ensure_items(c)
    c.execute("delete from meal_items where meal_id=?", (rid,))
    for pos, it in enumerate(items):
        c.execute(f"insert into meal_items (meal_id, pos, {','.join(ITEM_COLS)}) values (?,?,{','.join('?' * len(ITEM_COLS))})",
                  [rid, pos] + [it[k] for k in ITEM_COLS])


def items_for(c, ids):
    by = {}
    ids = list(ids)
    if not c.execute("select 1 from sqlite_master where name='meal_items'").fetchone():
        return by
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in c.execute(f"select * from meal_items where meal_id in ({','.join('?' * len(chunk))}) order by meal_id, pos, id", chunk):
            by.setdefault(r["meal_id"], []).append({k: r[k] for k in ITEM_COLS})
    return by


def saved_foods(_qs=None):
    with db() as c:
        c.execute(SAVED_SCHEMA)
        rows = c.execute("select * from saved_foods order by uses desc, coalesce(last_used, created_at) desc, name").fetchall()
    return {"foods": [{k: r[k] for k in ("id", *SAVED_COLS, "uses", "last_used")} for r in rows]}


def save_food(b):
    it = clean_items([b])[0]   # same validation as a meal's food
    with WRITE_LOCK:
        with db() as c:
            c.execute(SAVED_SCHEMA)
            c.execute(f"""insert into saved_foods ({','.join(SAVED_COLS)}) values ({','.join('?' * len(SAVED_COLS))})
                          on conflict(name) do update set {','.join(f'{k}=excluded.{k}' for k in SAVED_COLS[1:])}""",
                      [it[k] for k in SAVED_COLS])
            rid = c.execute("select id from saved_foods where name=?", (it["name"],)).fetchone()[0]
    return {"id": rid, **saved_foods()}


def delete_food(b):
    with WRITE_LOCK:
        with db() as c:
            c.execute(SAVED_SCHEMA)
            c.execute("delete from saved_foods where id=?", (int(b.get("id") or 0),))
    return saved_foods()


def _bump_saved(c, ids):
    ids = [int(i) for i in (ids or [])[:30] if str(i).isdigit()]
    if ids:
        c.execute(SAVED_SCHEMA)
        c.executemany("update saved_foods set uses=uses+1, last_used=datetime('now') where id=?", [(i,) for i in ids])


def _store_photo(date, image_b64):
    try:
        raw = base64.b64decode(image_b64, validate=True)
    except ValueError:
        raise Bad("bad photo")
    if raw[:3] != b"\xff\xd8\xff":
        raise Bad("photo must be JPEG")
    sub = date[:7]
    os.makedirs(f"{PHOTOS}/{sub}", exist_ok=True)
    name = f"{sub}/{date}_{secrets.token_hex(5)}.jpg"
    with open(f"{PHOTOS}/{name}", "wb") as f:
        f.write(raw)
    os.chmod(f"{PHOTOS}/{name}", 0o600)
    return name


COLS = ["log_date", "meal", "foods", *NUM, "estimated", "note"]


def add_meal(b):
    if not (b.get("images") or b.get("image")) and b.get("job"):
        # card saved after the app was closed: the phone no longer has the photos, the finished job does
        with JOBS_LOCK:
            j = JOBS.get(str(b["job"]))
        if j and j["kind"] == "chat":
            b = {**b, "images": j["req"].get("images") or []}
    images_of(b)   # validate photos before writing anything
    items = clean_items(b["items"]) if b.get("items") else None
    m = clean_meal({**b, **_sum_items(items)} if items else b)
    with WRITE_LOCK:
        try:
            with db() as c:
                cur = c.execute(f"insert into meals ({','.join(COLS)}) values ({','.join('?' * len(COLS))})",
                                [m[k] for k in COLS])
                rid = cur.lastrowid
                if items:
                    _write_items(c, rid, items)
                _bump_saved(c, b.get("saved_ids"))
        except sqlite3.IntegrityError:
            raise Bad("this exact meal is already logged for that day")
        imgs = images_of(b)
        if imgs:
            idx = photo_index()
            names = [_store_photo(m["log_date"], im) for im in imgs]
            idx[str(rid)] = names[0] if len(names) == 1 else names
            _save_index(idx)
    export_month(m["log_date"][:7])
    return {"id": rid}


def update_meal(b):
    rid = int(b.get("id") or 0)
    items = clean_items(b["items"]) if "items" in b else None   # present = replace the breakdown
    m = clean_meal({**b, **_sum_items(items)} if items else b)
    with WRITE_LOCK:
        with db() as c:
            old = c.execute("select log_date from meals where id=?", (rid,)).fetchone()
            if not old:
                raise Bad("meal no longer exists; reload")
            try:
                c.execute(f"update meals set {','.join(k + '=?' for k in COLS)} where id=?",
                          [m[k] for k in COLS] + [rid])
            except sqlite3.IntegrityError:
                raise Bad("this exact meal is already logged for that day")
            if items:
                _write_items(c, rid, items)
    for mo in {old["log_date"][:7], m["log_date"][:7]}:
        export_month(mo)
    return {"id": rid}


def delete_meal(b):
    rid = int(b.get("id") or 0)
    with WRITE_LOCK:
        with db() as c:
            old = c.execute("select log_date from meals where id=?", (rid,)).fetchone()
            if not old:
                raise Bad("meal no longer exists; reload")
            c.execute("delete from meals where id=?", (rid,))
            _ensure_items(c)
            c.execute("delete from meal_items where meal_id=?", (rid,))
        idx = photo_index()
        if idx.pop(str(rid), None):   # jpg stays on disk; only the link goes
            _save_index(idx)
    export_month(old["log_date"][:7])
    return {"ok": True}


def list_meals(qs):
    m = (qs.get("month") or [""])[0]
    if not re.fullmatch(r"\d{4}-\d{2}", m):
        m = dt.datetime.now(TZ).strftime("%Y-%m")
    with db() as c:
        months = [r[0] for r in c.execute("select distinct substr(log_date,1,7) from meals order by 1")]
        rows = c.execute("select * from meals where substr(log_date,1,7)=? order by log_date, id",
                         (m,)).fetchall()
    if m not in months:
        months.append(m)
        months.sort()
    with db() as c:
        items = items_for(c, [r["id"] for r in rows])
    idx = photo_index()
    out = []
    for r in rows:
        d = _row_out(r)
        d["photos"] = photos_of(idx.get(str(d["id"])))
        d["photo"] = d["photos"][0] if d["photos"] else None
        d["items"] = items.get(d["id"], [])
        # a chat edit or another tool writing to the DB may change the totals later; then the breakdown no longer adds up
        d["items_stale"] = bool(d["items"]) and abs(sum(i["kcal"] or 0 for i in d["items"]) - (d["kcal"] or 0)) > 2
        out.append(d)
    export_month(m)   # picks up rows written to the DB by other tools since the last change
    return {"months": months, "month": m, "rows": out, "targets": TARGETS,
            "today": dt.datetime.now(TZ).date().isoformat()}


# ---------- AI ----------
NON_CHAT = re.compile(r"image|embed|tts|whisper|audio|speech|transcri|moderation|dall-e|veo|imagen|realtime|live", re.I)


def models():
    if AI_MODELS:
        ids = AI_MODELS
    else:
        req = urllib.request.Request(AI_BASE + "/models", headers={"Authorization": f"Bearer {AI_KEY}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                ids = sorted(m["id"] for m in json.load(r).get("data", []) if not NON_CHAT.search(m["id"]))
        except Exception:
            ids = []
    allm = [DEFAULT_MODEL] + [i for i in ids if i != DEFAULT_MODEL]
    return {"default": DEFAULT_MODEL, "models": allm}


def _system_estimate():
    p, t = PROFILE, TARGETS
    who = f"for this person: {p['about']}" if p["about"] else "for an adult"
    cuisine = (f"Know {p['cuisine']} food well (local dishes, drinks, street food, chain restaurants and packaged "
               f"products sold there). Use typical local serving sizes. ") if p["cuisine"] else "Use typical serving sizes. "
    return f"""You estimate nutrition for one meal {who}
Daily targets: ~{t['kcal']:g} kcal, protein ~{t['protein_g']:g} g, carbs ~{t['carbs_g']:g} g, fat ~{t['fat_g']:g} g,
sat fat <= {t['sat_fat_g']:g} g, cholesterol <= {t['cholesterol_mg']:g} mg, fibre ~{t['fiber_g']:g} g, sodium <= {t['sodium_mg']:g} mg.
{p['notes']}

Identify every food and drink in the photo and/or text. {cuisine}Estimate portions from visual cues
(plate ~25 cm, utensils, hands, packaging). If the user's text gives quantities or corrections, they override what
you see. Nutrition labels in the photo override estimates.

Reply with ONE JSON object, no prose, no code fence:
{{"meal": "breakfast|lunch|dinner|snack|drink|supplement",
 "items": [{{"name": "short English name", "portion": "e.g. 1 plate / 2 eggs / 250 ml", "grams": number,
            "kcal": n, "protein_g": n, "carbs_g": n, "fat_g": n, "sat_fat_g": n, "cholesterol_mg": n,
            "fiber_g": n, "soluble_fiber_g": n, "sugar_g": n, "sodium_mg": n, "confidence": "high|medium|low"}}],
 "assumptions": "one short sentence with the key guesses (portion, oil, sauce)",
 "tip": "one short, concrete, non-preachy note tied to today's totals and targets, or empty string",
 "not_food": false}}
If there is no food, return {{"not_food": true, "items": []}}. Numbers only, no units inside numbers."""


def _parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < s:
        raise Bad("the model did not return an estimate; try again or pick another model")
    return json.loads(text[s:e + 1])


def _num(v):
    try:
        f = float(v)
        return f if f >= 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def post_chat(body):
    model = body.get("model")
    req = urllib.request.Request(AI_BASE + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {AI_KEY}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=150) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise Bad(f"{model}: HTTP {e.code} {e.read()[:160].decode(errors='replace')}")
    except OSError as e:
        raise Bad(f"{model}: {e}")


def _check_jpeg(image):
    if len(image) * 3 // 4 > 8 * 1024 * 1024:
        raise Bad("photo too large")
    try:
        if base64.b64decode(image[:16], validate=True)[:3] != b"\xff\xd8\xff":
            raise Bad("photo must be JPEG")
    except ValueError:
        raise Bad("bad photo")


def call_model(model, messages):
    resp = post_chat({"model": model, "max_tokens": 2500, "temperature": 0.2, "messages": messages})
    msg = resp["choices"][0]["message"].get("content") or ""
    if isinstance(msg, list):
        msg = "".join(p.get("text", "") for p in msg if isinstance(p, dict))
    return msg


def analyze(b, caller=call_model):
    model = str(b.get("model") or DEFAULT_MODEL)[:80]
    text = str(b.get("text") or "").strip()[:1500]
    images = images_of(b)
    if not text and not images:
        raise Bad("add a photo or describe the meal")
    now = dt.datetime.now(TZ)
    ctx = f"Now: {now:%Y-%m-%d %H:%M} ({TZ_NAME}). Meal date: {b.get('date') or now.date()}."
    so_far = b.get("today") or {}
    if isinstance(so_far, dict) and so_far:
        ctx += " Already eaten that day: " + ", ".join(f"{k} {_num(so_far[k]):g}" for k in TARGETS if k in so_far) + "."
    prev = b.get("previous")
    if prev:
        ctx += (" Your previous estimate for this same meal was: " + json.dumps(prev, ensure_ascii=False)[:3000]
                + " Revise it with the user's correction; keep items they did not mention.")
    if len(images) > 1:
        ctx += (f" {len(images)} photos of the SAME meal: they may show the same dish from other angles, a close-up, a"
                " nutrition label or receipt for something in it, or separate dishes. Count each food once.")
    content = [{"type": "text", "text": f"{ctx}\nUser note: {text or '(none)'}"}]
    for im in images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{im}"}})
    data = _parse_json(caller(model, [{"role": "system", "content": _system_estimate()}, {"role": "user", "content": content}]))
    items = []
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        clean = {"name": str(it.get("name") or "item")[:80], "portion": str(it.get("portion") or "")[:60],
                 "grams": round(_num(it.get("grams"))),
                 "confidence": it.get("confidence") if it.get("confidence") in ("high", "medium", "low") else "medium"}
        for k in NUM:
            clean[k] = round(_num(it.get(k)), 1)
        items.append(clean)
    return {"model": model, "meal": data.get("meal") if data.get("meal") in MEALS else None,
            "items": items, "not_food": bool(data.get("not_food")),
            "assumptions": str(data.get("assumptions") or "")[:400], "tip": str(data.get("tip") or "")[:300]}


def split_meal(b, analyzer=None):
    """AI breakdown of an existing meal into foods, scaled so it adds up to the logged totals. Draft only."""
    with db() as c:
        r = c.execute("select * from meals where id=?", (int(b.get("id") or 0),)).fetchone()
    if not r:
        raise Bad("meal no longer exists; reload")
    logged = {k: r[k] for k in NUM if r[k] is not None}
    text = (f"Already-logged {r['meal']}: {r['foods']}\nLogged totals for the whole meal: "
            + ", ".join(f"{k} {v:g}" for k, v in logged.items())
            + "\nList each food separately (keep the user's foods and portions) and estimate each one.")
    res = (analyzer or analyze)({"model": b.get("model"), "text": text, "date": r["log_date"]})
    items = res.get("items") or []
    if not items:
        raise Bad("couldn't split this meal")
    for k, total in logged.items():   # keep the logged totals; the model only decides the shares
        got = sum(i.get(k) or 0 for i in items)
        if got > 0:
            for i in items:
                i[k] = round((i.get(k) or 0) * total / got, 1)
    return {"items": items, "model": res.get("model")}


def _chat(b):
    import assistant   # lazy: assistant imports this module
    return assistant.chat(b)


# ---------- background AI jobs ----------
# iOS suspends a backgrounded page and drops its open request, so slow AI calls run here in a thread;
# the phone starts a job, polls it, and can come back to the result (or retry it) after leaving the app.
JOBS, JOBS_LOCK, JOB_TTL = {}, threading.Lock(), 6 * 3600
JOB_KINDS = {"chat": lambda b: _chat(b), "split": lambda b: split_meal(b)}


def _run_job(jid):
    with JOBS_LOCK:
        j = JOBS[jid]
        kind, req = j["kind"], j["req"]
    try:
        out, err = JOB_KINDS[kind](req), None
    except Exception as e:   # never leave a job "running" forever
        # by name: run as __main__, assistant's `server.Bad` is a different class object than ours
        out, err = None, str(e) if type(e).__name__ == "Bad" else f"{type(e).__name__}: {e}"
        err = re.sub(r'HTTP (\d+) \{.*?"message":\s*"([^"]+)".*', r"HTTP \1 — \2", err, flags=re.S)[:300]
    with JOBS_LOCK:
        if jid in JOBS:
            JOBS[jid].update(status="error" if err else "done", result=out, error=err, finished=time.time())


def _launch(jid):
    threading.Thread(target=_run_job, args=(jid,), daemon=True, name=f"job-{jid}").start()


def start_job(b):
    kind = b.get("kind")
    if kind not in JOB_KINDS:
        raise Bad("bad job kind")
    req = {k: v for k, v in b.items() if k != "kind"}
    if kind == "chat":
        images_of(req)   # reject bad photos now, not in the background
    now = time.time()
    with JOBS_LOCK:
        for k in [k for k, j in JOBS.items() if now - j["created"] > JOB_TTL]:
            del JOBS[k]
        if sum(1 for j in JOBS.values() if j["status"] == "running") >= 6:
            raise Bad("too many AI requests running; wait a moment")
        jid = secrets.token_hex(8)
        JOBS[jid] = {"kind": kind, "req": req, "status": "running", "created": now, "result": None, "error": None}
    _launch(jid)
    return {"job": jid, "status": "running"}


def job_status(jid):
    with JOBS_LOCK:
        j = JOBS.get(jid)
        if not j:
            return None
        return {"job": jid, "status": j["status"], "result": j["result"], "error": j["error"],
                "age": round(time.time() - j["created"])}


def retry_job(b):
    jid = str(b.get("id") or "")
    with JOBS_LOCK:
        j = JOBS.get(jid)
        if not j:
            raise Bad("job expired")   # client falls back to resending
        if j["status"] == "running":
            return {"job": jid, "status": "running"}
        j.update(status="running", result=None, error=None, created=time.time())
    _launch(jid)
    return {"job": jid, "status": "running"}


# ---------- auth ----------
_SECRET = []


def _secret():
    """Per-install signing key, created once in the data folder. Mixed with the password, so changing
    FOOD_PASSWORD signs everyone out."""
    if _SECRET:
        return _SECRET[0]
    path = os.path.join(DATA, ".session-key")
    try:
        with open(path, "rb") as f:
            key = f.read()
    except FileNotFoundError:
        os.makedirs(DATA, exist_ok=True)
        key = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
    _SECRET.append(hashlib.sha256(key + PASSWORD.encode()).digest())
    return _SECRET[0]


def _sign(exp):
    return hmac.new(_secret(), str(exp).encode(), hashlib.sha256).hexdigest()


def make_session():
    exp = int(time.time()) + SESSION_DAYS * 86400
    return f"{exp}.{_sign(exp)}"


def session_ok(value):
    exp, _, sig = (value or "").partition(".")
    return exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(sig, _sign(int(exp)))


FAILS, FAILS_LOCK = [], threading.Lock()


def check_password(pw):
    """Constant-time compare; at most 10 wrong tries per 10 minutes for the whole server."""
    now = time.time()
    with FAILS_LOCK:
        FAILS[:] = [t for t in FAILS if now - t < 600]
        if len(FAILS) >= 10:
            raise Bad("too many attempts; wait 10 minutes")
    if PASSWORD and hmac.compare_digest(str(pw).encode(), PASSWORD.encode()):
        return True
    with FAILS_LOCK:
        FAILS.append(now)
    time.sleep(0.5)
    return False


# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    server_version = "food-log"

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache="no-store", cookie=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if TRUST_HEADER and ALLOWED and self.headers.get(TRUST_HEADER, "") == ALLOWED:
            return True
        if PASSWORD:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "food_session" and session_ok(v):
                    return True
        return False

    def _cookie(self, value, max_age):
        secure = "; Secure" if ORIGIN.startswith("https://") or self.headers.get("X-Forwarded-Proto") == "https" else ""
        return f"food_session={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict{secure}"

    def _login(self, b):
        if not PASSWORD:
            return self._send(400, {"error": "password sign-in is not enabled"})
        if not check_password(b.get("password") or ""):
            return self._send(401, {"error": "wrong password"})
        return self._send(200, {"ok": True}, cookie=self._cookie(make_session(), SESSION_DAYS * 86400))

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path not in PUBLIC and not self._authed():
            if path == "/" and PASSWORD:   # signed out: the sign-in page instead of the app
                with open(os.path.join(STATIC, "login.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            return self._send(401, {"error": "unauthorized"})
        if path in STATIC_FILES:
            name, ctype = STATIC_FILES[path]
            try:
                with open(os.path.join(STATIC, name), "rb") as f:
                    return self._send(200, f.read(), ctype, "no-cache")
            except OSError:
                return self._send(404, {"error": "missing"})
        if path.startswith("/photo/"):
            name = path[len("/photo/"):]
            if not re.fullmatch(r"\d{4}-\d{2}/\d{4}-\d{2}-\d{2}_[0-9a-f]{10}\.jpg", name):
                return self._send(404, {"error": "not found"})
            try:
                with open(f"{PHOTOS}/{name}", "rb") as f:
                    return self._send(200, f.read(), "image/jpeg", "private, max-age=604800, immutable")
            except OSError:
                return self._send(404, {"error": "not found"})
        try:
            if path == "/api/meals":
                return self._send(200, list_meals(parse_qs(u.query)))
            if path == "/api/models":
                return self._send(200, models())
            if path == "/api/foods":
                return self._send(200, saved_foods())
            if path == "/api/jobs":
                st = job_status((parse_qs(u.query).get("id") or [""])[0])
                return self._send(200, st) if st else self._send(404, {"error": "job expired"})
        except (Bad, sqlite3.Error) as e:
            return self._send(400 if isinstance(e, Bad) else 503, {"error": str(e)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        # writes need a custom header (forces a CORS preflight we never grant) and, when sent, our exact Origin
        if self.headers.get("X-Food") != "1":
            return self._send(403, {"error": "forbidden"})
        origin = self.headers.get("Origin")
        if origin and ORIGIN and origin != ORIGIN:
            return self._send(403, {"error": "bad origin"})
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > MAX_BODY:
            return self._send(413, {"error": "body too large"})
        if path == "/api/logout":
            return self._send(200, {"ok": True}, cookie=self._cookie("", 0))
        if path == "/api/login":
            try:
                b = json.loads(self.rfile.read(n) or b"{}")
                return self._login(b if isinstance(b, dict) else {})
            except (Bad, ValueError) as e:
                return self._send(429 if isinstance(e, Bad) else 400, {"error": str(e)})
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        if n == 0:
            return self._send(400, {"error": "empty body"})
        routes = {"/api/analyze": analyze, "/api/meals/add": add_meal,
                  "/api/meals/update": update_meal, "/api/meals/delete": delete_meal, "/api/chat": _chat,
                  "/api/meals/split": split_meal, "/api/foods/save": save_food, "/api/foods/delete": delete_food,
                  "/api/jobs/start": start_job, "/api/jobs/retry": retry_job}
        fn = routes.get(path)
        if not fn:
            return self._send(404, {"error": "not found"})
        try:
            b = json.loads(self.rfile.read(n))
            if not isinstance(b, dict):
                raise Bad("bad body")
            return self._send(200, fn(b))
        except (Bad, ValueError) as e:
            return self._send(400, {"error": str(e)})
        except sqlite3.Error as e:
            return self._send(503, {"error": f"database busy: {e}"})


def main():
    if not PASSWORD and not (TRUST_HEADER and ALLOWED):
        sys.exit("food-log: set FOOD_PASSWORD (or FOOD_TRUST_HEADER + FOOD_ALLOWED_USER behind a trusted proxy). "
                 "See README.md.")
    if not AI_KEY:
        print("food-log: AI_API_KEY is not set; logging works, AI estimates will fail.", file=sys.stderr)
    init_db()
    print(f"food-log: http://{HOST}:{PORT}  data: {DATA}", file=sys.stderr)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()


if __name__ == "__main__":
    main()
