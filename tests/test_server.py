import base64, json, os, sqlite3, sys, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
os.environ.update(FOOD_ENV_FILE=f"{TMP}/none.env", FOOD_DATA_DIR=TMP, FOOD_TRUST_HEADER="X-Test-User",
                  FOOD_ALLOWED_USER="me@x", FOOD_PASSWORD="correct horse", FOOD_TZ="UTC")
sys.path.insert(0, HERE)
import server  # noqa: E402

server.init_db()
srv = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 64).decode()


def call(path, body=None, user="me@x", hdr=True):
    h = {"X-Test-User": user} if user else {}
    if body is not None:
        h["Content-Type"] = "application/json"
        if hdr:
            h["X-Food"] = "1"
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), headers=h,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}") if "json" in r.headers.get("Content-Type", "") else r.read()
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


MEAL = {"date": "2026-11-17", "meal": "lunch", "foods": "nasi lemak (1 plate)", "kcal": 650, "protein_g": 18,
        "carbs_g": 80, "fat_g": 28, "sat_fat_g": 14, "cholesterol_mg": 190}


class T(unittest.TestCase):
    def test_auth_and_csrf(self):
        self.assertEqual(call("/api/meals", user=None)[0], 401)
        self.assertEqual(call("/api/meals", user="other@x")[0], 401)
        self.assertEqual(call("/manifest.json", user=None)[0], 200)
        self.assertEqual(call("/api/meals/add", MEAL, hdr=False)[0], 403)

    def test_crud_and_external_writes(self):
        # another tool writes a row directly into the table: the app must see it
        with sqlite3.connect(server.DB) as c:
            c.execute("insert into meals (log_date, meal, foods, kcal) values ('2026-11-17','breakfast','2 eggs',140)")
        code, r = call("/api/meals/add", {**MEAL, "image": JPEG})
        self.assertEqual(code, 200, r)
        rid = r["id"]
        code, d = call("/api/meals?month=2026-11")
        foods = [x["foods"] for x in d["rows"]]
        self.assertIn("2 eggs", foods)
        self.assertIn("nasi lemak (1 plate)", foods)
        row = next(x for x in d["rows"] if x["id"] == rid)
        self.assertTrue(row["photo"].startswith("2026-11/2026-11-17_"))
        self.assertEqual(call("/photo/" + row["photo"])[0], 200)
        # CSV export written from the DB
        text = open(f"{TMP}/csv/2026-11.csv").read()
        self.assertIn("nasi lemak", text)
        self.assertIn("2 eggs", text)
        # duplicate is refused (UNIQUE(log_date, meal, foods))
        self.assertEqual(call("/api/meals/add", MEAL)[0], 400)
        # update keeps the photo link (row id stable)
        code, _ = call("/api/meals/update", {**MEAL, "id": rid, "kcal": 600})
        self.assertEqual(code, 200)
        with sqlite3.connect(server.DB) as c:
            self.assertEqual(c.execute("select kcal from meals where id=?", (rid,)).fetchone()[0], 600)
        self.assertIn(str(rid), server.photo_index())
        # delete
        self.assertEqual(call("/api/meals/delete", {"id": rid})[0], 200)
        self.assertNotIn("nasi lemak", open(f"{TMP}/csv/2026-11.csv").read())
        self.assertEqual(call("/api/meals/delete", {"id": rid})[0], 400)

    def test_password_session(self):
        def post(path, body, cookie=None):
            h = {"Content-Type": "application/json", "X-Food": "1", **({"Cookie": cookie} if cookie else {})}
            req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers=h, method="POST")
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, r.headers.get("Set-Cookie")
            except urllib.error.HTTPError as e:
                return e.code, None
        def get(path, cookie=None):
            req = urllib.request.Request(BASE + path, headers={"Cookie": cookie} if cookie else {})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, b""
        code, page = get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"login.js", page)                         # signed out -> sign-in page, not the app
        self.assertEqual(post("/api/login", {"password": "nope"})[0], 401)
        code, sc = post("/api/login", {"password": "correct horse"})
        self.assertEqual(code, 200)
        self.assertIn("HttpOnly", sc)
        self.assertIn("SameSite=Strict", sc)
        cookie = sc.split(";")[0]
        self.assertEqual(get("/api/meals", cookie)[0], 200)
        self.assertIn(b"app.js", get("/", cookie)[1])
        self.assertEqual(get("/api/meals", "food_session=9999999999.forged")[0], 401)
        self.assertEqual(get("/api/meals", "food_session=1.abc")[0], 401)   # expired / bad signature
        self.assertFalse(server.session_ok(""))

    def test_validation(self):
        self.assertEqual(call("/api/meals/add", {**MEAL, "meal": "brunch"})[0], 400)
        self.assertEqual(call("/api/meals/add", {**MEAL, "date": "x"})[0], 400)
        self.assertEqual(call("/api/meals/add", {**MEAL, "kcal": -5})[0], 400)
        self.assertEqual(call("/api/meals/add", {**MEAL, "foods": "x2", "image": base64.b64encode(b"GIF89a").decode()})[0], 400)
        self.assertEqual(call("/photo/../../etc/passwd")[0], 404)

    def test_analyze_sanitises_model_output(self):
        def fake(model, messages):
            self.assertEqual(messages[1]["content"][1]["type"], "image_url")
            return '```json\n{"meal":"lunch","items":[{"name":"Char kuey teow","portion":"1 plate","grams":"350",' \
                   '"kcal":740,"sat_fat_g":"12.34","sodium_mg":-3,"confidence":"weird"}],"assumptions":"lard","tip":"t"}\n```'
        r = server.analyze({"model": "m", "image": JPEG, "text": ""}, caller=fake)
        it = r["items"][0]
        self.assertEqual((it["kcal"], it["sat_fat_g"], it["sodium_mg"], it["confidence"], it["grams"]),
                         (740, 12.3, 0, "medium", 350))
        self.assertEqual(r["meal"], "lunch")
        with self.assertRaises(server.Bad):
            server.analyze({"model": "m"}, caller=fake)

    def test_chat_tools_propose_without_writing(self):
        import assistant
        code, r = call("/api/meals/add", {**MEAL, "foods": "chat test rice", "kcal": 300})
        rid = r["id"]
        steps = iter([
            {"tool_calls": [{"id": "a", "function": {"name": "daily_totals", "arguments": '{"date_from":"2026-11-17","date_to":"2026-11-17"}'}},
                            {"id": "b", "function": {"name": "propose_edit_meal", "arguments": json.dumps({"id": rid, "kcal": 250, "reason": "smaller"})}},
                            {"id": "c", "function": {"name": "propose_delete_meals", "arguments": '{"ids":[999999],"reason":"x"}'}}]},
            {"content": "Tap Apply."}])
        seen = []
        def fake(model, convo, tools):
            seen.append(convo[-1])
            return next(steps)
        out = assistant.chat({"model": "m", "messages": [{"role": "user", "content": "fix lunch"}]}, caller=fake)
        self.assertEqual(out["reply"], "Tap Apply.")
        self.assertEqual([p["type"] for p in out["proposals"]], ["edit"])
        self.assertEqual(out["proposals"][0]["changed"], ["kcal"])
        self.assertIn("error", json.loads(seen[-1]["content"]))            # bad delete id reported back to the model
        with sqlite3.connect(server.DB) as c:                  # proposal did not write
            self.assertEqual(c.execute("select kcal from meals where id=?", (rid,)).fetchone()[0], 300)
        tot = assistant.run_tool("daily_totals", {"date_from": "2026-11-17", "date_to": "2026-11-17"})
        self.assertGreaterEqual(tot["days"]["2026-11-17"]["kcal"], 300)
        self.assertIn("error", assistant.run_tool("propose_add_meal", {"date": "2026-11-17", "meal": "brunch", "foods": "x", "kcal": 1}))
        self.assertEqual(call("/api/chat", {"model": "m", "messages": []})[0], 400)
        call("/api/meals/delete", {"id": rid})

    def test_chat_photo_uses_itemized_estimator(self):
        import assistant
        fake = lambda b: {"model": "m", "meal": "dinner", "not_food": False, "assumptions": "no santan", "tip": "ok",
                          "items": [{"name": "rice", "portion": "1 cup", "kcal": 200, "sat_fat_g": 0.1},
                                    {"name": "fish", "portion": "", "kcal": 150, "sat_fat_g": 1}]}
        out = assistant.chat({"model": "m", "image": JPEG, "messages": [{"role": "user", "content": "dinner"}]}, analyzer=fake)
        p = out["proposals"][0]
        self.assertEqual((p["type"], p["after"]["kcal"], p["after"]["sat_fat_g"], p["after"]["meal"]), ("add", 350, 1.1, "dinner"))
        self.assertEqual(p["after"]["foods"], "rice (1 cup); fish")
        self.assertEqual(len(p["items"]), 2)

    def test_per_food_items(self):
        items = [{"name": "nasi", "portion": "1 cup", "kcal": 300, "protein_g": 5, "sat_fat_g": 6},
                 {"name": "egg", "portion": "1", "kcal": 75, "protein_g": 6.5, "cholesterol_mg": 186}]
        code, r = call("/api/meals/add", {**MEAL, "foods": "nasi; egg", "kcal": 1, "items": items})
        self.assertEqual(code, 200, r)
        rid = r["id"]
        row = next(x for x in call("/api/meals?month=2026-11")[1]["rows"] if x["id"] == rid)
        self.assertEqual((row["kcal"], row["protein_g"], row["cholesterol_mg"]), (375, 11.5, 186))   # totals = sum of foods
        self.assertEqual([i["name"] for i in row["items"]], ["nasi", "egg"])
        self.assertFalse(row["items_stale"])
        # replace breakdown
        call("/api/meals/update", {**MEAL, "id": rid, "foods": "nasi", "items": items[:1]})
        row = next(x for x in call("/api/meals?month=2026-11")[1]["rows"] if x["id"] == rid)
        self.assertEqual((row["kcal"], len(row["items"])), (300, 1))
        # another tool edits totals directly -> breakdown flagged stale, not silently wrong
        with sqlite3.connect(server.DB) as c:
            c.execute("update meals set kcal=500 where id=?", (rid,))
        row = next(x for x in call("/api/meals?month=2026-11")[1]["rows"] if x["id"] == rid)
        self.assertTrue(row["items_stale"])
        self.assertEqual(call("/api/meals/add", {**MEAL, "foods": "x", "items": [{"name": "", "kcal": 1}]})[0], 400)
        # split keeps the logged totals
        fake = lambda b: {"model": "m", "items": [{"name": "a", "kcal": 100, "protein_g": 1}, {"name": "b", "kcal": 300, "protein_g": 3}]}
        out = server.split_meal({"id": rid, "model": "m"}, analyzer=fake)
        self.assertEqual([i["kcal"] for i in out["items"]], [125, 375])
        call("/api/meals/delete", {"id": rid})
        with sqlite3.connect(server.DB) as c:
            self.assertEqual(c.execute("select count(*) from meal_items where meal_id=?", (rid,)).fetchone()[0], 0)

    def test_saved_foods(self):
        f = {"name": "Whey protein", "portion": "1 scoop (35 g)", "grams": 35, "kcal": 140, "protein_g": 25, "sat_fat_g": 0.5}
        code, r = call("/api/foods/save", f)
        self.assertEqual(code, 200, r)
        fid = r["id"]
        code, r = call("/api/foods/save", {**f, "name": "whey PROTEIN", "kcal": 145})   # upsert, case-insensitive
        self.assertEqual((r["id"], len(r["foods"]), r["foods"][0]["kcal"]), (fid, 1, 145))
        self.assertEqual(call("/api/foods/save", {"name": "", "kcal": 1})[0], 400)
        code, r = call("/api/meals/add", {**MEAL, "foods": "Whey x2", "items": [{**f, "kcal": 290, "protein_g": 50}], "saved_ids": [fid]})
        self.assertEqual(code, 200, r)
        foods = call("/api/foods")[1]["foods"]
        self.assertEqual((foods[0]["uses"], foods[0]["name"]), (1, "Whey protein"))
        import assistant
        self.assertIn("Whey protein (1 scoop (35 g)): kcal 145", assistant._system())
        call("/api/meals/delete", {"id": r["id"]})
        self.assertEqual(call("/api/foods/delete", {"id": fid})[1]["foods"], [])

    def test_multi_photo(self):
        code, r = call("/api/meals/add", {**MEAL, "foods": "two photos", "images": [JPEG, JPEG]})
        self.assertEqual(code, 200, r)
        row = next(x for x in call("/api/meals?month=2026-11")[1]["rows"] if x["id"] == r["id"])
        self.assertEqual(len(row["photos"]), 2)
        self.assertEqual(row["photo"], row["photos"][0])
        self.assertEqual(call("/photo/" + row["photos"][1])[0], 200)
        self.assertEqual(call("/api/meals/add", {**MEAL, "foods": "five", "images": [JPEG] * 5})[0], 400)
        seen = {}
        def fake(model, messages):
            seen["n"] = sum(1 for p in messages[1]["content"] if p["type"] == "image_url")
            seen["t"] = messages[1]["content"][0]["text"]
            return '{"items": [{"name": "x", "kcal": 1}]}'
        server.analyze({"model": "m", "images": [JPEG, JPEG, JPEG], "text": ""}, caller=fake)
        self.assertEqual(seen["n"], 3)
        self.assertIn("Count each food once", seen["t"])
        call("/api/meals/delete", {"id": r["id"]})

    def test_background_jobs(self):
        import time as _t
        calls = {"n": 0}
        def flaky(b):
            calls["n"] += 1
            _t.sleep(0.2)
            if calls["n"] == 1:
                raise server.Bad("model: HTTP 502")
            return {"reply": "ok", "proposals": []}
        old = server.JOB_KINDS["chat"]
        server.JOB_KINDS["chat"] = flaky
        try:
            code, r = call("/api/jobs/start", {"kind": "chat", "messages": [{"role": "user", "content": "hi"}]})
            self.assertEqual((code, r["status"]), (200, "running"))
            jid = r["job"]
            self.assertEqual(call(f"/api/jobs?id={jid}")[1]["status"], "running")   # returns at once
            for _ in range(50):
                st = call(f"/api/jobs?id={jid}")[1]
                if st["status"] != "running":
                    break
                _t.sleep(0.05)
            self.assertEqual((st["status"], st["error"]), ("error", "model: HTTP 502"))
            call("/api/jobs/retry", {"id": jid})                                       # same request rerun server-side
            for _ in range(50):
                st = call(f"/api/jobs?id={jid}")[1]
                if st["status"] != "running":
                    break
                _t.sleep(0.05)
            self.assertEqual((st["status"], st["result"]["reply"]), ("done", "ok"))
        finally:
            server.JOB_KINDS["chat"] = old
        self.assertEqual(call("/api/jobs?id=nope")[0], 404)
        self.assertEqual(call("/api/jobs/retry", {"id": "nope"})[0], 400)
        self.assertEqual(call("/api/jobs/start", {"kind": "rm -rf"})[0], 400)
        self.assertEqual(call("/api/jobs/start", {"kind": "chat", "images": ["R0lGODlh"]})[0], 400)
        self.assertEqual(call("/api/jobs?id=x", user="evil@x")[0], 401)

    def test_assets(self):
        for p in server.STATIC_FILES:
            self.assertEqual(call(p)[0], 200, p)


if __name__ == "__main__":
    unittest.main()
