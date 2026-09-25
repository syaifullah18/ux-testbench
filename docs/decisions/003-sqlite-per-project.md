# 003: SQLite file per project

**Context**  
The application hosts multiple distinct research projects (studies) simultaneously. Each project needs to store participant sessions, answers, and task results.

**Decision**  
Each project receives its own dedicated SQLite database file (e.g. `DATA_DIR/slug.db`).

**Alternatives rejected**  
- **Single database with a tenant column**: Requires adding `project_id` to every query, risking accidental data leakage between studies. It also makes archiving, duplicating, or deleting a single project much harder and slower.
- **PostgreSQL / external databases**: Overkill for the scale of typical UX research studies (dozens of concurrent users). Introduces operational complexity and deployment hurdles.

**Costs accepted**  
- Managing multiple database connections across the application.
- No easy way to run cross-project aggregate queries (though UX research rarely requires this).
- Backup mechanisms must handle a folder of `.db` files rather than a single database dump.
