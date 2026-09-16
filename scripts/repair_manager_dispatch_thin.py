"""repair_manager_dispatch_thin responsibilities; existing job state and scheduling policy."""
from __future__ import annotations
import time
import novel_manga.repair.scheduling as schedule_rules

def split_pending(manager):
    """A legacy batch keeps its child; split only after that child has exited."""
    for job in list(manager.state["jobs"]):
        if job["status"] != "pending" or job["kind"] not in schedule_rules.FLOWS or len(job["episodes"]) <= 1:
            continue
        children = [manager.add(job["kind"], [n], step=job["step"], cycle=job["cycle"],
                             failures=job["failures"], parent=job["id"])["id"] for n in job["episodes"]]
        job.update(status="split", pid=None, children=children, finished_at=time.strftime("%F %T"))


def schedule_recovery(manager):
    """Consume stopped work fairly; a changed strategy gets one bounded cycle."""
    active = sum(len(j["episodes"]) for j in manager.state["jobs"]
                 if j["kind"] in {"repair", "recovery"} and j["status"] in {"pending", "running"})
    busy = schedule_rules.active_episodes(manager.state)
    waiting = {n: j for j in manager.state["jobs"] if j["status"] == "waiting_plan" for n in j["episodes"]}
    # Clear missing secondary-card dependencies before starting more new
    # targets. Otherwise a long initial list starves these stopped jobs.
    for n,row in sorted(manager.info.items()):
        if active >= schedule_rules.REPAIR_EPISODES:
            break
        reasons=[reason for values in manager.state.get('plan_queue',{}).get(str(n),{}).values() for reason in values]
        if not any(reason.startswith('asset:') and reason.endswith('/expressions.jpeg') for reason in reasons):
            continue
        if row.get('held') or row.get('awaiting_preparation') or (n in busy and n not in waiting):
            continue
        old=waiting.pop(n,None)
        if old:
            old.update(status='superseded',finished_at=time.strftime('%F %T'))
        job=manager.add('recovery',[n],recovery_kind='references')
        if old:
            job['replaces']=old['id']
        used=manager.state['recovery_attempts'].setdefault(str(n),{})
        used['references']=used.get('references',0)+1
        busy.add(n);active+=1
    for item in list(manager.state['targeted_recovery']):
        if active >= schedule_rules.REPAIR_EPISODES:
            break
        n = item['episode']
        if n in busy or manager.info.get(n, {}).get('held') or manager.info.get(n, {}).get('awaiting_preparation'):
            continue
        options = {k:v for k,v in item.items() if k not in {'episode', 'method'}}
        manager.add('recovery', [n], recovery_kind=item['method'], **options)
        used = manager.state['recovery_attempts'].setdefault(str(n), {})
        used[item['method']] = used.get(item['method'], 0) + 1
        manager.state['targeted_recovery'].remove(item)
        busy.add(n)
        active += 1
    queues = {k: [] for k in ("plan", "technical", "residual",'managed')}
    exposure = set()
    for n, row in sorted(manager.info.items()):
        used = manager.state["recovery_attempts"].get(str(n), {})
        if row.get("held") or row.get('awaiting_preparation') or (n in busy and n not in waiting):
            continue
        block_reasons = [reason for reasons in manager.state.get('plan_queue', {}).get(str(n), {}).values() for reason in reasons]
        request_only = bool(block_reasons) and all(reason.startswith('request:') for reason in block_reasons)
        kind = ('managed' if row.get('plan_blocked') and request_only and row.get('managed_clips') else "plan" if row.get("plan_blocked") else "technical" if row["status"] == "done_with_warnings"
                else "residual" if row["ready"] and row["bad"] and manager.state["passes"].get(str(n), 0) >= 2 else None)
        cache_exposure = kind == "technical" and used.get(kind) and row.get("exposure_due") and not used.get("exposure")
        if kind and (kind=='managed' or not used.get(kind) or cache_exposure):
            # A waiting record alone is not enough: prepare needs the source script.
            if kind == "plan" and not (manager.novel / f"{manager.novel.name}_{n}" / "chapter_script.json").is_file():
                continue
            queues[kind].append(n)
            if cache_exposure:
                exposure.add(n)
    while active < schedule_rules.REPAIR_EPISODES and any(queues.values()):
        for kind, queue in queues.items():
            if not queue or active >= schedule_rules.REPAIR_EPISODES:
                continue
            n = queue.pop(0)
            if n in waiting:
                old = waiting[n]
                # Transfer the old waiting job's ownership; no competing worker.
                old.update(status="superseded", finished_at=time.strftime("%F %T"))
            job = manager.add("recovery", [n], recovery_kind=kind, **(
                {"step": 1, "cache_only": True, "correction": "restore_underexposed_scene"} if n in exposure else {}))
            if n in waiting:
                job["replaces"] = waiting[n]["id"]
            manager.state["recovery_attempts"].setdefault(str(n), {})["exposure" if n in exposure else kind] = 1
            active += 1


def schedule(manager):
    split_pending(manager)
    schedule_recovery(manager)
    busy = schedule_rules.active_episodes(manager.state)
    busy.update(item['episode'] for item in manager.state['targeted_recovery'])
    def eligible(n):
        return n not in busy and not manager.info[n]["held"] and not manager.info[n].get('awaiting_preparation')
    repair_count = sum(len(j["episodes"]) for j in manager.state["jobs"] if j["kind"] in {"repair", "recovery"} and j["status"] in {"pending", "running"})
    candidates = [n for n in sorted(manager.info) if eligible(n) and manager.info[n]["ready"] and manager.info[n]["bad"]
                  and manager.info[n].get('managed_clips',manager.state['passes'].get(str(n),0)<manager.state['phase'])]
    for n in candidates[:max(0, schedule_rules.REPAIR_EPISODES - repair_count)]:
        manager.add("repair", [n], cycle=min(manager.state['phase'],manager.state["passes"].get(str(n), 0) + 1))
        busy.add(n)
    fills = [j for j in manager.state["jobs"] if j["kind"] == "fill" and j["status"] in {"pending", "running"}]
    free_fills = min(schedule_rules.REPAIR_BATCH_SIZE - sum(len(j["episodes"]) for j in fills if j["step"] == 0),
                     schedule_rules.FILL_EPISODES - sum(len(j["episodes"]) for j in fills))
    for n in [n for n in sorted(manager.info, reverse=not manager.state.get('preparation_gate', False))
              if eligible(n) and manager.info[n]["can_fill"]][:max(0, free_fills)]:
        manager.add("fill", [n])
        busy.add(n)
    confirms = sum(len(j["episodes"]) for j in manager.state["jobs"] if j["kind"] == "confirm" and j["status"] in {"pending", "running"})
    for n in [n for n in sorted(manager.info) if eligible(n) and manager.info[n]["ready"] and manager.info[n]["flash_pending"]][:max(0, 12 - confirms)]:
        manager.add("confirm", [n])
        busy.add(n)
    local_scanning = any(j["kind"] == "scan" and j.get("source") == "local" and j["status"] in {"pending", "running"} for j in manager.state["jobs"])
    if not local_scanning and not any(j["kind"] == "audit" and j["status"] in {"pending", "running"} for j in manager.state["jobs"]):
        group = [n for n in sorted(manager.info) if eligible(n) and manager.info[n]["ready"] and manager.info[n]["unverified"]][:12]
        if group:
            for n in group:
                manager.add("audit", [n])
