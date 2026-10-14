"""Observed host compatibility failures eligible for one existing-candidate continuation."""
import json,os
from .input_effects import _phase_groups

SOURCE_REVIEW_POLICY=3

def asset_configuration_recoverable(ctx, record):
    """One unchanged continuation for the observed pre-request image-port failure."""
    if (record.get('status') != 'incomplete' or record.get('step') != 'assets'
            or record.get('published') or record.get('asset_configuration_recovery')
            or (record.get('source') or {}).get('consistent') is not True
            or 'image helper exited' not in str(record.get('error', ''))):
        return False
    names=set((_phase_groups(record.get('decision') or {}).get('characters') or {}))
    if not names:
        return False
    from novel_manga.llm.resources import _endpoint
    base=os.environ.get('NOVEL_LOCAL_QWEN_IMAGE_URL','')
    try:
        if base:_endpoint(base)
    except ValueError:
        return False
    log=ctx.run_dir/'input-assets.log'
    if not log.is_file():return False
    prefix=f'-agent-{ctx.episode_dir.name}-{record["clip_id"]}-'
    latest={}
    for line in log.read_text().splitlines():
        try:row=json.loads(line)
        except ValueError:continue
        if (isinstance(row,dict) and row.get('name') in names
                and prefix in str(row.get('asset_id',''))):
            latest[row['name']]=row
    return names==set(latest) and all(
        'model resource endpoint has an invalid port' in str(row.get('status',''))
        and row.get('seconds') == 0 for row in latest.values())


def source_review_recoverable(record):
    """Recheck only source failures affected by the missing judgment/turn contract."""
    prior = int(record.get('source_review_policy', 1))
    changed_contract = prior < 2 or (prior == 2 and any(
        'turns' in patch for patch in (record.get('decision') or {}).get('shots', [])))
    return (record.get('status') == 'rejected' and record.get('step') == 'source'
            and bool(record.get('decision')) and not record.get('published')
            and prior < SOURCE_REVIEW_POLICY and changed_contract)


def posture_protocol_recoverable(record):
    """One same-candidate continuation after replacing the roster-echo protocol."""
    error = str(record.get('error', ''))
    return (record.get('status') == 'incomplete' and record.get('step') == 'compile'
            and not record.get('published') and not record.get('posture_protocol_recovery')
            and bool(record.get('decision')) and (record.get('source') or {}).get('consistent') is True
            and '姿态台账镜' in error and '入镜名单不符' in error)

