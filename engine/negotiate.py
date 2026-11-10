"""Two-sided negotiation: a producer agent and one agent per affected consumer.

All decisions are deterministic rules, so the same input always gives the same deal.
"""
from __future__ import annotations

from dataclasses import replace

from .enforce import apply_agreement
from .models import (Agreement, Change, Consumer, Contract, Message, Producer,
                     Proposal, Response, Result, day_to_date)


def _covered(c: Consumer, p: Proposal) -> bool:
    """Is the consumer's need for the OLD column covered by this proposal?"""
    return (p.overlap_days >= c.min_overlap_days
            or (c.can_use_view and p.compat_view and p.view_days >= c.min_overlap_days)
            or (c.can_use_snapshot and p.snapshot))


def evaluate(c: Consumer, p: Proposal, contract: Contract, today: int) -> Response:
    """A consumer agent reacts to the current proposal."""
    ch, col, issues = p.change, p.change.column, []

    if ch.kind == "drop_column" and col in c.hard_dependency:
        return Response(c.name, False, [{"type": "fatal", "ask": None,
                                         "text": f"I have no replacement for `{col}`; dropping it breaks me permanently."}])

    notice = p.effective_day - today
    if notice < c.notice_days:
        issues.append({"type": "notice", "ask": c.notice_days,
                       "text": f"I need {c.notice_days} days notice; {notice} is not enough."})

    if c.freeze and c.freeze[0] <= p.effective_day <= c.freeze[1]:
        issues.append({"type": "freeze", "ask": c.freeze[1] + 1,
                       "text": (f"{day_to_date(p.effective_day)} falls inside my freeze window "
                                f"({day_to_date(c.freeze[0])} to {day_to_date(c.freeze[1])}). "
                                f"Earliest I can accept is {day_to_date(c.freeze[1] + 1)}.")})

    if ch.kind in ("rename_column", "drop_column") and not _covered(c, p):
        issues.append({"type": "overlap", "ask": c.min_overlap_days,
                       "text": f"I need the old `{col}` to stay available for {c.min_overlap_days} days after the change."})

    if ch.kind in ("change_type", "change_meaning"):
        what = f"as {ch.new_dtype}" if ch.kind == "change_type" else f"with values multiplied by {ch.factor:g}"
        restore = (f"casts it back to {contract.columns[col].dtype}" if ch.kind == "change_type"
                   else "restores the old meaning")
        if not (c.can_use_view or c.can_use_snapshot):
            issues.append({"type": "fatal", "ask": None,
                           "text": f"I cannot consume `{col}` {what} and have no workaround."})
        elif not ((c.can_use_view and p.compat_view) or (c.can_use_snapshot and p.snapshot)):
            if c.can_use_view:
                issues.append({"type": "view", "ask": True,
                               "text": f"I cannot read `{col}` {what}. Give me a compatibility view that {restore}."})
            else:
                issues.append({"type": "snapshot", "ask": True,
                               "text": f"I cannot read `{col}` {what}. Give me a frozen snapshot with the old values."})

    return Response(c.name, not issues, issues)


def counter(prod: Producer, p: Proposal, objections: list, consumers: dict, today: int):
    """Producer concedes in order: later date, longer overlap, compatibility view, snapshot.
    Returns (new_proposal, notes, fatal)."""
    issues = [(r.consumer, i) for r in objections for i in r.issues]
    fatal = [(who, i) for who, i in issues if i["type"] == "fatal"]
    if fatal:
        return p, [], fatal

    new, notes = replace(p), []
    day = max([p.effective_day] + [today + i["ask"] for _, i in issues if i["type"] == "notice"]
              + [i["ask"] for _, i in issues if i["type"] == "freeze"])
    if day > prod.latest_day:
        return p, [], [("Producer", {"text": f"Moving to {day_to_date(day)} misses my release deadline "
                                             f"({day_to_date(prod.latest_day)})."})]
    if day != p.effective_day:
        new.effective_day = day
        notes.append(f"move the change to {day_to_date(day)}")

    overlap_asks = {who: i["ask"] for who, i in issues if i["type"] == "overlap"}
    if overlap_asks:
        need = max(overlap_asks.values())
        new.overlap_days = min(max(new.overlap_days, need), prod.max_overlap_days)
        if need <= prod.max_overlap_days:
            notes.append(f"keep the old column for {new.overlap_days} days")
        else:
            notes.append(f"keep the old column for {new.overlap_days} days (my maximum; some of you asked for {need})")
            if any(consumers[w].can_use_view for w in overlap_asks):
                new.compat_view, new.view_days = True, prod.max_view_days
                notes.append(f"publish a compatibility view for {prod.max_view_days} days")
            if any(consumers[w].can_use_snapshot for w in overlap_asks):
                new.snapshot = True
                notes.append("export a frozen snapshot for those who need longer")

    if any(i["type"] == "view" for _, i in issues):
        new.compat_view, new.view_days = True, max(new.view_days, prod.max_view_days)
        notes.append("publish a compatibility view that restores the old shape")
    if any(i["type"] == "snapshot" for _, i in issues):
        new.snapshot = True
        notes.append("export a frozen snapshot with the old values")

    return new, notes, []


def _summary(p: Proposal) -> str:
    bits = [f"change on {day_to_date(p.effective_day)}"]
    if p.overlap_days:
        bits.append(f"old column kept {p.overlap_days} days")
    if p.compat_view:
        bits.append(f"compatibility view for {p.view_days} days")
    if p.snapshot:
        bits.append("frozen snapshot export")
    return ", ".join(bits)


def negotiate(contract: Contract, change: Change, affected: list, others: list, producer: Producer,
              today: int = 0, max_rounds: int = 6, evidence: str = "Impact measurement",
              llm=None, facts: dict | None = None) -> Result:
    """affected: Consumer agents measured to break or change. others: names of consumers that do not.

    Rules always decide whether a proposal is acceptable. With `llm` (engine.llm.LLM), the model writes each
    team's lines and proposes the producer's counter-offers; invalid model output falls back to the rules.
    `facts`: consumer name -> measured evidence, shown to the model so it can give real reasons."""
    by_name = {c.name: c for c in affected}
    names_affected = [c.name for c in affected]
    names_other = list(others)
    msgs, rounds = [], []

    def say(rnd, speaker, role, kind, text):
        msgs.append(Message(rnd, speaker, role, kind, text))

    def finish(status, agreement=None, open_issues=()):
        return Result(status, msgs, rounds, names_affected, names_other, agreement, list(open_issues))

    def escalate(rnd, text, issues):
        say(rnd, producer.name, "producer", "escalate", text)
        say(rnd, "System", "system", "escalate", "Escalated to a human. The change stays blocked until the owners decide.")
        return finish("escalated", open_issues=issues)

    proposal = Proposal(change=change, effective_day=change.effective_day)
    say(0, producer.name, "producer", "propose",
        f"I want to {change.describe()} on {contract.table}, effective {day_to_date(change.effective_day)}.")
    say(0, "Impact test", "system", "info",
        f"{evidence} shows {len(affected)} consumer(s) break or change when `{change.column}` changes: "
        f"{', '.join(names_affected) or 'none'}. {len(names_other)} are unaffected and not invited.")

    if not affected:
        say(1, "System", "system", "info", "Nobody depends on this column. Change approved automatically.")
        return finish("agreed", _agree(contract, proposal, []))

    seen = set()
    for rnd in range(1, max_rounds + 1):
        responses = [evaluate(c, proposal, contract, today) for c in affected]
        rounds.append({"round": rnd, "proposal": proposal, "responses": responses})
        drafts = [(r, "accept", f"Works for me: {_summary(proposal)}.") if r.accept
                  else (r, "object", " ".join(i["text"] for i in r.issues)) for r in responses]
        lines = {}
        if llm:
            lines = llm.voices(f"{producer.name} wants to {change.describe()} on {contract.table}.",
                               [{"name": r.consumer, "role": f"{by_name[r.consumer].kind} owned by {by_name[r.consumer].owner}",
                                 "facts": (facts or {}).get(r.consumer, ""), "draft": text} for r, _, text in drafts])
        for r, kind, text in drafts:
            say(rnd, r.consumer, "consumer", kind, lines.get(r.consumer, text))

        objections = [r for r in responses if not r.accept]
        if not objections:
            agreement = _agree(contract, proposal, names_affected)
            say(rnd, "System", "system", "info",
                f"All {len(affected)} affected consumers signed. Contract v{agreement.new_contract.version} published.")
            return finish("agreed", agreement)

        new, notes, fatal = counter(producer, proposal, objections, by_name, today)
        if fatal:
            reasons = [f"{who}: {i['text']}" for who, i in fatal]
            return escalate(rnd, "I cannot resolve this with the agents alone: " + " ".join(reasons), reasons)
        text = "Counter-offer: " + "; ".join(notes) + "."
        if llm and rnd < 4:  # the model proposes; invalid or repeated offers fall back to the rule ladder
            offer = llm.counter(producer, proposal, [{"consumer": r.consumer, "asks": [i["text"] for i in r.issues]}
                                                     for r in objections], today)
            if offer and offer[0].key() not in seen:
                new, text = offer
        if new.key() == proposal.key() or new.key() in seen:
            return escalate(rnd, "We are going in circles and I have nothing more to offer.", ["stalemate"])
        seen.add(proposal.key())
        say(rnd, producer.name, "producer", "counter", text)
        proposal = new

    return escalate(max_rounds, "Round limit reached.", ["round limit"])


def _agree(contract: Contract, p: Proposal, signers: list) -> Agreement:
    a = Agreement(change=p.change, effective_day=p.effective_day, overlap_days=p.overlap_days,
                  compat_view=p.compat_view, view_days=p.view_days, snapshot=p.snapshot,
                  signed_by=signers, new_contract=contract)
    a.new_contract = apply_agreement(contract, a)
    return a
