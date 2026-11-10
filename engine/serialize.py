"""Turn a negotiation Result into plain JSON and back, so it can be computed on Databricks and shown in the app."""
from __future__ import annotations

from dataclasses import asdict

from .models import Agreement, Change, Column, Contract, Message, Proposal, Response, Result


def result_to_dict(res: Result) -> dict:
    a = res.agreement
    return {
        "status": res.status, "affected": res.affected, "unaffected": res.unaffected, "open_issues": res.open_issues,
        "messages": [asdict(m) for m in res.messages],
        "rounds": [{"round": r["round"], "proposal": asdict(r["proposal"]),
                    "responses": [{"consumer": x.consumer, "accept": x.accept} for x in r["responses"]]}
                   for r in res.rounds],
        "agreement": None if a is None else {**asdict(a)},
    }


def _change(d):
    return Change(**d)


def result_from_dict(d: dict) -> Result:
    rounds = [{"round": r["round"], "proposal": Proposal(**{**r["proposal"], "change": _change(r["proposal"]["change"])}),
               "responses": [Response(x["consumer"], x["accept"], []) for x in r["responses"]]} for r in d["rounds"]]
    agreement = None
    if d["agreement"]:
        a = d["agreement"]
        c = a["new_contract"]
        contract = Contract(c["table"], c["version"], {n: Column(**col) for n, col in c["columns"].items()}, c["notes"])
        agreement = Agreement(change=_change(a["change"]), effective_day=a["effective_day"],
                              overlap_days=a["overlap_days"], compat_view=a["compat_view"], view_days=a["view_days"],
                              snapshot=a["snapshot"], signed_by=a["signed_by"], new_contract=contract)
    return Result(d["status"], [Message(**m) for m in d["messages"]], rounds, d["affected"], d["unaffected"],
                  agreement, d["open_issues"])
