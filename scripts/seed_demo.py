"""Seed instance/example.db with the deterministic sample data from reference/admin/assets/demo-people.js.
Creates 14 participants, sessions, answers, and task_results so the example study has full results.
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path("instance/example.db")

PARTICIPANTS = [
    ("K7Q2-9FXA", "2026-09-29T09:12:00Z"),
    ("M3TP-4RZB", "2026-09-29T08:47:00Z"),
    ("W8HD-2NQC", "2026-09-28T17:30:00Z"),
    ("B5XV-7KLE", "2026-09-28T15:02:00Z"),
    ("R2JG-6PYD", "2026-09-28T11:26:00Z"),
    ("T9CM-3WSA", "2026-09-24T16:40:00Z"),
    ("H4NF-8BQV", "2026-09-24T10:15:00Z"),
    ("Z6LK-1DTX", "2026-09-23T14:58:00Z"),
    ("C7PR-5HGE", "2026-09-22T13:21:00Z"),
    ("F3SV-9MJW", "2026-09-22T09:03:00Z"),
    ("Q8YB-2CXN", "2026-09-21T18:44:00Z"),
    ("L5DA-7ETK", "2026-09-20T12:09:00Z"),
    ("N4WU-6RHZ", "2026-09-17T15:35:00Z"),
    ("D9GQ-3VFP", "2026-09-15T10:50:00Z"),
]

ROLES = [
    "member", "member", "staff", "member", "visitor",
    "member", "member", "staff", "member", "member",
    "staff", "visitor", "member", "member"
]
FREQS = [
    "Weekly", "Monthly", "Weekly", "A few times a year", "Monthly",
    "Weekly", "Monthly", "Weekly", "A few times a year", "Monthly",
    "Weekly", "Never", "Monthly", "Monthly"
]
DEVICES = [
    "mobile", "desktop", "both", "mobile", "desktop",
    "mobile", "both", "desktop", "mobile", "desktop",
    "both", "mobile", "desktop", "desktop"
]
AGES = [
    "18-34", "35-54", "55+", "18-34", "Prefer not to say",
    "35-54", "18-34", "35-54", "55+", "18-34",
    "35-54", "18-34", "55+", "35-54"
]

PICKS = [
    ["find_event", "book_event"],
    ["find_event"],
    ["publish_event"],
    ["book_event"],
    ["find_event"],
    ["find_event"],
    ["search_catalog"],
    ["manage_bookings"],
    ["book_event"],
    ["find_event"],
]

STORIES = [
    "I could not tell which events still had seats.",
    "The list was long and I lost my place.",
    "Booking asked me to sign in twice.",
    "I could not find the date without opening each event.",
    "The search on the catalogue page did not find the book.",
    "I gave up looking for the weekend events.",
    "The page took a long time on my phone.",
    "I wanted to filter by day.",
    "The confirmation email did not arrive.",
    "It was fine, I just could not see seat numbers."
]

AB_FAIL_A = {"date": [2, 7, 9], "seats": [1, 5, 8, 10], "sunday": [3, 6]}
AB_FAIL_B = {"date": [4], "seats": [], "sunday": [0, 5, 11]}
AB_GAVE_A = {"date": [9], "seats": [1, 5], "sunday": []}
AB_GAVE_B = {"date": [], "seats": [], "sunday": [11]}
MOBILE_USERS = {2, 5, 8, 11}

COMMENTS = [
    "The search box found it straight away.",
    "I kept opening every event to see seat numbers.",
    "Filters were clear but I missed the days at first.",
    "Too much scrolling.",
    "Seat counts on the card were a relief.",
    "Fast to scan.",
    "Easy to use.",
    "Clear layout.",
    "Found everything quickly.",
    "Simple navigation.",
    "Great search filters.",
    "A bit confusing at first."
]


def seed():
    if not DB_PATH.exists():
        print(f"Error: {DB_PATH} does not exist.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    
    # Check if participants already exist
    count = conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    if count > 0:
        print(f"Database already has {count} participants. Clearing to re-seed...")
        conn.execute("DELETE FROM participants")

    print("Seeding participants and session data...")
    for idx, (ident, seen) in enumerate(PARTICIPANTS):
        conn.execute(
            "INSERT INTO participants (id, identity, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (idx + 1, ident, seen, seen)
        )
        pid = idx + 1

        # 1. Profile module (14 started, 13 finished)
        if idx < 13:
            prof_fin = seen
            prof_step = "p1"
        else:
            prof_fin = None
            prof_step = "p1"

        c1 = conn.execute(
            "INSERT INTO sessions (participant_id, module_id, step, state, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, "profile", prof_step, json.dumps({}), seen, prof_fin)
        )
        sid_profile = c1.lastrowid
        conn.execute(
            "INSERT INTO answers (session_id, page, data, updated_at) VALUES (?, ?, ?, ?)",
            (sid_profile, "p1", json.dumps({
                "role": ROLES[idx],
                "frequency": FREQS[idx],
                "device": DEVICES[idx],
                "age_band": AGES[idx]
            }), seen)
        )

        # 2. Journey module (13 started, 10 finished, 3 stopped at page 2)
        if idx < 13:
            is_fin_journey = idx < 10
            j_fin = seen if is_fin_journey else None
            j_step = "p2" if is_fin_journey else "p2"
            c2 = conn.execute(
                "INSERT INTO sessions (participant_id, module_id, step, state, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
                (pid, "journey", j_step, json.dumps({}), seen, j_fin)
            )
            sid_journey = c2.lastrowid
            
            p1_answers = {}
            if ROLES[idx] == "staff":
                p1_answers["staff_steps"] = {"publish_event": "2", "manage_bookings": "3", "reports": "4"}
                p1_answers["staff_priorities"] = ["publish_event"]
            else:
                p1_answers["member_steps"] = {
                    "find_event": "4" if idx % 2 == 0 else "3",
                    "book_event": "5" if idx % 3 == 0 else "2",
                    "search_catalog": "2",
                    "renew": "na" if idx == 4 else "1",
                    "account": "1"
                }
                p1_answers["priorities"] = PICKS[idx] if idx < len(PICKS) else ["find_event"]
            
            conn.execute(
                "INSERT INTO answers (session_id, page, data, updated_at) VALUES (?, ?, ?, ?)",
                (sid_journey, "p1", json.dumps(p1_answers), seen)
            )

            if is_fin_journey:
                conn.execute(
                    "INSERT INTO answers (session_id, page, data, updated_at) VALUES (?, ?, ?, ?)",
                    (sid_journey, "p2", json.dumps({
                        "stuck": ["Ask staff"] if idx % 2 == 0 else ["Search the help page"],
                        "story": STORIES[idx],
                        "interview": "yes" if idx in (0, 3, 6) else "no",
                        "contact": f"participant{pid}@example.org" if idx in (0, 3, 6) else ""
                    }), seen)
                )

        # 3. Info-arch module (12 started, 11 finished, 1 stopped at task 2)
        if idx < 12:
            is_fin_ia = idx < 11
            ia_fin = seen if is_fin_ia else None
            c3 = conn.execute(
                "INSERT INTO sessions (participant_id, module_id, step, state, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
                (pid, "info-arch", "t2" if not is_fin_ia else "post", json.dumps({}), seen, ia_fin)
            )
            sid_ia = c3.lastrowid

            t1_ok = idx not in (3, 8)
            t1_node = "rooms" if t1_ok else "computers"
            t1_data = {
                "path_taken": ["services", t1_node],
                "final_node": t1_node,
                "time_ms": 7000 + (idx % 4) * 1000
            }
            conn.execute(
                "INSERT INTO answers (session_id, page, data, updated_at) VALUES (?, ?, ?, ?)",
                (sid_ia, "t1", json.dumps(t1_data), seen)
            )

            if is_fin_ia:
                t2_ok = idx in (0, 2, 4, 7, 9)
                t2_node = "local_history" if t2_ok else "databases"
                t2_data = {
                    "path_taken": ["resources", t2_node],
                    "final_node": t2_node,
                    "time_ms": 12000 + (idx % 5) * 1000
                }
                conn.execute(
                    "INSERT INTO answers (session_id, page, data, updated_at) VALUES (?, ?, ?, ?)",
                    (sid_ia, "t2", json.dumps(t2_data), seen)
                )
                conn.execute(
                    "INSERT INTO answers (session_id, page, data, updated_at) VALUES (?, ?, ?, ?)",
                    (sid_ia, "post", json.dumps({"ease": "5" if (t1_ok and t2_ok) else "3"}), seen)
                )

        # 4. Events-ab module (13 started, 12 finished, 1 stopped at task 1)
        if idx < 13:
            is_fin_ab = idx < 12
            ab_fin = seen if is_fin_ab else None
            order = ["A", "B"] if idx % 2 == 0 else ["B", "A"]
            viewport_w = 390 if idx in MOBILE_USERS else 1280

            c4 = conn.execute(
                "INSERT INTO sessions (participant_id, module_id, step, state, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
                (pid, "events-ab", "final" if is_fin_ab else "tasks1", json.dumps({"order": order}), seen, ab_fin)
            )
            sid_ab = c4.lastrowid

            if is_fin_ab:
                # Tasks for each variant
                for pos, v in enumerate(order, start=1):
                    # Task 1: date
                    t1_failed = idx in AB_FAIL_A["date"] if v == "A" else idx in AB_FAIL_B["date"]
                    t1_gave = idx in AB_GAVE_A["date"] if v == "A" else idx in AB_GAVE_B["date"]
                    t1_ans = "14 March" if not (t1_failed or t1_gave) else ("2 March" if t1_failed else "")
                    t1_time = (31 if v == "A" else 14) + (idx % 5 - 2) * (2 if v == "A" else 1)
                    conn.execute(
                        "INSERT INTO task_results (session_id, position, variant, task_id, time_ms, clicks, scroll_reversals, first_click, click_path, viewport_w, answer, gave_up, ease, auto_pass, grade, finished_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (sid_ab, pos, v, "date", max(5000, t1_time * 1000), 3 if v == "B" else 6, 1 if v == "A" else 0,
                         "Search events" if v == "B" else "More details", json.dumps(["filter", "results"]), viewport_w,
                         json.dumps({"date": t1_ans}), 1 if t1_gave else 0, 6 if v == "B" else 4,
                         1 if not (t1_failed or t1_gave) else 0, "success" if not (t1_failed or t1_gave) else "fail", seen, seen)
                    )

                    # Task 2: seats
                    t2_failed = idx in AB_FAIL_A["seats"] if v == "A" else idx in AB_FAIL_B["seats"]
                    t2_gave = idx in AB_GAVE_A["seats"] if v == "A" else idx in AB_GAVE_B["seats"]
                    t2_ans = "6" if not (t2_failed or t2_gave) else ("5" if t2_failed else "")
                    t2_time = (26 if v == "A" else 12) + (idx % 4 - 2) * (2 if v == "A" else 1)
                    conn.execute(
                        "INSERT INTO task_results (session_id, position, variant, task_id, time_ms, clicks, scroll_reversals, first_click, click_path, viewport_w, answer, gave_up, ease, auto_pass, grade, finished_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (sid_ab, pos, v, "seats", max(4000, t2_time * 1000), 2 if v == "B" else 5, 1 if v == "A" else 0,
                         "Card text" if v == "B" else "More details", json.dumps(["view", "card"]), viewport_w,
                         json.dumps({"seats": t2_ans}), 1 if t2_gave else 0, 6 if v == "B" else 4,
                         1 if not (t2_failed or t2_gave) else 0, "success" if not (t2_failed or t2_gave) else "fail", seen, seen)
                    )

                    # Task 3: sunday
                    t3_failed = idx in AB_FAIL_A["sunday"] if v == "A" else idx in AB_FAIL_B["sunday"]
                    t3_gave = idx in AB_GAVE_A["sunday"] if v == "A" else idx in AB_GAVE_B["sunday"]
                    t3_ans = "No" if not (t3_failed or t3_gave) else ("Yes" if t3_failed else "")
                    t3_time = (18 if v == "A" else 22) + (idx % 4 - 1)
                    conn.execute(
                        "INSERT INTO task_results (session_id, position, variant, task_id, time_ms, clicks, scroll_reversals, first_click, click_path, viewport_w, answer, gave_up, ease, auto_pass, grade, finished_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (sid_ab, pos, v, "sunday", max(5000, t3_time * 1000), 2, 0,
                         "Day filter" if v == "B" else "List", json.dumps(["nav", "calendar"]), viewport_w,
                         json.dumps({"sunday": t3_ans}), 1 if t3_gave else 0, 5,
                         1 if not (t3_failed or t3_gave) else 0, "success" if not (t3_failed or t3_gave) else "fail", seen, seen)
                    )

                    # Survey post per variant
                    conn.execute(
                        "INSERT INTO answers (session_id, page, data, updated_at) VALUES (?, ?, ?, ?)",
                        (sid_ab, f"survey{pos}", json.dumps({
                            "find_easy": 6 if v == "B" else 4,
                            "confident": 6 if v == "B" else 4,
                            "comment": COMMENTS[idx] if pos == 2 else ""
                        }), seen)
                    )

                # Final survey
                pref_val = "2" if order[1] == "B" else "1"  # most prefer B
                if idx == 11:
                    pref_val = "none"
                conn.execute(
                    "INSERT INTO answers (session_id, page, data, updated_at) VALUES (?, ?, ?, ?)",
                    (sid_ab, "final", json.dumps({
                        "_preference": pref_val,
                        "_preference_reason": "Searching made it much faster." if pref_val != "none" else "Both worked fine for me.",
                        "other_ideas": "Add an export to calendar button." if idx % 3 == 0 else ""
                    }), seen)
                )

    conn.commit()
    conn.close()
    print("Seeding completed successfully! 14 participants seeded.")


if __name__ == "__main__":
    seed()
