from argparse import Namespace
from types import SimpleNamespace
import json

from novel_manga.application.production.reports import report


def test_planning_report_serializes_selected_agent_config_path(tmp_path):
    config=tmp_path/'agent.json'
    args=Namespace(agent_config=config,chapters='17,22,56,82',stage='plan',dry_run=False,
                   unattended=False,review_only=False)
    batch=SimpleNamespace(novel_dir=tmp_path,novel_id='book',rows={17:{'plan':'planned','clips':14}},
        args=args,reviewing=False,plan_status=lambda n:'planned',render_status=lambda n:'pending')
    assert report(batch,[17])==0
    saved=json.loads((tmp_path/'batch_report.json').read_text())
    assert saved['args']['agent_config']==str(config)
    assert saved['rows']['17']['plan']=='planned'
    assert args.agent_config==config


def test_volume_summary_usage_keeps_book_and_stage_attribution(tmp_path,monkeypatch):
    from novel_manga.application.production.reports import volume_checkpoint
    from novel_manga.application.identity import volume_context
    from novel_manga.llm import usage
    seen=[]
    def summarize(*a):
        seen.append(usage.scoped());return {}
    monkeypatch.setattr(volume_context,'summarize_volume',summarize)
    batch=SimpleNamespace(args=Namespace(volume_size=50),novel_dir=tmp_path,novel_id='book',rows={100:{}},
                          novel=lambda:SimpleNamespace(episodes=range(501)))
    volume_checkpoint(batch,100,[100])
    assert seen[0]['novel']=='book' and seen[0]['stage']=='volume_summary'


def test_partial_repair_batch_does_not_buy_a_volume_summary(tmp_path,monkeypatch):
    from novel_manga.application.production.reports import volume_checkpoint
    from novel_manga.application.identity import volume_context
    seen=[]
    monkeypatch.setattr(volume_context,'summarize_volume',lambda *a:seen.append(a) or {})
    batch=SimpleNamespace(args=Namespace(volume_size=50),novel_dir=tmp_path,novel_id='book',
                          rows={25:{},29:{},501:{}},novel=lambda:SimpleNamespace(episodes=range(501)))
    volume_checkpoint(batch,25,[25])
    volume_checkpoint(batch,29,[29])
    assert seen==[] and not list(tmp_path.glob('volume_review_*.md'))
    volume_checkpoint(batch,501,[501])
    assert len(seen)==1 and seen[0][1:]==(501,501)
