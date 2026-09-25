import random
import uuid
import json

from . import storage


def seed_example(n_participants):
    from . import create_app
    app = create_app()
    with app.app_context():
        slug = "example"
        conn = storage.connect(slug)
        
        print(f"Seeding {n_participants} participants into '{slug}'...")
        roles = ["member", "staff", "visitor"]
        
        for _ in range(n_participants):
            identity = f"DEMO-{uuid.uuid4().hex[:6].upper()}"
            ts = storage.now_iso()
            
            # 1. Participant
            conn.execute("INSERT INTO participants (identity, created_at, last_seen_at) VALUES (?, ?, ?)",
                         (identity, ts, ts))
            pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            
            # 2. Profile
            role = random.choice(roles)
            conn.execute("INSERT INTO sessions (participant_id, module_id, step, started_at, finished_at) VALUES (?, ?, ?, ?, ?)",
                         (pid, "profile", "final", ts, ts))
            sid_profile = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ans_profile = {
                "role": role,
                "frequency": random.choice(["Weekly", "Monthly", "Never"]),
                "device": random.choice(["desktop", "mobile", "both"]),
                "age_band": random.choice(["18-34", "35-54"])
            }
            storage.save_page(conn, sid_profile, "page1", ans_profile)
            
            # 3. Journey
            conn.execute("INSERT INTO sessions (participant_id, module_id, step, started_at, finished_at) VALUES (?, ?, ?, ?, ?)",
                         (pid, "journey", "final", ts, ts))
            sid_journey = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            ans_j1 = {}
            if role in ("member", "visitor"):
                ans_j1["member_steps"] = {"find_event": random.choice([1, 5]), "search_catalog": 1}
                ans_j1["priorities"] = ["find_event"]
            else:
                ans_j1["staff_steps"] = {"publish_event": 5}
                ans_j1["staff_priorities"] = ["publish_event"]
            storage.save_page(conn, sid_journey, "page1", ans_j1)
            ans_j2 = {"stuck": ["Search the help page"], "interview": "no"}
            storage.save_page(conn, sid_journey, "page2", ans_j2)
            
            # 4. events-ab
            order = ["A", "B"] if random.random() < 0.5 else ["B", "A"]
            conn.execute("INSERT INTO sessions (participant_id, module_id, step, state, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (pid, "events-ab", "final", storage.dumps({"order": order}), ts, ts))
            sid_ab = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            
            for n, variant in enumerate(order, start=1):
                # Tasks
                for task_id in ["date", "seats", "sunday"]:
                    time_ms = random.randint(5000, 30000)
                    if variant == "B" and task_id == "date":
                        time_ms = random.randint(3000, 15000)
                    
                    conn.execute("INSERT INTO task_results (session_id, position, variant, task_id, time_ms, clicks, scroll_reversals, answer, ease, auto_pass, finished_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (sid_ab, n, variant, task_id, time_ms, random.randint(1, 5), random.randint(0, 2), storage.dumps({task_id: "demo"}), random.randint(3, 7), 1, ts, ts))
                
                # Survey n
                ans_surv = {
                    "find_easy": random.randint(3, 5) if variant == "B" else random.randint(1, 4),
                    "confident": random.randint(3, 5)
                }
                storage.save_page(conn, sid_ab, f"survey{n}", ans_surv)
                
            storage.save_page(conn, sid_ab, "final", {"other_ideas": "", "_preference": "1" if order[0] == "B" else "2"})
        
        conn.commit()
        print("Done.")
