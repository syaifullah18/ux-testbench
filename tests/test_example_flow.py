"""Walks the shipped example project end to end, as a participant and as an admin."""
from .conftest import extract_json


def finish_ab(c, base, answers):
    """Drives an ab_test module through the HTTP API the task runner uses."""
    r = c.get(base + "/")
    assert r.status_code == 302 and r.location.endswith("/s/intro")
    c.post(base + "/s/intro")
    seen_orders = []
    for n in (1, 2):
        assert c.post(f"{base}/s/brief{n}").status_code == 302
        html = c.get(f"{base}/s/tasks{n}").get_data(as_text=True)
        cfg = extract_json(html, "TASKS")
        page = c.get(cfg["frameUrl"]).get_data(as_text=True)
        seen_orders.append("filter" if "Search events" in page else "list")
        assert c.get(cfg["frameUrl"] + "events.js").status_code == 200  # sibling assets resolve
        for i, t in enumerate(cfg["tasks"]):
            sub = cfg["submitUrl"].replace("__T__", t["id"])
            met = cfg["metricsUrl"].replace("__T__", t["id"])
            if i == 0 and n == 1:
                other = cfg["submitUrl"].replace("__T__", cfg["tasks"][1]["id"])
                assert c.post(other, json={"gave_up": True, "ease": 3}).status_code == 409
                assert c.post(sub, json={"answer": {"date": "x"}, "ease": 0}).status_code == 400
                c.post(met, json={"time_ms": 9000, "clicks": 4, "click_path": ["a: Events"], "viewport_w": 390})
                c.post(met, json={"time_ms": 10, "clicks": 1})  # stale totals never roll back
            ans = answers[seen_orders[-1]][t["id"]]
            r = c.post(sub, json={"answer": ans, "ease": 5, "metrics": {"time_ms": 12000 if seen_orders[-1] == "list" else 6000,
                                                                      "clicks": 3, "viewport_w": 1280}})
            assert r.status_code == 200, r.get_json()
        assert r.get_json()["next"].endswith(f"/s/survey{n}")
        assert c.post(met, json={}).status_code == 409  # view closed
        c.post(f"{base}/s/survey{n}", data={"find_easy": "2" if seen_orders[-1] == "list" else "5", "confident": "4"})
    r = c.post(f"{base}/s/final", data={})
    assert "Choose one option" in r.get_data(as_text=True)
    r = c.post(f"{base}/s/final", data={"_preference": "1" if seen_orders[0] == "filter" else "2", "_preference_reason": "cards"})
    assert r.status_code == 302 and "done=events-ab" in r.location
    return seen_orders


RIGHT = {"list": {"date": {"date": "14 March"}, "seats": {"seats": "6"}, "sunday": {"sunday": "No"}},
         "filter": {"date": {"date": "march 14"}, "seats": {"seats": "6"}, "sunday": {"sunday": "No"}}}
WRONG = {"list": {"date": {"date": "7 March"}, "seats": {"seats": "3"}, "sunday": {"sunday": "Yes"}},
         "filter": RIGHT["filter"]}


def test_full_participant_and_admin_flow(projects_dir, make_app):
    app = make_app(projects_dir, EXAMPLE_ADMIN_PASSCODE="adm")
    orders = []
    for answers in (WRONG, RIGHT):
        c = app.test_client()
        assert "City Library" in c.get("/").get_data(as_text=True)
        r = c.post("/example/", data={"action": "start"})
        assert "consent" in r.get_data(as_text=True).lower()
        r = c.post("/example/", data={"action": "start", "consent": "1"})
        assert r.status_code == 302
        home = c.get("/example/").get_data(as_text=True)
        assert "Save your code" in home and home.count("Locked") == 2
        assert c.get("/example/m/journey/").status_code == 302  # locked until profile is done

        c.get("/example/m/profile/")
        r = c.post("/example/m/profile/s/p1", data={"role": "member", "frequency": "Weekly", "device": "mobile"})
        assert "done=profile" in r.location

        c.get("/example/m/journey/")
        page = c.get("/example/m/journey/s/p1").get_data(as_text=True)
        assert "Finding an event" in page and "Publishing an event" not in page  # show_if across modules
        form = {f"member_steps.{k}": v for k, v in
                zip(["find_event", "book_event", "search_catalog", "renew", "account"], ["4", "5", "2", "na", "1"])}
        form["priorities"] = ["find_event", "book_event", "renew"]
        r = c.post("/example/m/journey/s/p1", data=form)
        assert "Choose at most 2" in r.get_data(as_text=True)
        form["priorities"] = ["find_event", "book_event"]
        assert c.post("/example/m/journey/s/p1", data=form).status_code == 302
        r = c.post("/example/m/journey/s/p2", data={"story": "Could not find the date", "interview": "yes"})
        assert "Write at least one sentence" in r.get_data(as_text=True)  # contact shown by show_if, so required
        r = c.post("/example/m/journey/s/p2", data={"story": "Could not find the date", "interview": "yes", "contact": "me@x.org"})
        assert r.status_code == 302

        # Drive info-arch
        assert c.get("/example/m/info-arch/").status_code == 302 # start (goes to t1)
        r = c.get("/example/m/info-arch/s/t1")
        assert r.status_code == 200
        r = c.post("/example/m/info-arch/s/t1", json={"path_taken": ["catalog", "books"], "final_node": "books", "time_ms": 5000})
        assert r.status_code == 302 and "t2" in r.location
        r = c.post("/example/m/info-arch/s/t2", json={"path_taken": ["services", "rooms"], "final_node": "rooms", "time_ms": 3000})
        assert r.status_code == 302 and "post1" in r.location
        r = c.post("/example/m/info-arch/s/post1", data={"ease": "5"})
        assert r.status_code == 302 and "post2" in r.location
        r = c.post("/example/m/info-arch/s/post2", data={"ease": "4"})
        assert r.status_code == 302 and "done=info-arch" in r.location

        orders.append(finish_ab(c, "/example/m/events-ab", answers))
        home = c.get("/example/").get_data(as_text=True)
        assert "All done" in home

    assert orders[0] != orders[1]  # rotate: consecutive participants get opposite orders

    admin = app.test_client()
    assert admin.get("/example/admin/m/journey/").status_code == 302
    assert "does not match" in admin.post("/example/admin/", data={"passcode": "nope"}).get_data(as_text=True)
    admin.post("/example/admin/", data={"passcode": "adm"})
    assert "Overview" in admin.get("/example/admin/").get_data(as_text=True)
    journey = admin.get("/example/admin/m/journey/").get_data(as_text=True)
    assert "Picked as priority" in journey and "Could not find the date" in journey
    assert "&lt;section" not in journey and "&lt;table" not in journey  # report HTML must not be escaped
    ab = admin.get("/example/admin/m/events-ab/").get_data(as_text=True)
    assert "Decision rule: B against A" in ab and "2/2" in ab  # B faster for both
    assert "&lt;div" not in ab and "&lt;table" not in ab
    assert "&lt;dl" not in admin.get("/example/admin/p/1").get_data(as_text=True)
    assert admin.get("/example/admin/m/events-ab/x/preview/B/").status_code == 200

    csv_ab = admin.get("/example/admin/m/events-ab/export.csv").get_data(as_text=True)
    assert csv_ab.count("\n") == 1 + 2 * 2 * 3  # header + participants x views x tasks
    csv_j = admin.get("/example/admin/m/journey/export.csv").get_data(as_text=True)
    assert "member_steps[find_event]" in csv_j and "me@x.org" in csv_j

    people = admin.get("/example/admin/participants").get_data(as_text=True)
    assert people.count("/delete\"") == 2
    detail = admin.get("/example/admin/p/1").get_data(as_text=True)
    assert "Coordinator grade" in detail and "Click path" in detail
    import re
    key = re.search(r'name="grade_(\d+_1_date)"', detail).group(1)
    r = admin.post("/example/admin/p/1", data={f"grade_{key}": "assisted", f"note_{key}": "hesitated"})
    assert "saved=1" in r.location
    assert "hesitated" in admin.get("/example/admin/p/1").get_data(as_text=True)

    assert admin.post("/example/admin/reset", data={"passcode": "bad"}).location.endswith("reset=failed")
    admin.post("/example/admin/p/2/delete")
    assert admin.get("/example/admin/participants").get_data(as_text=True).count("/delete\"") == 1
