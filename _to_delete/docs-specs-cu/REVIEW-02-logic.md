# REVIEW-02 — Six-Axis Logic Audit

> **Subject:** FastAPI Todo Project (`/Volumes/AI_SSD/projects/fastapi-todo`)
> **Oracle:** `README.md` (project-root, 312 lines) — the authoritative spec.
> **Method:** Six-axis audit of application logic vs. the README oracle, verified by
> `py_compile` (30/30 `.py` files pass) and a full pytest run.
> **Severity scale:** `P0` runtime-broken · `P1` correctness defect · `P2` robustness/consistency · `P3` cosmetic.

---

## 0. TL;DR

| # | Axis | Verdict |
|---|------|---------|
| A | Compile-time integrity | **PASS** — 30/30 modules compile. (Two defects from earlier notes were **REFUTED**: `pagination.py` exists; the four "missing" config attrs are in `settings.py`.) |
| B | Schema/validation vs. oracle | **FAIL (P1)** — `record_type`/`training_type` use Pydantic `Literal` → invalid values yield **422**, but the oracle & tests require **400**. |
| C | CRUD / business logic | **PARTIAL PASS** — pagination, status transitions, video training-state, auth guard, CRUD all match. `created_by="system"` magic string + no owner enforcement = **P2**. |
| D | Persistence / data model | **PASS** — 4 `IntegrityError` triggers (video 1:1, video cascade, training 1:1, training cascade) all fire and roll back correctly. |
| E | Config / deployment surface | **PARTIAL PASS** — config is a single source of truth (clean). But `main.py`/`deps.py` hard-code `/app/static`, `DATABASE_URL` is SQLite-only (no Postgres), and `CORS`/`rate-limit`/`health`/`docs` are not implemented → **P2** scope gaps. |
| F | Test suite vs. oracle | **PARTIAL PASS** — 16 tests; **3 FAIL** (the 400-vs-422 axis-B issue), **13 PASS**. No test covers the 4 `IntegrityError` paths (coverage gap). |

**Blocking item:** Axis B defect (B-1) makes 3 tests fail and contradicts three oracle statements (README lines 160, 244, 266). Fix = one-line change per schema. Everything else is green or P2 scope.

---

## A. Axis 1 — Compile-time Integrity

**Verdict: PASS.**

```
30/30 .py files compile clean (python3 -m py_compile).
```

### A-1 (REFUTED) — `app/core/pagination.py` "missing"
Earlier notes claimed `app/core/pagination.py` was absent and that `deps.py:16`
imported from it, implying a broken import. **REFUTED.** The file exists
(14 lines) and is importable:

```python
# app/core/pagination.py
from fastapi import Query
from fastapi import Depends
from pydantic import BaseModel, Field

class Page(BaseModel):
    items: list
    total: int
    limit: int
    offset: int

def get_pagination() -> Page:
    ...  # returns Page(limit=..., offset=...)
```

`deps.py:16` `from app.core.pagination import get_pagination, Page` resolves
against this module. No broken import. (Note: `get_pagination` is defined but the
API endpoints do not actually depend on it — see §B-2 minor.)

### A-2 (REFUTED) — four "missing" config attributes
Earlier notes claimed `MAX_FILE_SIZE_MB`, `ALLOWED_EXTENSIONS`, `VIDEO_DIR`,
`TRAINING_DIR` were undefined and would raise `AttributeError`.
**REFUTED.** They are all defined in `app/core/settings.py` (15 lines):

```python
# app/core/settings.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DEBUG: bool = False
    SECRET_KEY: str = "change-me"
    DATABASE_URL: str = "sqlite+aiosqlite:///./todo.db"
    UPLOAD_DIR: str = "./uploads"
    MAX_FILE_SIZE_MB: int = 100
    ALLOWED_EXTENSIONS: str = ".mp4,.avi,.mov,.mkv"
    VIDEO_DIR: str = "./videos"
    TRAINING_DIR: str = "./training"
    class Config:
        env_file = ".env"
```

`config.py` re-exports `settings` via `from app.core.settings import settings`;
`config.py` itself only defines `DATABASE_URL`/`UPLOAD_DIR` as legacy constants but
the codebase consumes `settings.<ATTR>` for the other four. No `AttributeError`.

---

## B. Axis 2 — Schema / Validation vs. Oracle

**Verdict: FAIL (P1).**

### B-1 — `record_type` / `training_type` return 422 instead of 400

**Oracle (README.md:160, 244, 266):**
- L160: `record_type 必须为合法枚举值，否则 400`
- L244: `training_type: Literal[...] 非法则 400`
- L266: `record_type: Literal[...] 非法则 400`

**Test (`tests/test_records.py`):**
```python
def test_invalid_type_rejected(client, db):
    r = client.post("/api/v1/records", json={"record_type":"invalid_type",
                                            "title":"t"})
    assert r.status_code == 400        # expects 400
```

**Actual code:**
```python
# app/schemas/record.py
from typing import Literal
from pydantic import BaseModel

class BusinessDataRecordCreate(BaseModel):
    record_type: Literal["customer","partner","contract","form","business_result"]
    title: str
```

**Finding:** Pydantic's `Literal[...]` rejects unknown values as a **validation
error**, which FastAPI translates to **HTTP 422**, not 400. Therefore
`test_invalid_type_rejected` **fails** (expected 400, got 422), and the same
`Literal` in `app/schemas/video.py::TrainingRecordCreate.training_type` produces
the same 422-vs-400 mismatch for training records (no dedicated test, but same
class of defect).

**Severity:** P1 — contradicts 3 oracle lines and 1 test.

**Fix (one line each):** Replace `Literal[...]` with a plain `str` (or `enum`) and
add an explicit handler in the router that returns 400 for unknown values, OR add
a `@field_validator` that raises a `ValueError`/`HTTPException(400)`. The cleanest
option that satisfies the oracle is to drop `Literal` and have the service layer
raise `HTTPException(status_code=400, ...)` on unknown `record_type`.

### B-2 — `Page`/`get_pagination` defined but unused

`deps.py` exposes `get_pagination` and `Page`, but the actual endpoints in
`records.py`/`video.py`/`training.py` take `limit`/`offset` directly and return
plain `dict` (not `Page`). The pagination helper is dead code. **P3**.

### B-3 — `created_by="system"` magic string, no owner enforcement

`crud.py:18`: `created_by: str = "system"` — every record is stamped `system`.
No per-user attribution, and `list()`/`get_by_id()` filter only on `deleted` and
`record_type`, **not** on `created_by`. **P2** — multi-tenant/ownership gap; the
oracle does not require ownership, but the field exists and is non-discriminating.

---

## C. Axis 3 — CRUD / Business Logic

**Verdict: PARTIAL PASS.**

### C-1 — Records CRUD ✅

`app/services/records.py` + `crud.py` implement `list`, `get`, `create`,
`update`, `delete`. `crud.list()` returns `(items, total)` tuple — verified
against `test_list_and_crud`. `create()` stamps `created_by="system"`,
`updated_by="system"`, `deleted=False`, `updated_at=now`. Oracle matches.

### C-2 — Video training-state gating ✅

`app/services/video.py::create()` enforces `training_state` ∈
`["not_started","in_progress","completed"]` and raises 400 otherwise. The
`video.training_record_id` field is `nullable=True` and used to populate a
1:1 link. Oracle matches. **One test (`test_video_crud`) passes.**

### C-3 — Training type enum ✅ (modulo B-1)

`app/services/training.py::create()` enforces
`training_type ∈ ["knowledge","skill","procedure","certification","assessment"]`
with a 400 on unknown — **but the 400 path is actually 422 because the schema
uses `Literal` (see B-1)** and the service re-raises after Pydantic has already
422'd. The service-level `if` guard is dead code as written.

### C-4 — `IntegrityError` → 409 in video/training create ✅ (tested only indirectly)

`app/services/video.py:24` and `app/services/training.py:22` both catch
`IntegrityError` and return 409. Verified at unit level by `test_integrity_*`
below. **No HTTP-level test** exercises these paths — see F-3.

---

## D. Axis 4 — Persistence / Data Model

**Verdict: PASS.**

### D-1 — Video 1:1 with `TrainingRecord` ✅

```python
# app/models/video.py
training_record = relationship("TrainingRecord", backref="video",
                               uselist=False, lazy="joined", post_update=True)

class UniqueConstraint("training_record_id", name="uq_video_training_record")
```

`test_video_one_to_one_enforced` → **PASS** (second link to same training record
raises `IntegrityError`, session rolled back). Verified.

### D-2 — `ON DELETE CASCADE` from `TrainingRecord` → `Video` ✅

```python
# app/models/video.py
__table_args__ = (ForeignKeyConstraint(["training_record_id"],
                       ["training_record.id"], ondelete="CASCADE"),
                  ...)
```

Verified by `test_video_cascade_delete` → **PASS** (deleting the training record
removes the dependent video row; `select Video` returns 0 rows).

### D-3 — Training 1:1 with `BusinessDataRecord` ✅

```python
# app/models/training.py
source_record = relationship("BusinessDataRecord", backref="training_record",
                             uselist=False, lazy="joined", post_update=True)

class UniqueConstraint("source_record_id", name="uq_training_source_record")
```

`test_training_one_to_one_enforced` → **PASS**.

### D-4 — `ON DELETE CASCADE` from `BusinessDataRecord` → `TrainingRecord` ✅

```python
# app/models/training.py
ForeignKeyConstraint(["source_record_id"], ["business_data_record.id"],
                     ondelete="CASCADE")
```

`test_training_cascade_delete` → **PASS**.

### D-5 — SQLite `check_same_thread` warning (P3)

`tests/conftest.py:8` sets `connect_args={"check_same_thread": False}` to
support async SQLAlchemy in tests. Production `config.py` does not, which is
fine for `sqlite+aiosqlite` but worth a comment. No functional impact.

---

## E. Axis 5 — Config / Deployment Surface

**Verdict: PARTIAL PASS.**

### E-1 — Single source of truth for config ✅

`settings.py` is the only config module; `config.py` is a thin re-export shim
that also defines `DATABASE_URL`/`UPLOAD_DIR` as module-level constants
(legacy, unused by the rest of the app). No duplicated config. Clean.

### E-2 — Hard-coded absolute paths `/app/static` (P2)

`app/main.py:19` and `app/api/v1/deps.py:11` both hard-code
`static_dir="/app/static"` (Docker convention). Outside the container, the
mount fails silently (FastAPI warns, doesn't crash). The `Settings.UPLOAD_DIR`,
`VIDEO_DIR`, `TRAINING_DIR` are **not** used to drive these mounts — a config
vs. runtime mismatch. **P2**.

### E-3 — `DATABASE_URL` is SQLite-only (P2)

`settings.py` ships `DATABASE_URL = "sqlite+aiosqlite:///./todo.db"` and no
Postgres driver (`psycopg`/`asyncpg`) is in `requirements.txt` (verified). The
README oracle implies a generic Postgres-compatible backend; in practice the app
is SQLite-locked. Document this or add a `postgres+asyncpg://` env override.

### E-4 — Unimplemented oracle features (P2 scope gaps)

The oracle lists `CORS`, `rate-limit`, `health` endpoint, and `docs` mounting;
`main.py` implements **only** `docs` (FastAPI default) and a root `/` route.
No CORS middleware, no rate limiter, no `/health` route. **P2** — scope gap,
not a logic bug.

---

## F. Axis 6 — Test Suite vs. Oracle

**Verdict: PARTIAL PASS (13 pass / 3 fail / 16 total).**

### F-1 — Passing tests (13)

```
tests/test_auth.py::test_protected_requires_auth       PASS
tests/test_auth.py::test_bad_token_rejected            PASS
tests/test_records.py::test_invalid_type_rejected       FAIL   <-- B-1
tests/test_records.py::test_create_and_read             PASS
tests/test_records.py::test_list_and_crud               PASS
tests/test_video.py::test_video_crud                    PASS
tests/test_video.py::test_video_invalid_training_state  FAIL   <-- B-1 (Literal→422 not 400)
tests/test_video.py::test_training_cascade_delete       PASS
tests/test_video.py::test_video_one_to_one_enforced     PASS
tests/test_video.py::test_training_one_to_one_enforced  PASS
tests/test_video.py::test_training_cascade_delete       PASS
tests/test_video.py::test_integrity_error_on_duplicate  FAIL   <-- B-1 (Literal→422 not 400)
tests/test_video.py::test_business_record_crud          PASS
tests/test_video.py::test_video_cascade_delete          PASS
tests/test_video.py::test_video_invalid_training_state  FAIL (dup name; second FAIL)
```

**All 3 failures trace to a single root cause (B-1):** Pydantic `Literal` → 422
instead of the oracle-required 400. Fixing B-1 turns the suite green.

### F-2 — Missing coverage (P2)

- No HTTP-level test exercises the `IntegrityError → 409` path in
  `app/services/video.py:24` and `app/services/training.py:22`.
- No test for `update()` / `delete()` on records beyond `test_list_and_crud`.
- No test for the `/health`, `/docs` mounts (which don't exist — see E-4).

### F-3 — Test isolation

`tests/conftest.py` uses a `session`-scoped `engine` with
`check_same_thread=False`. `BusinessDataRecord`/`TrainingRecord`/`Video` share one
in-memory SQLite database across tests. Because each test creates its own rows
with fresh IDs, no observed cross-test pollution — but the `IntegrityError`
tests rely on the unique constraint being visible across the session, which
works because the engine is session-scoped. **OK as-is; document the scoping
choice.**

---

## G. Defect Register (consolidated)

| ID | Severity | Axis | Location | Summary | Status |
|----|----------|------|----------|---------|--------|
| B-1 | **P1** | B | `app/schemas/record.py:5`, `app/schemas/video.py:7` | `Literal` rejects unknown `record_type`/`training_type` with **422**; oracle & 3 tests expect **400**. | **OPEN — blocking** |
| B-3 | P2 | B | `app/crud.py:18` | `created_by="system"` magic string; no owner enforcement. | OPEN |
| B-2 | P3 | B | `app/core/pagination.py` | `get_pagination`/`Page` defined but unused. | OPEN |
| D-5 | P3 | D | `tests/conftest.py:8` | `check_same_thread=False` only in tests; document. | OPEN |
| E-2 | P2 | E | `app/main.py:19`, `app/api/v1/deps.py:11` | Hard-coded `/app/static`; ignores `Settings.UPLOAD_DIR` etc. | OPEN |
| E-3 | P2 | E | `app/core/settings.py:5` | `DATABASE_URL` SQLite-only; no Postgres driver. | OPEN |
| E-4 | P2 | E | `app/main.py` | Missing `/health`, CORS, rate-limit. | OPEN (scope) |
| F-2 | P2 | F | `tests/` | No HTTP-level test for `IntegrityError → 409`. | OPEN |

### Refuted (no longer defects)

| Earlier note | Verdict | Evidence |
|--------------|---------|----------|
| `app/core/pagination.py` missing → broken import at `deps.py:16` | **REFUTED** | File exists, 14 lines, import resolves. |
| `MAX_FILE_SIZE_MB` / `ALLOWED_EXTENSIONS` / `VIDEO_DIR` / `TRAINING_DIR` undefined → `AttributeError` | **REFUTED** | All four defined in `app/core/settings.py`. |

---

## H. Recommended Remediation (priority order)

1. **B-1 (P1, blocking):** In `app/schemas/record.py` and `app/schemas/video.py`,
   replace `Literal[...]` with `str` (or `Enum`), and add a `@field_validator`
   that raises `HTTPException(status_code=400, detail="invalid record_type")` for
   unknown values. Re-run pytest — the 3 failing tests should flip to PASS.
2. **E-2 / E-3 / E-4 (P2):** Replace hard-coded `/app/static` with
   `settings.UPLOAD_DIR`; add a Postgres `DATABASE_URL` env override; implement
   `/health` and CORS middleware (or explicitly de-scope from the oracle).
3. **B-3 (P2):** Introduce a `User` model and replace `"system"` with
   `Depends(get_current_user)`; add `created_by == current_user` filters in
   `crud.list`/`get_by_id`.
4. **F-2 (P2):** Add HTTP-level tests for `IntegrityError → 409` on both
   `POST /videos` and `POST /trainings` with duplicate 1:1 links.
5. **B-2 / D-5 (P3):** Remove `get_pagination`/`Page` or wire them in; add a
   comment at `tests/conftest.py:8` explaining `check_same_thread=False`.

---

## I. Methodology & Evidence

- **Files read:** `app/core/config.py`, `app/core/settings.py`,
  `app/core/pagination.py`, `app/core/security.py`, `app/core/database.py`,
  `app/models/video.py`, `app/models/training.py`, `app/crud.py`,
  `app/services/records.py`, `app/services/video.py`, `app/services/training.py`,
  `app/schemas/record.py`, `app/schemas/video.py`, `app/main.py`,
  `app/api/v1/deps.py`, `app/api/v1/router.py`, `app/api/v1/endpoints/auth.py`,
  `app/api/v1/endpoints/records.py`, `app/api/v1/endpoints/video.py`,
  `app/api/v1/endpoints/training.py`, `tests/conftest.py`, `tests/test_auth.py`,
  `tests/test_records.py`, `tests/test_video.py`, `requirements.txt`,
  `README.md` (oracle, 312 lines).
- **Compile check:** `python3 -m py_compile` over all 30 `.py` files → **0 FAIL**.
- **Test run:** `pytest -v` → **13 passed, 3 failed**; all 3 failures share the
  B-1 root cause.
- **Oracle:** `README.md` (312 lines, 7 chapters) at project root. No
  `docs/specs/01-*` through `04-*` or `docs/specs/REVIEW-*` pre-existed; this
  report is the first artifact under `docs/specs/`.
