# 005: PostgreSQL and object storage as opt-in backends

**Context**  
ADR 003 keeps each study in its own SQLite file, and ADR 004 keeps each study's configuration as a folder of YAML. Both live in `DATA_DIR`, on one volume. That is the right default for a laptop or a small server, but a hosted instance on a platform such as Dokploy wants the database managed by the platform (backups, replicas, point-in-time recovery) and study files that survive a lost volume or a fresh container.

**Decision**  
Both stay the default, and two opt-in backends are added behind the same code:

- **`DATABASE_URL=postgresql://…`** stores each study in its own PostgreSQL schema (`study_<slug>`) and the platform tables in a `testbench` schema. `testbench/db.py` owns every connection; the rest of the code keeps writing SQLite-flavoured SQL, which the layer adapts (placeholders, identity columns, the few functions that differ). A request uses one pooled connection and switches `search_path` per study, so a query on one study cannot reach another study's rows, which is the isolation ADR 003 chose files for.
- **`STORAGE_BACKEND=s3`** makes a bucket (AWS S3, Cloudflare R2 or MinIO) the durable copy of every Studio study folder. Changes are mirrored to the bucket after each request that makes them; at start, the app downloads anything the disk is missing. Prototypes are still served from the local copy, on the app's origin, as ADR 001 requires. Exports are written to the bucket and handed out as links that expire.

**Alternatives rejected**  
- **One PostgreSQL database with a `project_id` column on every table**: rejected for the reason ADR 003 gives; one missing `WHERE` leaks rows between studies. A schema per study keeps the boundary in the database.
- **Replacing SQLite outright**: makes local development and small self-hosted instances depend on a database server for no gain at their scale.
- **Serving prototypes straight from the bucket**: breaks ADR 001; the task runner can only instrument a prototype on the app's own origin.
- **Moving the YAML into the database**: would undo ADR 004. Mirroring the folder keeps the YAML editable as files and still survives a lost volume.

**Costs accepted**  
- Two code paths in `db.py`, and a test suite that has to pass on both (`TEST_DATABASE_URL` runs it on PostgreSQL).
- The local Studio folder is a cache of the bucket, so a second app replica without a shared volume sees another replica's edits only after it restarts. Run one replica, or share the volume.
- `.history` (the Studio's edit backups) is not mirrored.
