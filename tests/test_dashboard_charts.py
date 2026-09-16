import subprocess
from pathlib import Path

from pipeline_dashboard import net_windows, pipeline_metrics


def test_net_buckets_share_boundaries_without_counting_a_delivery_twice():
    samples = [{'at': n * 60, 'passed': 100 + n} for n in range(11)]
    rows = net_windows(samples)['1']['buckets']
    measured = [r for r in rows if r['value'] is not None]
    assert [r['value'] for r in measured] == [5, 5]
    assert sum(r['value'] for r in measured) == 10
    assert all(not r['partial'] for r in measured)


def test_missing_history_is_not_zero_and_a_gap_is_not_invented_production():
    assert net_windows([]) == {}
    rows = net_windows([{'at': 0, 'passed': 10}, {'at': 1800, 'passed': 15},
                        {'at': 1860, 'passed': 17}])['1']['buckets']
    assert len([r for r in rows if r['value'] is None]) == 11
    last = rows[-1]
    assert last['value'] == 2 and last['partial']
    assert (last['observed_start'], last['observed_end']) == (1800, 1860)


def test_negative_net_progress_survives_chart_aggregation():
    rows = net_windows([{'at': 0, 'passed': 10}, {'at': 60, 'passed': 8}])['1']['buckets']
    assert rows[-1]['value'] == -2
    assert rows[-1]['partial']


def test_first_page_can_draw_existing_samples_without_writing_state(tmp_path):
    import json
    novel = tmp_path / 'book'
    (novel / 'repair_manager').mkdir(parents=True)
    state = novel / 'repair_manager/state.json'
    state.write_text(json.dumps({'summary': {'total': 100, 'deliverable_precise': 12}, 'jobs': []}))
    (novel / 'monitor_metrics').mkdir()
    samples = novel / 'monitor_metrics/net_delivery.json'
    samples.write_text(json.dumps([{'at': 0, 'passed': 10}, {'at': 60, 'passed': 12}]))
    before = state.read_bytes(), samples.read_bytes()
    assert pipeline_metrics(novel)['net_delivery']['windows']['1']['buckets'][-1]['value'] == 2
    assert before == (state.read_bytes(), samples.read_bytes())


def test_progress_partition_and_chart_empty_negative_states():
    source = Path(__file__).resolve().parents[1] / 'scripts/pipeline_dashboard.js'
    code = """const fs=require('fs'),vm=require('vm');const c={};vm.createContext(c);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
const p={total:100,deliverable:10,remaining:90,preparation:{admitted:30},inspection:{episode_buckets:{awaiting_preparation:70,not_fully_checked:5,checked_with_errors:12}}};
const rows=c.pipelineDeliveryRows(p);
if(rows.reduce((n,r)=>n+r.value,0)!==100||rows[3].value!==3)throw Error('progress categories overlap');
const net={sampled_at:'2026-09-15 23:30:00',windows:{'1':{step_minutes:5,buckets:[
 {start:0,end:300,value:null,partial:true},{start:300,end:600,value:-2,partial:true},
 {start:600,end:900,value:0,partial:false}]}}};
const html=c.pipelineTrendChart('book',net);
if(!html.includes('pc-missing')||!html.includes('-2 集净增')||!html.includes('0 集净增'))throw Error('chart loses missing/negative/zero state');
if(html.includes('NaN')||html.includes('undefined'))throw Error('invalid chart markup');
if(!c.pipelineTrendChart('empty',null).includes('正在读取历史采样'))throw Error('missing history must stay empty');
"""
    subprocess.run(['node', '-e', code, str(source)], check=True)
