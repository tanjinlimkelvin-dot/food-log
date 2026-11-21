"""Ask: chat about the food log. The model can read meals and compute totals with tools, and can PROPOSE
adds/edits/deletes. Proposals go back to the phone as cards; nothing is written until the user taps Apply
(which calls the normal /api/meals/* endpoints). All totals come from code, not from the model."""
import datetime as dt
import json
import secrets

import server as S

MAX_STEPS = 6
DAY = {"type": "string", "description": "YYYY-MM-DD"}
NUMS = {k: {"type": "number"} for k in S.NUM}


def _fn(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": list(required)}}}


TOOLS = [
    _fn("list_meals", "Logged meals (with ids and all nutrients) between two dates, inclusive. Use before proposing edits/deletes.",
        {"date_from": DAY, "date_to": DAY, "query": {"type": "string", "description": "optional text to match in foods/note"}},
        ["date_from", "date_to"]),
    _fn("daily_totals", "Per-day nutrient totals between two dates (only days with meals logged), plus averages and how many "
        "days went over each limit.", {"date_from": DAY, "date_to": DAY}, ["date_from", "date_to"]),
    _fn("propose_add_meal", "Propose a new meal for the user to confirm. Give per-meal totals (sum of the foods).",
        {"date": DAY, "meal": {"type": "string", "enum": list(S.MEALS)}, "foods": {"type": "string",
         "description": "foods with portions, separated by '; '"}, **NUMS, "note": {"type": "string"},
         "items": {"type": "array", "description": "one entry per food with its own nutrients; meal totals = their sum",
                   "items": {"type": "object", "properties": {"name": {"type": "string"}, "portion": {"type": "string"},
                                                              "grams": {"type": "number"}, **NUMS}, "required": ["name", "kcal"]}},
         "reason": {"type": "string"}}, ["date", "meal", "foods", "kcal", "items"]),
    _fn("propose_edit_meal", "Propose changing one logged meal. Only pass fields that change. Needs the id from list_meals.",
        {"id": {"type": "integer"}, "date": DAY, "meal": {"type": "string", "enum": list(S.MEALS)},
         "foods": {"type": "string"}, **NUMS, "note": {"type": "string"}, "reason": {"type": "string"}}, ["id"]),
    _fn("propose_delete_meals", "Propose deleting meals (duplicates, mistakes). Needs ids from list_meals.",
        {"ids": {"type": "array", "items": {"type": "integer"}}, "reason": {"type": "string"}}, ["ids", "reason"]),
]


def _saved_block():
    try:
        foods = S.saved_foods()["foods"][:40]
    except Exception:
        return ""
    if not foods:
        return ""
    line = lambda f: (f"- {f['name']} ({f['portion'] or '1 serving'}): " +
                      ", ".join(f"{k} {f[k]:g}" for k in ("kcal", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "cholesterol_mg",
                                                            "soluble_fiber_g", "sodium_mg") if f[k] is not None))
    return ("The user's saved foods (\"My foods\", exact numbers per serving). When one is mentioned, use these numbers "
            "(times the servings) instead of estimating:\n" + "\n".join(map(line, foods)) + "\n")


def _system():
    today = dt.datetime.now(S.TZ)
    t, p = S.TARGETS, S.PROFILE
    who = f"About the user: {p['about']}\n" if p["about"] else ""
    notes = f"{p['notes']}\n" if p["notes"] else ""
    cuisine = f" ({p['cuisine']} portions and dishes)" if p["cuisine"] else ""
    return f"""You are the assistant inside the user's food log app. Today is {today:%A %Y-%m-%d %H:%M} ({S.TZ_NAME}).
{who}{notes}Daily targets: {t['kcal']:g} kcal, protein ~{t['protein_g']:g} g, carbs ~{t['carbs_g']:g} g, fat ~{t['fat_g']:g} g,
sat fat <= {t['sat_fat_g']:g} g, cholesterol <= {t['cholesterol_mg']:g} mg, fibre ~{t['fiber_g']:g} g, sodium <= {t['sodium_mg']:g} mg.

{_saved_block()}
Rules:
- Use the tools for any number about logged food. Never invent logged data. Say when a day looks partly logged.
- To change data, call a propose_* tool. Nothing is saved until the user taps Apply on the card, so say "tap Apply"
  rather than claiming it is done. For edits, read the row first with list_meals.
- When the user describes or photographs food to log, estimate it{cuisine} and propose_add_meal.
- Reply in the language the user writes in. Be brief and concrete, numbers first, no lecturing.
  Use short bullet lists or a small table when comparing. Not medical advice."""


def _rows(a, b, q=""):
    for d in (a, b):
        dt.date.fromisoformat(d)
    sql = "select * from meals where log_date between ? and ?"
    args = [a, b]
    if q:
        sql += " and (foods like ? or coalesce(note,'') like ?)"
        args += [f"%{q}%", f"%{q}%"]
    with S.db() as c:
        return [S._row_out(r) for r in c.execute(sql + " order by log_date, id limit 300", args)]


def run_tool(name, args):
    try:
        if name == "list_meals":
            rows = _rows(args["date_from"], args["date_to"], str(args.get("query") or "")[:80])
            with S.db() as c:
                items = S.items_for(c, [r["id"] for r in rows])
            return {"meals": [{**{k: r.get(k) for k in ("id", "date", "meal", "foods", *S.NUM, "note")},
                               "per_food": [{k: i[k] for k in ("name", "portion", "kcal", "protein_g", "carbs_g", "fat_g",
                                                               "sat_fat_g", "cholesterol_mg")} for i in items.get(r["id"], [])]}
                              for r in rows]}
        if name == "daily_totals":
            rows = _rows(args["date_from"], args["date_to"])
            days = {}
            for r in rows:
                d = days.setdefault(r["date"], {"meals": 0, **{k: 0.0 for k in S.NUM}})
                d["meals"] += 1
                for k in S.NUM:
                    d[k] += r.get(k) or 0
            for d in days.values():
                for k in S.NUM:
                    d[k] = round(d[k], 1)
            n = len(days) or 1
            avg = {k: round(sum(d[k] for d in days.values()) / n, 1) for k in S.NUM}
            over = {k: sum(1 for d in days.values() if d[k] > S.TARGETS[k])
                    for k in ("sat_fat_g", "cholesterol_mg", "sodium_mg")}
            return {"days": days, "days_logged": len(days), "average_per_logged_day": avg, "days_over_limit": over,
                    "targets": S.TARGETS}
        if name.startswith("propose_"):
            return proposal(name, args)
    except (KeyError, ValueError, S.Bad) as e:
        return {"error": str(e)}
    return {"error": "unknown tool"}


def _get(rid):
    with S.db() as c:
        r = c.execute("select * from meals where id=?", (int(rid),)).fetchone()
    return S._row_out(r) if r else None


def proposal(name, a):
    pid = secrets.token_hex(6)
    reason = str(a.get("reason") or "")[:200]
    if name == "propose_add_meal":
        row = {k: a.get(k) for k in ("date", "meal", "foods", *S.NUM, "note")}
        items = None
        if a.get("items"):
            items = S.clean_items(a["items"])
            row.update(S._sum_items(items))   # totals come from the foods, not the model's arithmetic
        S.clean_meal(row)   # validate now so Apply can't fail on format
        return {"proposal": {"id": pid, "type": "add", "after": row, "reason": reason, **({"items": items} if items else {})}}
    if name == "propose_edit_meal":
        old = _get(a.get("id") or 0)
        if not old:
            return {"error": "no meal with that id; call list_meals first"}
        after = {k: old.get(k) for k in ("date", "meal", "foods", *S.NUM, "note")}
        changed = [k for k in after if k in a and a[k] is not None and a[k] != old.get(k)]
        for k in changed:
            after[k] = a[k]
        if not changed:
            return {"error": "nothing changes"}
        S.clean_meal(after)
        return {"proposal": {"id": pid, "type": "edit", "row_id": old["id"], "before": old, "after": after,
                             "changed": changed, "reason": reason}}
    if name == "propose_delete_meals":
        rows = [r for r in (_get(i) for i in (a.get("ids") or [])[:30]) if r]
        if not rows:
            return {"error": "none of those ids exist; call list_meals first"}
        return {"proposal": {"id": pid, "type": "delete", "rows": rows, "reason": reason}}
    return {"error": "unknown proposal"}


def chat(b, caller=None, analyzer=None):
    caller = caller or _call
    model = str(b.get("model") or S.DEFAULT_MODEL)[:80]
    msgs = [m for m in (b.get("messages") or []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    if not msgs or msgs[-1]["role"] != "user":
        raise S.Bad("say something")
    convo = [{"role": "system", "content": _system()}]
    for m in msgs[-20:-1]:
        if isinstance(m.get("content"), str) and m["content"].strip():
            convo.append({"role": m["role"], "content": m["content"][:4000]})
    text = str(msgs[-1].get("content") or "").strip()[:4000]
    images = S.images_of(b)
    if images:
        return photo_estimate(model, images, text, analyzer=analyzer)
    convo.append({"role": "user", "content": text})
    proposals, used = [], []
    for _ in range(MAX_STEPS):
        msg = caller(model, convo, TOOLS)
        calls = msg.get("tool_calls") or []
        if not calls:
            return {"reply": _text(msg).strip() or "(no answer)", "proposals": proposals, "tools": used, "model": model}
        convo.append({"role": "assistant", "content": _text(msg) or None, "tool_calls": calls})
        for tc in calls:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            res = run_tool(fn.get("name", ""), args if isinstance(args, dict) else {})
            used.append(fn.get("name", ""))
            if "proposal" in res:
                proposals.append(res["proposal"])
                res = {"ok": "shown to the user as a card; they must tap Apply", "proposal_id": res["proposal"]["id"]}
            convo.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": json.dumps(res, ensure_ascii=False)[:12000]})
    return {"reply": "I ran out of steps; try a narrower question.", "proposals": proposals, "tools": used, "model": model}


def _meal_by_clock(now):
    h = now.hour
    return "breakfast" if h < 11 else "lunch" if h < 15 else "snack" if h < 18 else "dinner" if h < 22 else "snack"


def photo_estimate(model, images, text, analyzer=None):
    """Photo in chat -> the itemized estimator (same as the old add sheet), returned as one Add card."""
    now = dt.datetime.now(S.TZ)
    today = now.date().isoformat()
    so_far = run_tool("daily_totals", {"date_from": today, "date_to": today})["days"].get(today)
    res = (analyzer or S.analyze)({"model": model, "images": images, "text": text, "date": today,
                                   "today": {k: so_far[k] for k in S.NUM} if so_far else None})
    if res.get("not_food") or not res.get("items"):
        return {"reply": "That doesn't look like food. Try another photo, or just type what you ate.", "proposals": [], "tools": ["analyze"], "model": model}
    items = res["items"]
    tot = {k: round(sum(i.get(k) or 0 for i in items), 1) for k in S.NUM}
    foods = "; ".join(f"{i['name']} ({i['portion']})" if i.get("portion") else i["name"] for i in items)[:500]
    after = {"date": today, "meal": res.get("meal") or _meal_by_clock(now), "foods": foods, **tot,
             "note": " ".join(x for x in (res.get("assumptions") or "", f"AI: {model}") if x)[:500]}
    S.clean_meal(after)
    reply = "\n\n".join(x for x in (res.get("assumptions"), res.get("tip") and f"**Tip:** {res['tip']}") if x)
    return {"reply": reply or "Here's my estimate.", "tools": ["analyze"], "model": model,
            "proposals": [{"id": secrets.token_hex(6), "type": "add", "after": after, "items": items, "reason": ""}]}


def _text(msg):
    c = msg.get("content") or ""
    if isinstance(c, list):
        c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c


def _call(model, convo, tools):
    body = {"model": model, "max_tokens": 3000, "temperature": 0.3, "messages": convo, "tools": tools}
    return S.post_chat(body)["choices"][0]["message"]
