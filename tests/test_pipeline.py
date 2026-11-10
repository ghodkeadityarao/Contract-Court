import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import make_snapshot  # noqa: E402
from engine import Change, Column, Contract, check_change, negotiate, profiles  # noqa: E402


@pytest.fixture(scope="module")
def snap():
    return make_snapshot.build()


def contract(snap, table):
    return Contract(table, 1, {n: Column(n, t) for n, t in snap["tables"][table]})


def run(snap, table, change, overrides=None, producer=None):
    from engine import Producer
    rows = profiles.affected(snap, table, change)
    names = {r["consumer"] for r in rows}
    cols = snap["tables"][table]
    no_repl = frozenset([change.column]) if change.kind == "drop_column" and not profiles.has_replacement(cols, change.column) else frozenset()
    cons = [profiles.build_consumer(c, (overrides or {}).get(c["name"]), no_repl) for c in snap["consumers"] if c["name"] in names]
    others = [c["name"] for c in snap["consumers"] if c["name"] not in names]
    return negotiate(contract(snap, table), change, cons, others, producer or Producer("Owner", 30, 90, 60)), names


def test_workload_is_built_from_schema_and_has_chains(snap):
    cols = {c for cs in snap["tables"].values() for c, _ in cs}
    assert len(snap["consumers"]) >= 20
    assert any(c["level"] == 2 for c in snap["consumers"]) and any(c["depth"] > 0 for c in snap["consumers"])
    assert {c["kind"] for c in snap["consumers"]} == {"dashboard", "ml_model", "report", "stream_job"}
    assert cols  # tables came from introspection, not code


def test_unread_column_has_no_impact(snap):
    assert ["users", "email"] in snap["unread"]
    res, names = run(snap, "users", Change("drop_column", "email", 5))
    assert names == set() and res.status == "agreed" and res.agreement.signed_by == []


def test_drop_breaks_exactly_the_readers(snap):
    rows = [r for r in snap["impact"] if (r["table"], r["column"], r["kind"], r["mode"]) == ("bookings", "total_amount", "drop", "no_contract")]
    broke = {r["consumer"] for r in rows if r["outcome"] == "error"}
    direct = {c["name"] for c in snap["consumers"] if "total_amount" in c["sql"] and "bookings" in c["base_tables"]}
    assert direct <= broke
    assert "join_payments_bookings" not in broke  # joins bookings but never reads total_amount


def test_agreed_plan_is_verified_on_real_consumer_sql(snap):
    wc = [r for r in snap["impact"] if r["mode"] == "with_contract"]
    assert wc and all(r["outcome"] == "ok" for r in wc)


def test_meaning_change_is_silent_not_a_crash(snap):
    silent = [r for r in snap["impact"] if r["kind"] == "meaning" and r["mode"] == "no_contract" and r["outcome"] == "silent"]
    assert silent and not [r for r in snap["impact"] if r["kind"] == "meaning" and r["outcome"] == "error"]


def test_rename_any_column_negotiates_to_agreement(snap):
    res, names = run(snap, "bookings", Change("rename_column", "total_amount", 3, new_name="booking_value"))
    assert names and res.status == "agreed" and set(res.agreement.signed_by) == names
    assert len(res.rounds) >= 2 and res.agreement.new_contract.version == 2


def test_meaning_change_needs_a_compat_view(snap):
    res, names = run(snap, "bookings", Change("change_meaning", "total_amount", 5, factor=100))
    assert names and res.status == "agreed" and res.agreement.compat_view


def test_drop_without_replacement_escalates(snap):
    res, names = run(snap, "bookings", Change("drop_column", "status", 7))
    assert names and res.status == "escalated" and any("no replacement" in i for i in res.open_issues)


def test_downstream_chains_raise_a_teams_needs(snap):
    deep = next(c for c in snap["consumers"] if c["depth"] > 0 and c["kind"] == "dashboard")
    flat = next(c for c in snap["consumers"] if c["depth"] == 0 and c["kind"] == "dashboard")
    assert profiles.defaults(deep)["notice_days"] > profiles.defaults(flat)["notice_days"]


def test_editing_a_teams_constraints_changes_the_deal(snap):
    ch = Change("rename_column", "total_amount", 3, new_name="booking_value")
    base, names = run(snap, "bookings", ch)
    victim = sorted(names)[0]
    edited, _ = run(snap, "bookings", ch, overrides={victim: {"freeze": (0, 40)}})
    assert edited.agreement.effective_day > base.agreement.effective_day


def test_rogue_push_is_blocked_then_allowed_after_window(snap):
    res, _ = run(snap, "bookings", Change("rename_column", "total_amount", 3, new_name="booking_value"))
    v2 = res.agreement.new_contract
    assert not check_change(v2, Change("drop_column", "total_amount", 5), today=5)[0]
    assert check_change(v2, Change("drop_column", "total_amount", 200), today=200)[0]


def test_canary_detects_a_real_break():
    import standin
    from engine import measure, workload
    from engine.backend import DuckBackend
    be = DuckBackend(standin.make())
    names = [r[0] for r in be.con.execute("SHOW TABLES").fetchall()]
    tables = {n[:-6]: be.columns(n) for n in sorted(names) if n.endswith("__orig")}
    specs = workload.generate(tables)
    measure.build_sandbox(be, tables, specs)
    consumer, table, column, broke = measure.canary(be, tables, specs)
    assert broke, f"dropping {table}.{column} did not break {consumer}"


# ------------------------------------------------------------------ LLM layer (scripted fake model)
import json  # noqa: E402

from engine import Producer, serialize  # noqa: E402
from engine.llm import LLM  # noqa: E402


def llm_run(snap, ask, **kw):
    ch = Change("rename_column", "total_amount", 3, new_name="booking_value")
    rows = profiles.affected(snap, "bookings", ch)
    names = {r["consumer"] for r in rows}
    cons = [profiles.build_consumer(c) for c in snap["consumers"] if c["name"] in names]
    others = [c["name"] for c in snap["consumers"] if c["name"] not in names]
    llm = LLM(ask)
    res = negotiate(contract(snap, "bookings"), ch, cons, others, Producer("Owner", 30, 90, 60),
                    llm=llm, facts={r["consumer"]: r["detail"] for r in rows}, **kw)
    return res, llm


def test_llm_text_is_used_but_numbers_are_kept(snap):
    def ask(prompt):
        if "write lines" in prompt:
            items = json.loads(prompt.split("Participants: ")[1])
            # first participant keeps numbers, second one drops them (must be rejected)
            return json.dumps({items[0]["name"]: "LLM VOICE " + items[0]["draft"], items[1]["name"]: "no numbers here"})
        return "not json"
    res, llm = llm_run(snap, ask)
    texts = [m.text for m in res.messages if m.role == "consumer"]
    assert any(t.startswith("LLM VOICE") for t in texts)
    assert "no numbers here" not in texts and res.status == "agreed"  # referee and fallback still work


def test_valid_llm_offer_is_used_and_invalid_one_is_rejected(snap):
    good = {"effective_day": 25, "overlap_days": 21, "compat_view": False, "snapshot": False, "message": "I offer day 25 and 21 days."}
    bad = {"effective_day": 999, "overlap_days": 5, "compat_view": False, "snapshot": False, "message": "x"}

    def make(offer):
        return lambda prompt: json.dumps(offer) if "negotiating" in prompt else "{}"
    res, _ = llm_run(snap, make(good))
    assert any(m.text == "I offer day 25 and 21 days." for m in res.messages)
    res2, _ = llm_run(snap, make(bad))  # beyond the deadline: ignored, rule ladder used instead
    assert res2.status == "agreed" and not any(m.text == "x" for m in res2.messages)


def test_model_failure_falls_back_to_rules(snap):
    def boom(prompt):
        raise RuntimeError("endpoint down")
    res, llm = llm_run(snap, boom)
    assert res.status == "agreed" and llm.log and all(r.startswith("ERROR") for _, _, r in llm.log)


def test_results_survive_json_round_trip(snap):
    res, _ = llm_run(snap, lambda p: "{}")
    back = serialize.result_from_dict(json.loads(json.dumps(serialize.result_to_dict(res))))
    assert back.status == res.status and len(back.messages) == len(res.messages)
    assert back.agreement.effective_day == res.agreement.effective_day
    assert back.agreement.new_contract.columns["total_amount"].deprecated_until == res.agreement.new_contract.columns["total_amount"].deprecated_until


# ------------------------------------------------------------ robustness on real-world column types
def test_complex_types_are_never_treated_as_text_or_numbers():
    from engine.backend import category
    for t in ("struct<a:string,b:int>", "array<string>", "map<string,int>", "STRUCT(a VARCHAR)", "VARCHAR[]", "binary"):
        assert category(t) == "other", t
    assert category("string") == "string" and category("bigint") == "numeric" and category("timestamp") == "temporal"


def test_measurement_survives_struct_columns_and_impossible_variants():
    import duckdb
    from engine import measure, workload
    from engine.backend import DuckBackend
    con = duckdb.connect()
    con.execute("CREATE TABLE events__orig AS SELECT i AS event_id, i % 3 AS user_id, {'k': 'v'} AS metadata, "
                "CAST(i AS DOUBLE) AS cost, 'click' AS kind FROM range(50) t(i)")
    be = DuckBackend(con)
    tables = {"events": be.columns("events__orig")}
    specs = workload.generate(tables)
    measure.build_sandbox(be, tables, specs)
    out = measure.measure(be, tables, specs, progress=lambda m: None)
    assert out["rows"] and not [r for r in out["rows"] if r["column"] == "metadata" and r["kind"] == "retype"]
