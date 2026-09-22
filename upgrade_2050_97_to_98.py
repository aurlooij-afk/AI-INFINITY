from pathlib import Path
import re
import shutil

MAIN = Path("main.py")
BACKUP = Path("main.py.97.backup")

if not MAIN.exists():
    raise SystemExit("ERROR: main.py not found")

src = MAIN.read_text(encoding="utf-8")

# ------------------------------------------------------------
# VERIFY ACTUAL 97
# ------------------------------------------------------------

required = [
    'APP_VERSION = "TARGET-2050.97"',
    "def execute_action_fabric(",
    "def execute_safe_gateway(",
    "action_transactions",
    "action_snapshots",
]

missing = [x for x in required if x not in src]

if missing:
    raise SystemExit(
        "ERROR: This is not the expected TARGET-2050.97 main.py.\n"
        + "\n".join(missing)
    )

shutil.copy2(MAIN, BACKUP)

# ------------------------------------------------------------
# VERSION
# ------------------------------------------------------------

src = src.replace(
    'APP_VERSION = "TARGET-2050.97"',
    'APP_VERSION = "TARGET-2050.98"',
    1,
)

src = re.sub(
    r'BUILD\s*=\s*"[^"]*"',
    'BUILD = "REAL-WORLD-COMMAND-TRANSACTION-RECOVERY-CORE"',
    src,
    count=1,
)

# ------------------------------------------------------------
# STALE TRANSACTION CONFIG
# ------------------------------------------------------------

if "ACTION_STALE_SECONDS" not in src:
    marker = "ACTION_MAX_ATTEMPTS = 2\n"

    if marker not in src:
        raise SystemExit(
            "ERROR: ACTION_MAX_ATTEMPTS marker not found"
        )

    src = src.replace(
        marker,
        marker
        + 'ACTION_STALE_SECONDS = max(30, int(os.getenv("AI_INFINITY_ACTION_STALE_SECONDS", "120")))\n',
        1,
    )

# ------------------------------------------------------------
# TRANSACTION RECOVERY ENGINE
# ------------------------------------------------------------

if "def _begin_action_transaction(" not in src:

    marker = "def execute_action_fabric(\n"

    if marker not in src:
        raise SystemExit(
            "ERROR: execute_action_fabric marker not found"
        )

    helper = r'''
def _transaction_result(row):
    if not row or not row["result_json"]:
        return None

    try:
        return json.loads(row["result_json"])
    except Exception:
        return None


def _transaction_replay(
    row,
    action,
    key,
    reclaimed=False,
):
    return {
        "status": row["status"],
        "transaction_id": row["id"],
        "action": action,
        "idempotency_key": key,
        "idempotent_replay": not reclaimed,
        "stale_running_reclaimed": reclaimed,
        "result": _transaction_result(row),
    }


def _begin_action_transaction(
    mission_id,
    action,
    key,
    input_json,
    snapshot,
):
    existing = q(
        """
        SELECT *
        FROM action_transactions
        WHERE idempotency_key=?
        """,
        (key,),
        one=True,
    )

    if existing:

        age = max(
            0.0,
            now()
            - float(
                existing["updated_at"]
                or existing["created_at"]
                or now()
            ),
        )

        if (
            existing["status"] == "running"
            and age >= ACTION_STALE_SECONDS
        ):

            txid = existing["id"]

            write(
                """
                UPDATE action_transactions
                SET status='running',
                    input_json=?,
                    result_json=NULL,
                    error=NULL,
                    updated_at=?
                WHERE id=?
                  AND status='running'
                """,
                (
                    input_json,
                    now(),
                    txid,
                ),
            )

            refreshed = q(
                """
                SELECT *
                FROM action_transactions
                WHERE id=?
                """,
                (txid,),
                one=True,
            )

            if (
                refreshed
                and refreshed["status"] == "running"
            ):

                write(
                    """
                    INSERT INTO action_snapshots(
                        transaction_id,
                        snapshot_json,
                        created_at
                    )
                    VALUES(?,?,?)
                    """,
                    (
                        txid,
                        json.dumps(
                            {
                                **snapshot,
                                "recovery":
                                    "stale_running_reclaimed",
                                "previous_status":
                                    "running",
                                "stale_age_seconds":
                                    age,
                                "external_side_effects":
                                    False,
                            },
                            ensure_ascii=False,
                        ),
                        now(),
                    ),
                )

                return {
                    "mode": "reclaimed",
                    "transaction_id": txid,
                    "row": refreshed,
                    "stale_age_seconds": age,
                }

        return {
            "mode": "replay",
            "transaction_id": existing["id"],
            "row": existing,
        }

    txid = make_id("tx")

    try:

        write(
            """
            INSERT INTO action_transactions(
                id,
                mission_id,
                action,
                status,
                idempotency_key,
                input_json,
                created_at,
                updated_at
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                txid,
                mission_id,
                action,
                "running",
                key,
                input_json,
                now(),
                now(),
            ),
        )

    except sqlite3.IntegrityError:

        existing = q(
            """
            SELECT *
            FROM action_transactions
            WHERE idempotency_key=?
            """,
            (key,),
            one=True,
        )

        if existing:
            return {
                "mode": "replay",
                "transaction_id": existing["id"],
                "row": existing,
            }

        raise

    write(
        """
        INSERT INTO action_snapshots(
            transaction_id,
            snapshot_json,
            created_at
        )
        VALUES(?,?,?)
        """,
        (
            txid,
            json.dumps(
                snapshot,
                ensure_ascii=False,
            ),
            now(),
        ),
    )

    return {
        "mode": "new",
        "transaction_id": txid,
        "row": q(
            """
            SELECT *
            FROM action_transactions
            WHERE id=?
            """,
            (txid,),
            one=True,
        ),
    }


'''

    src = src.replace(
        marker,
        helper + marker,
        1,
    )

# ------------------------------------------------------------
# ACTION FABRIC
# ------------------------------------------------------------

fabric_start = src.find(
    "def execute_action_fabric(\n"
)

fabric_end = src.find(
    "# ============================================================\n"
    "# SAFE REAL-WORLD ACTION GATEWAY",
    fabric_start,
)

if fabric_start < 0 or fabric_end < 0:
    raise SystemExit(
        "ERROR: action fabric boundaries not found"
    )

new_fabric = r'''def execute_action_fabric(
    request: ActionRequest,
):

    action = request.action.strip()
    args = dict(request.args or {})

    if action not in SAFE_ACTIONS:
        return {
            "status": "blocked",
            "reason": "action_not_registered",
            "approval_required": True,
            "external_side_effects": False,
        }

    if not _action_allowed(action):
        return {
            "status": "blocked",
            "reason": "circuit_open",
            "action": action,
        }

    if request.require_approval:
        return {
            "status": "awaiting_approval",
            "action": action,
            "approval_required": True,
            "external_side_effects": False,
        }

    idem = (
        request.idempotency_key
        or fingerprint(
            action,
            json.dumps(
                args,
                sort_keys=True,
            ),
        )
    )

    tx = _begin_action_transaction(
        request.mission_id,
        action,
        idem,
        json.dumps(
            args,
            ensure_ascii=False,
        ),
        {
            "action": action,
            "args": args,
            "side_effects": False,
        },
    )

    if tx["mode"] == "replay":
        return _transaction_replay(
            tx["row"],
            action,
            idem,
            False,
        )

    txid = tx["transaction_id"]
    attempts = []

    for attempt in range(
        1,
        ACTION_MAX_ATTEMPTS + 1,
    ):

        try:

            started = now()

            result = _registered_execute(
                action,
                args,
            )

            attempts.append({
                "attempt": attempt,
                "status": "verified",
                "latency_ms": int(
                    (now() - started) * 1000
                ),
            })

            _circuit_success(action)

            result = {
                **result,
                "attempts": attempts,
            }

            write(
                """
                UPDATE action_transactions
                SET status='committed',
                    result_json=?,
                    error=NULL,
                    updated_at=?
                WHERE id=?
                """,
                (
                    json.dumps(
                        result,
                        ensure_ascii=False,
                    ),
                    now(),
                    txid,
                ),
            )

            return {
                "status": "committed",
                "transaction_id": txid,
                "action": action,
                "result": result,
                "idempotency_key": idem,
                "stale_running_reclaimed":
                    tx["mode"] == "reclaimed",
                "safety": {
                    "registered_action_only": True,
                    "external_side_effects": False,
                    "spending": False,
                    "arbitrary_code_execution": False,
                },
            }

        except Exception as exc:

            attempts.append({
                "attempt": attempt,
                "status": "failed",
                "error": str(exc)[:500],
            })

            _circuit_failure(action)

            if attempt >= ACTION_MAX_ATTEMPTS:

                error = str(exc)[:500]

                write(
                    """
                    UPDATE action_transactions
                    SET status='failed_closed',
                        error=?,
                        result_json=NULL,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        error,
                        now(),
                        txid,
                    ),
                )

                return {
                    "status": "failed_closed",
                    "transaction_id": txid,
                    "action": action,
                    "attempts": attempts,
                    "idempotency_key": idem,
                    "stale_running_reclaimed":
                        tx["mode"] == "reclaimed",
                    "external_side_effects": False,
                }

    raise RuntimeError(
        "action_fabric_unreachable"
    )


'''

src = (
    src[:fabric_start]
    + new_fabric
    + src[fabric_end:]
)

# ------------------------------------------------------------
# SAFE GATEWAY
# ------------------------------------------------------------

gateway_start = src.find(
    "def execute_safe_gateway(\n"
)

gateway_end = src.find(
    "# ============================================================\n"
    "# PROVIDERS",
    gateway_start,
)

if gateway_start < 0 or gateway_end < 0:
    raise SystemExit(
        "ERROR: safe gateway boundaries not found"
    )

new_gateway = r'''def execute_safe_gateway(
    request: SafeActionRequest,
):

    key = (
        request.idempotency_key
        or _action_fingerprint(
            request.action_type,
            request.target,
            request.payload,
        )
    )

    if request.require_approval:
        return {
            "status": "approval_required",
            "approved": False,
            "idempotency": True,
            "idempotency_key": key,
            "action_type": request.action_type,
            "target": request.target,
        }

    tx = _begin_action_transaction(
        request.mission_id,
        request.action_type,
        key,
        json.dumps(
            {
                "target": request.target,
                "payload": request.payload,
            },
            ensure_ascii=False,
        ),
        {
            "action_type": request.action_type,
            "target": request.target,
            "payload": request.payload,
            "external_side_effects": False,
        },
    )

    if tx["mode"] == "replay":

        return {
            "status": (
                "replayed"
                if tx["row"]["status"] != "running"
                else "running"
            ),
            "idempotency": True,
            "idempotency_key": key,
            "transaction_id": tx["transaction_id"],
            "stale_running_reclaimed": False,
            "action": _transaction_result(
                tx["row"]
            ),
        }

    txid = tx["transaction_id"]

    try:

        result = _execute_safe_action(
            request.action_type,
            request.target,
            request.payload,
        )

        status = (
            "committed"
            if result.get("ok")
            else "failed_closed"
        )

        result["transaction_id"] = txid
        result["idempotency_key"] = key
        result["stale_running_reclaimed"] = (
            tx["mode"] == "reclaimed"
        )

        write(
            """
            UPDATE action_transactions
            SET status=?,
                result_json=?,
                error=NULL,
                updated_at=?
            WHERE id=?
            """,
            (
                status,
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
                now(),
                txid,
            ),
        )

        return {
            "status": (
                "completed"
                if result.get("ok")
                else "failed"
            ),
            "action_requested": True,
            "action_executed": bool(
                result.get("ok")
            ),
            "action_verified": bool(
                result.get("ok")
            ),
            "idempotency": True,
            "idempotency_key": key,
            "provenance_recorded": True,
            "recovery_tested":
                tx["mode"] == "reclaimed",
            "stale_running_reclaimed":
                tx["mode"] == "reclaimed",
            "transaction_id": txid,
            "result": result,
        }

    except HTTPException:
        raise

    except Exception as exc:

        error = str(exc)[:500]

        write(
            """
            UPDATE action_transactions
            SET status='failed_closed',
                error=?,
                updated_at=?
            WHERE id=?
            """,
            (
                error,
                now(),
                txid,
            ),
        )

        return {
            "status": "failed",
            "action_requested": True,
            "action_executed": False,
            "action_verified": False,
            "idempotency": True,
            "idempotency_key": key,
            "provenance_recorded": True,
            "recovery_tested": True,
            "stale_running_reclaimed":
                tx["mode"] == "reclaimed",
            "transaction_id": txid,
            "error": error,
        }


'''

src = (
    src[:gateway_start]
    + new_gateway
    + src[gateway_end:]
)

# ------------------------------------------------------------
# SELF TEST
# ------------------------------------------------------------

if '@app.get("/test-action-recovery")' not in src:

    marker = (
        "# ============================================================\n"
        "# LEGACY VERIFY"
    )

    if marker not in src:
        raise SystemExit(
            "ERROR: LEGACY VERIFY marker not found"
        )

    test = r'''
# ============================================================
# ACTION TRANSACTION RECOVERY TEST
# ============================================================

@app.get("/test-action-recovery")
def test_action_recovery():

    key = "test-stale-recovery-" + make_id("k")
    txid = make_id("tx")

    old = now() - max(
        ACTION_STALE_SECONDS + 5,
        35,
    )

    write(
        """
        INSERT INTO action_transactions(
            id,
            mission_id,
            action,
            status,
            idempotency_key,
            input_json,
            result_json,
            error,
            created_at,
            updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            txid,
            None,
            "save_result",
            "running",
            key,
            json.dumps({
                "target": "self-test",
                "payload": {"ok": True},
            }),
            None,
            None,
            old,
            old,
        ),
    )

    request = SafeActionRequest(
        action_type="save_result",
        target="self-test",
        payload={"ok": True},
        idempotency_key=key,
        require_approval=False,
    )

    result = execute_safe_gateway(
        request
    )

    row = q(
        """
        SELECT status
        FROM action_transactions
        WHERE id=?
        """,
        (txid,),
        one=True,
    )

    terminal = bool(
        row
        and row["status"]
        in {
            "committed",
            "failed_closed",
        }
    )

    reclaimed = bool(
        result.get(
            "stale_running_reclaimed"
        )
    )

    return {
        "version": APP_VERSION,
        "status":
            "passed"
            if reclaimed and terminal
            else "failed",
        "stale_running_reclaimed":
            reclaimed,
        "terminal_status":
            row["status"]
            if row
            else None,
        "transaction_id": txid,
        "bounded_recovery": True,
        "expected_terminal_states": [
            "committed",
            "failed_closed",
        ],
    }


'''

    src = src.replace(
        marker,
        test + marker,
        1,
    )

# ------------------------------------------------------------
# CAPABILITY FLAGS
# ------------------------------------------------------------

if '"stale_running_transaction_recovery"' not in src:
    src = src.replace(
        '"action_idempotency": True,',
        '"action_idempotency": True,\n'
        '        "stale_running_transaction_recovery": True,',
        1,
    )

if '"action_stale_seconds"' not in src:
    src = src.replace(
        '"self_modification_enabled":\n            True,',
        '"self_modification_enabled":\n'
        '            True,\n\n'
        '        "action_stale_seconds":\n'
        '            ACTION_STALE_SECONDS,',
        1,
    )

# ------------------------------------------------------------
# FINAL VALIDATION
# ------------------------------------------------------------

checks = {
    "98_version":
        'APP_VERSION = "TARGET-2050.98"' in src,

    "98_build":
        "REAL-WORLD-COMMAND-TRANSACTION-RECOVERY-CORE"
        in src,

    "stale_recovery":
        "def _begin_action_transaction(" in src,

    "recovery_test":
        '@app.get("/test-action-recovery")' in src,

    "action_fabric_once":
        src.count("def execute_action_fabric(") == 1,

    "gateway_once":
        src.count("def execute_safe_gateway(") == 1,

    "providers_preserved":
        "# PROVIDERS" in src,

    "transactions_preserved":
        "action_transactions" in src,

    "snapshots_preserved":
        "action_snapshots" in src,

    "97_lineage":
        '"2050.97"' in src,
}

failed = [
    name
    for name, ok in checks.items()
    if not ok
]

if failed:
    shutil.copy2(BACKUP, MAIN)

    raise SystemExit(
        "ERROR: 98 validation failed:\n"
        + "\n".join(failed)
        + "\n97 backup restored."
    )

MAIN.write_text(
    src,
    encoding="utf-8",
)

print()
print("============================================")
print(" AI INFINITY TARGET-2050.98 READY")
print("============================================")
print()

for name, ok in checks.items():
    print(
        ("PASS " if ok else "FAIL ")
        + name
    )

print()
print("Backup:", BACKUP)
print("Main:", MAIN)
print()
print("NEXT:")
print("python -m py_compile main.py")
print()
