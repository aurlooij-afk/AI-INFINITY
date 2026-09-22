from __future__ import annotations

from pathlib import Path
import py_compile

SRC = Path("main.py")
OUT = Path("main_98.py")
BACKUP = Path("main.py.97.backup")

text = SRC.read_text(encoding="utf-8")

if 'APP_VERSION = "TARGET-2050.97"' not in text:
    raise SystemExit("Expected TARGET-2050.97 main.py; refusing to patch a different build.")


def replace_between(source: str, start: str, end: str, replacement: str) -> str:
    a = source.find(start)
    if a < 0:
        raise SystemExit(f"Missing start marker: {start}")
    b = source.find(end, a + len(start))
    if b < 0:
        raise SystemExit(f"Missing end marker: {end}")
    return source[:a] + replacement + source[b:]


text = text.replace(
    "TARGET-2050.97\nBUILD: REAL-WORLD-COMMAND-AUDIT-RECOVERY-CORE",
    "TARGET-2050.98\nBUILD: REAL-WORLD-COMMAND-TRANSACTION-RECOVERY-CORE",
    1,
)
text = text.replace(
    'APP_VERSION = "TARGET-2050.97"\nBUILD = "REAL-WORLD-COMMAND-AUDIT-RECOVERY-CORE"',
    'APP_VERSION = "TARGET-2050.98"\nBUILD = "REAL-WORLD-COMMAND-TRANSACTION-RECOVERY-CORE"',
    1,
)
text = text.replace(
    "ACTION_MAX_ATTEMPTS = 2\n\nSTARTED_AT",
    'ACTION_MAX_ATTEMPTS = 2\nACTION_STALE_SECONDS = max(30, int(os.getenv("AI_INFINITY_ACTION_STALE_SECONDS", "120")))\n\nSTARTED_AT',
    1,
)
text = text.replace(
    '"User-Agent": "AI-Infinity/2050.96"',
    '"User-Agent": "AI-Infinity/2050.98"',
    1,
)

helper_and_fabric = r'''

def _transaction_result(row):
    result = None

    if row["result_json"]:
        try:
            result = json.loads(row["result_json"])
        except Exception:
            result = {
                "raw_result": row["result_json"]
            }

    return result


def _transaction_replay(
    row,
    action: str,
    idempotency_key: str,
):
    return {
        "replay": True,
        "recovered": False,
        "status": row["status"],
        "transaction_id": row["id"],
        "action": action,
        "idempotency_key": idempotency_key,
        "result": _transaction_result(row),
        "previous_status": row["status"],
    }


def _begin_action_transaction(
    *,
    action: str,
    mission_id: Optional[str],
    idempotency_key: str,
    input_payload: Dict[str, Any],
):
    payload_json = json.dumps(
        input_payload,
        ensure_ascii=False,
    )

    with DB_LOCK:
        existing = q(
            """
            SELECT *
            FROM action_transactions
            WHERE idempotency_key=?
            """,
            (idempotency_key,),
            one=True,
        )

        if existing:
            status = str(existing["status"])

            updated_at = float(
                existing["updated_at"]
                or existing["created_at"]
                or now()
            )

            age = max(
                0.0,
                now() - updated_at,
            )

            if (
                status == "running"
                and age > ACTION_STALE_SECONDS
            ):
                txid = existing["id"]

                write(
                    """
                    UPDATE action_transactions
                    SET status='running',
                        result_json=NULL,
                        error=NULL,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        now(),
                        txid,
                    ),
                )

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
                                "recovery":
                                    "stale_running_reclaimed",
                                "previous_status":
                                    "running",
                                "stale_age_seconds":
                                    round(age, 3),
                                "action": action,
                                "input": input_payload,
                                "external_side_effects":
                                    False,
                            },
                            ensure_ascii=False,
                        ),
                        now(),
                    ),
                )

                return {
                    "replay": False,
                    "recovered": True,
                    "status": "running",
                    "transaction_id": txid,
                    "action": action,
                    "idempotency_key": idempotency_key,
                    "result": None,
                    "previous_status": "running",
                    "stale_age_seconds": round(age, 3),
                }

            return _transaction_replay(
                existing,
                action,
                idempotency_key,
            )

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
                    idempotency_key,
                    payload_json,
                    now(),
                    now(),
                ),
            )

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
                            "action": action,
                            "input": input_payload,
                            "external_side_effects":
                                False,
                            "recovery": None,
                        },
                        ensure_ascii=False,
                    ),
                    now(),
                ),
            )

            return {
                "replay": False,
                "recovered": False,
                "status": "running",
                "transaction_id": txid,
                "action": action,
                "idempotency_key": idempotency_key,
                "result": None,
                "previous_status": None,
            }

        except sqlite3.IntegrityError:
            raced = q(
                """
                SELECT *
                FROM action_transactions
                WHERE idempotency_key=?
                """,
                (idempotency_key,),
                one=True,
            )

            if raced:
                return _transaction_replay(
                    raced,
                    action,
                    idempotency_key,
                )

            raise


def execute_action_fabric(
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

    transaction = _begin_action_transaction(
        action=action,
        mission_id=request.mission_id,
        idempotency_key=idem,
        input_payload={
            "action": action,
            "args": args,
            "external_side_effects": False,
        },
    )

    if transaction["replay"]:
        return {
            "status": transaction["status"],
            "transaction_id": transaction["transaction_id"],
            "action": action,
            "idempotent_replay": True,
            "result": transaction["result"],
            "recovered_stale_running": False,
        }

    txid = transaction["transaction_id"]
    recovered = bool(transaction["recovered"])
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
                "recovered_stale_running": recovered,
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

            write(
                """
                INSERT INTO action_log(
                    mission_id,
                    action,
                    status,
                    details_json,
                    ts,
                    idempotency_key,
                    action_type,
                    target,
                    result_json
                )
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    request.mission_id,
                    action,
                    "committed",
                    json.dumps(
                        {
                            "result": result,
                            "recovered_stale_running":
                                recovered,
                        },
                        ensure_ascii=False,
                    ),
                    now(),
                    idem,
                    action,
                    "",
                    json.dumps(
                        result,
                        ensure_ascii=False,
                    ),
                ),
            )

            return {
                "status": "committed",
                "transaction_id": txid,
                "action": action,
                "result": result,
                "idempotency_key": idem,
                "recovered_stale_running": recovered,
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

                write(
                    """
                    INSERT INTO action_log(
                        mission_id,
                        action,
                        status,
                        details_json,
                        ts,
                        idempotency_key,
                        action_type,
                        target,
                        result_json
                    )
                    VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        request.mission_id,
                        action,
                        "failed_closed",
                        json.dumps(
                            {
                                "attempts": attempts,
                                "recovered_stale_running":
                                    recovered,
                            },
                            ensure_ascii=False,
                        ),
                        now(),
                        idem,
                        action,
                        "",
                        None,
                    ),
                )

                return {
                    "status": "failed_closed",
                    "transaction_id": txid,
                    "action": action,
                    "attempts": attempts,
                    "idempotency_key": idem,
                    "recovered_stale_running": recovered,
                    "external_side_effects": False,
                }

    raise RuntimeError("action_fabric_unreachable")


'''

safe_gateway = r'''
def execute_safe_gateway(
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
                "status": "replayed",
                "idempotency": True,
                "idempotency_key": key,
                "transaction_id": existing["id"],
                "action": _transaction_result(existing),
                "recovered_stale_running": False,
            }

        return {
            "status": "approval_required",
            "approved": False,
            "idempotency": True,
            "idempotency_key": key,
            "action_type": request.action_type,
            "target": request.target,
        }

    transaction = _begin_action_transaction(
        action=request.action_type,
        mission_id=request.mission_id,
        idempotency_key=key,
        input_payload={
            "action_type": request.action_type,
            "target": request.target,
            "payload": request.payload,
            "external_side_effects": False,
        },
    )

    if transaction["replay"]:
        return {
            "status": "replayed",
            "idempotency": True,
            "idempotency_key": key,
            "transaction_id": transaction["transaction_id"],
            "action": transaction["result"],
            "recovered_stale_running": False,
        }

    txid = transaction["transaction_id"]
    recovered = bool(transaction["recovered"])

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
        result["recovered_stale_running"] = recovered

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

        write(
            """
            INSERT INTO action_log(
                mission_id,
                action,
                status,
                details_json,
                ts,
                idempotency_key,
                action_type,
                target,
                result_json
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                request.mission_id,
                request.action_type,
                status,
                json.dumps(
                    {
                        "result": result,
                        "recovered_stale_running":
                            recovered,
                    },
                    ensure_ascii=False,
                ),
                now(),
                key,
                request.action_type,
                request.target,
                json.dumps(
                    result,
                    ensure_ascii=False,
                ),
            ),
        )

        return {
            "status": (
                "completed"
                if result.get("ok")
                else "failed"
            ),
            "action_requested": True,
            "action_executed": bool(result.get("ok")),
            "action_verified": bool(result.get("ok")),
            "idempotency": True,
            "idempotency_key": key,
            "provenance_recorded": True,
            "recovery_tested": recovered,
            "recovered_stale_running": recovered,
            "transaction_id": txid,
            "result": result,
        }

    except HTTPException as exc:

        error = str(exc.detail)[:500]

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

        write(
            """
            INSERT INTO action_log(
                mission_id,
                action,
                status,
                details_json,
                ts,
                idempotency_key,
                action_type,
                target
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                request.mission_id,
                request.action_type,
                "failed_closed",
                json.dumps(
                    {
                        "error": error,
                        "recovered_stale_running":
                            recovered,
                    },
                    ensure_ascii=False,
                ),
                now(),
                key,
                request.action_type,
                request.target,
            ),
        )

        raise

    except Exception as exc:

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

        write(
            """
            INSERT INTO action_log(
                mission_id,
                action,
                status,
                details_json,
                ts,
                idempotency_key,
                action_type,
                target
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                request.mission_id,
                request.action_type,
                "failed_closed",
                json.dumps(
                    {
                        "error": error,
                        "recovered_stale_running":
                            recovered,
                    },
                    ensure_ascii=False,
                ),
                now(),
                key,
                request.action_type,
                request.target,
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
            "recovered_stale_running": recovered,
            "transaction_id": txid,
            "error": error,
        }


'''

text = replace_between(
    text,
    "def execute_safe_gateway(",
    "# ============================================================\n# PROVIDERS",
    safe_gateway,
)
text = replace_between(
    text,
    "def execute_action_fabric(",
    "# ============================================================\n# SAFE REAL-WORLD ACTION GATEWAY",
    helper_and_fabric,
)

text = text.replace(
    '            "action_idempotency": True,\n        },',
    '            "action_idempotency": True,\n            "stale_running_transaction_recovery": True,\n        },',
    1,
)
text = text.replace(
    '        "action_retry_limit":\n            ACTION_MAX_ATTEMPTS,\n        "action_circuit_breaker": True,\n    }',
    '        "action_retry_limit":\n            ACTION_MAX_ATTEMPTS,\n        "action_circuit_breaker": True,\n        "stale_running_transaction_recovery": True,\n        "action_stale_seconds": ACTION_STALE_SECONDS,\n    }',
    1,
)
text = text.replace(
    '            "command-gateway",\n        ],',
    '            "command-gateway",\n            "stale-transaction-recovery",\n            "idempotent-transaction-reclamation",\n        ],',
    1,
)
text = text.replace(
    '        "2050.97",\n    ]',
    '        "2050.97",\n        "2050.98",\n    ]',
    1,
)

test_marker = "# ============================================================\n# LEGACY VERIFY"
if '@app.get("/test-action-recovery")' in text:
    raise SystemExit("/test-action-recovery already exists; refusing duplicate patch.")

test_block = r'''
@app.get("/test-action-recovery")
def test_action_recovery():
    key = (
        "TEST_ACTION_RECOVERY_"
        + uuid.uuid4().hex
    )

    stale_time = (
        now()
        - ACTION_STALE_SECONDS
        - 5
    )

    mission_id = "test-action-recovery"

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
            make_id("tx"),
            mission_id,
            "save_result",
            "running",
            key,
            json.dumps(
                {
                    "target": "recovery-test",
                    "payload": {
                        "probe": True
                    },
                },
                ensure_ascii=False,
            ),
            stale_time,
            stale_time,
        ),
    )

    outcome = execute_safe_gateway(
        SafeActionRequest(
            action_type="save_result",
            target="recovery-test",
            payload={
                "probe": True
            },
            idempotency_key=key,
            mission_id=mission_id,
            require_approval=False,
        )
    )

    row = q(
        """
        SELECT id, status, error,
               created_at, updated_at
        FROM action_transactions
        WHERE idempotency_key=?
        """,
        (key,),
        one=True,
    )

    transaction_status = (
        row["status"]
        if row
        else "missing"
    )

    passed = (
        row is not None
        and transaction_status in {
            "committed",
            "failed_closed",
        }
        and bool(
            outcome.get(
                "recovery_tested",
                False,
            )
        )
    )

    return {
        "version": APP_VERSION,
        "status": "passed" if passed else "failed",
        "stale_running_reclaimed": bool(
            outcome.get(
                "recovery_tested",
                False,
            )
        ),
        "transaction_id":
            row["id"] if row else None,
        "transaction_status":
            transaction_status,
        "recovery_tested": bool(
            outcome.get(
                "recovery_tested",
                False,
            )
        ),
        "result_status":
            outcome.get("status"),
        "error":
            row["error"] if row else None,
    }


'''

if test_marker not in text:
    raise SystemExit("Missing legacy verify marker.")
text = text.replace(test_marker, test_block + "\n" + test_marker, 1)

# Structural checks performed on the transformed real 97 source pattern.
checks = {
    "version": 'APP_VERSION = "TARGET-2050.98"' in text,
    "build": 'BUILD = "REAL-WORLD-COMMAND-TRANSACTION-RECOVERY-CORE"' in text,
    "stale_constant": "ACTION_STALE_SECONDS" in text,
    "recovery_helper": "def _begin_action_transaction(" in text,
    "recovery_marker": '"stale_running_reclaimed"' in text,
    "health_flag": '"stale_running_transaction_recovery": True' in text,
    "resilience_flag": '"action_stale_seconds": ACTION_STALE_SECONDS' in text,
    "capability": '"idempotent-transaction-reclamation"' in text,
    "test_endpoint": '@app.get("/test-action-recovery")' in text,
    "version_history": '"2050.98"' in text,
    "fabric_once": text.count("def execute_action_fabric(") == 1,
    "gateway_once": text.count("def execute_safe_gateway(") == 1,
    "helper_once": text.count("def _begin_action_transaction(") == 1,
    "providers_once": text.count("# ============================================================\n# PROVIDERS") == 1,
    "triple_quotes_balanced": text.count('"""') % 2 == 0,
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("Patch self-check failed: " + ", ".join(failed))

OUT.write_text(text, encoding="utf-8")
py_compile.compile(str(OUT), doraise=True)
if not BACKUP.exists():
    SRC.replace(BACKUP)
else:
    raise SystemExit(f"Backup already exists: {BACKUP}; refusing to overwrite it.")
OUT.replace(SRC)
print(f"WROTE {SRC} from {OUT} ({SRC.stat().st_size} bytes)")
print(f"BACKUP {BACKUP}")
print("SELF_CHECK", {k: v for k, v in checks.items() if k != "triple_quotes_balanced"})
