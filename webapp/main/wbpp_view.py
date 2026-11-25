"""Target page: WBPP runs (PixInsight WeightedBatchPreprocessing)."""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select

from webapp import db
from webapp.archive import wbpp
from webapp.models import Frame, Target


def target_runs(t: Target) -> list[dict]:
    runs = wbpp.runs_by_target(t.folders or [])
    if not runs:
        return []
    lights = db.session.scalars(select(Frame).where(Frame.target_folder.in_(t.folders),
                                                    Frame.frame_type == "light")).all()
    latest_ok = next((w for w in runs if wbpp.master_written(w)), None)
    out = []
    for w in runs:
        cov = wbpp.coverage(w, lights)
        errs = Counter(e["message"] for e in w.errors or [])
        frames = sorted(((s, m) for s, m in (w.frames or {}).items() if m.get("rejected_pct") is not None),
                        key=lambda kv: -kv[1]["rejected_pct"])
        med = sorted(m["rejected_pct"] for _, m in frames)
        median = med[len(med) // 2] if med else None
        out.append(dict(
            run=w, ok=wbpp.master_written(w), latest=w is latest_ok, cov=cov,
            errors=errs.most_common(), warnings=sorted((w.warnings or {}).items(), key=lambda kv: -kv[1]),
            masters=next((b.get("masters") for b in w.blocks or [] if b.get("masters")), None),
            steps=[b for b in w.blocks or [] if b.get("step")],
            worst=frames[:12], median_rej=median,
        ))
    return out


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def session_status() -> dict[str, dict]:
    """session root -> WBPP status for the session overview (missing = no WBPP run of the target).

    ok     the newest run that used lights of this session wrote a masterLight without errors
    none   no WBPP log, or no run used lights of this session (not stacked yet)
    error  that run failed (no masterLight) or reported errors
    """
    from collections import defaultdict

    from webapp.models import Session, WbppRun
    runs = db.session.scalars(select(WbppRun).where(WbppRun.error.is_(None)).order_by(WbppRun.started.desc())).all()
    if not runs:
        return {}
    used = [(w, set(w.lights_used or [])) for w in runs]
    lights: dict[str, set[str]] = defaultdict(set)
    q = select(Frame.session_root, Frame.path).where(Frame.frame_type == "light", Frame.rejected.is_(False),
                                                     Frame.duplicate.is_(False))
    for root, path in db.session.execute(q):
        lights[root].add(_stem(path))
    folders_of = {t.id: set(t.folders or []) for t in db.session.scalars(select(Target))}
    out = {}
    for s in db.session.scalars(select(Session).where(Session.lights > 0)):
        mine = lights.get(s.root, set())
        hits = [(w, u & mine) for w, u in used if u & mine]
        when = lambda w: w.started.strftime("%d.%m.%Y") if w.started else "?"  # noqa: E731
        if not hits:
            if any(w.target_folder in folders_of.get(s.target_id, ()) for w in runs):
                out[s.root] = dict(status="none", reasons=["WBPP-Läufe des Ziels enthalten diese Session nicht"],
                                   notes=[], target_id=s.target_id)
            continue
        w, inside = hits[0]                                  # newest run with lights of this session
        notes = [f"{len(inside)} von {len(mine)} Lights im Stack vom {when(w)}"]
        reasons = []
        if not wbpp.master_written(w):
            first = (w.errors or [{}])[0].get("message", "ohne Fehlermeldung")
            reasons.append(f"Lauf vom {when(w)} ohne masterLight – {first}")
            ok_run = next((r for r, _ in hits if wbpp.master_written(r)), None)
            if ok_run:
                notes.append(f"letzter erfolgreicher Lauf: {when(ok_run)}")
        elif w.errors:
            reasons += [f"WBPP-Fehler: {m}" for m in sorted({e['message'] for e in w.errors})][:5]
        missing = len(mine) - len(inside)
        if missing:
            notes.append(f"{missing} Light(s) dieser Session fehlen im Stack")
        out[s.root] = dict(status="error" if reasons else "ok", reasons=reasons, notes=notes,
                           target_id=s.target_id)
    return out


def session_runs(s) -> dict | None:
    """WBPP runs for the session page: the runs of the target that used lights of this session
    (all runs of the target if none did). None if the target has no WBPP log."""
    t = s.target
    if t is None:
        return None
    runs = target_runs(t)
    if not runs:
        return None
    q = select(Frame.path).where(Frame.session_root == s.root, Frame.frame_type == "light",
                                 Frame.rejected.is_(False), Frame.duplicate.is_(False))
    mine = {_stem(p) for p in db.session.scalars(q)}
    for r in runs:
        r["session_in_stack"] = len(mine & set(r["run"].lights_used or []))
    with_session = [r for r in runs if r["session_in_stack"]]
    return dict(runs=with_session or runs, session_lights=len(mine), contains_session=bool(with_session),
                status=session_status().get(s.root), total=len(runs))
