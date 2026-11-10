"""LLM layer. The model writes each team's lines and proposes the producer's counter-offers.

It never decides whether a deal is valid: the rule engine is the referee. Every model output is
validated (JSON shape, limits, numbers kept intact) and falls back to the rule-based text or offer.
"""
from __future__ import annotations

import json
import re

from .models import Proposal


def spark_ask(spark, endpoint: str, max_retries: int = 1):
    """Return ask(prompt) -> str that calls a Databricks model endpoint through ai_query."""
    def ask(prompt: str) -> str:
        safe = prompt.replace("\\", "\\\\").replace("'", "\\'")
        for attempt in range(max_retries + 1):
            try:
                return spark.sql(f"SELECT ai_query('{endpoint}', '{safe}') AS r").collect()[0]["r"]
            except Exception:  # noqa: BLE001
                if attempt == max_retries:
                    raise
    return ask


def _json(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except Exception:  # noqa: BLE001
        return None


def _numbers(s: str) -> set:
    return set(re.findall(r"\d+", s))


class LLM:
    def __init__(self, ask):
        self.ask = ask
        self.log = []  # (purpose, prompt, reply) for tracing

    def _call(self, purpose: str, prompt: str):
        try:
            reply = self.ask(prompt)
        except Exception as e:  # noqa: BLE001
            reply = f"ERROR: {e}"
        self.log.append((purpose, prompt, reply))
        return reply

    def voices(self, context: str, items: list) -> dict:
        """items: [{name, role, facts, draft}] -> {name: line}. Lines that lose a number are dropped."""
        prompt = (
            f"You write lines in a negotiation about a database change: {context}\n"
            "For each participant write one or two short sentences in the first person, in their voice. Say what "
            "their 'draft' says and add a reason taken from their 'facts'. Never change, drop or invent numbers or "
            "dates: every number in the draft must appear in your line. Reply with ONLY a JSON object that maps "
            f"each participant's name to their line.\nParticipants: {json.dumps(items)}")
        data = _json(self._call("voices", prompt)) or {}
        out = {}
        for it in items:
            line = data.get(it["name"])
            if isinstance(line, str) and line.strip() and _numbers(it["draft"]) <= _numbers(line):
                out[it["name"]] = line.strip()
        return out

    def counter(self, producer, proposal: Proposal, objections: list, today: int):
        """Ask the model for the producer's next offer. Returns (Proposal, message) or None if invalid."""
        prompt = (
            f"You are the {producer.name} negotiating a database change. Current offer: effective day "
            f"{proposal.effective_day}, old column kept {proposal.overlap_days} days, compatibility view "
            f"{proposal.compat_view}, snapshot {proposal.snapshot}. Objections: {json.dumps(objections)}.\n"
            f"You may change ONLY: effective_day (integer days from today, at most {producer.latest_day}), "
            f"overlap_days (0 to {producer.max_overlap_days}), compat_view (true/false; it lasts "
            f"{producer.max_view_days} days), snapshot (true/false). Make the SMALLEST concession that answers the "
            "objections. Reply with ONLY JSON: "
            '{"effective_day": int, "overlap_days": int, "compat_view": bool, "snapshot": bool, '
            '"message": "one sentence explaining the offer that mentions each number"}')
        d = _json(self._call("counter", prompt))
        try:
            day, overlap = int(d["effective_day"]), int(d["overlap_days"])
            view, snap, msg = bool(d["compat_view"]), bool(d["snapshot"]), str(d["message"]).strip()
        except Exception:  # noqa: BLE001
            return None
        if not (today <= day <= producer.latest_day and 0 <= overlap <= producer.max_overlap_days and msg):
            return None
        new = Proposal(change=proposal.change, effective_day=day, overlap_days=overlap, compat_view=view,
                       view_days=producer.max_view_days if view else 0, snapshot=snap)
        return None if new.key() == proposal.key() else (new, msg)
