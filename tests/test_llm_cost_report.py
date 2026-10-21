import importlib.util
from pathlib import Path


def report_module():
    path = Path(__file__).resolve().parents[1]/'scripts/llm_usage_report.py'
    spec = importlib.util.spec_from_file_location('llm_report', path)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


PRICES = {'ds41': {'input_credits_per_million_tokens': 300,
                  'output_credits_per_million_tokens': 1200,
                  'cache_read_credits_per_million_tokens': 6}}


def test_cached_input_is_priced_inside_input_total_not_added_again():
    report = report_module().summarize([{'model': 'ds41', 'prompt_tokens': 1000000,
        'completion_tokens': 100000, 'cache_read_input_tokens': 800000, 'protocol': 'chat_completions'}], PRICES)
    assert report['estimated_credits_min'] == report['estimated_credits_max'] == 184.8
    assert report['cost_basis'] == 'token_price_estimate_not_invoice'


def test_missing_usage_and_native_semantics_remain_explicit():
    report = report_module().summarize([
        {'model': 'ds41', 'event_type': 'agent_attempt', 'usage_known': False},
        {'model': 'ds41', 'protocol': 'messages', 'usage_source': 'cli_transcript',
         'input_semantics': 'provider_native_unverified', 'input_tokens': 1000000,
         'cache_read_input_tokens': 800000, 'output_tokens': 100000}], PRICES)
    assert report['estimated_credits_min'] == 184.8
    assert report['estimated_credits_max'] == 424.8
    assert report['unidentified_agent_attempts'] == 1
    assert report['unknown_usage_records'] == 1
    assert report['native_semantics_unverified'] == 1


def test_initial_native_stream_estimate_is_not_sold_as_actual_usage():
    report = report_module().summarize([{'model': 'ds41', 'protocol': 'messages',
        'usage_source': 'cli_stream_unverified', 'input_tokens': 1000000, 'output_tokens': 0}], PRICES)
    assert report['cost_unknown_records'] == 1
    assert report['estimated_credits_min'] == report['estimated_credits_max'] == 0
def test_resumed_codex_totals_are_differenced_per_thread_without_changing_raw_rows():
    from copy import deepcopy
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'scripts/llm_usage_report.py'
    spec=importlib.util.spec_from_file_location('cli_cost_report',path)
    report=importlib.util.module_from_spec(spec);spec.loader.exec_module(report)
    def row(thread,inputs,outputs):
        return {'name':'episode-agent','model':'gpt-6-luna','backend':'codex-chatgpt-cli',
            'usage_source':'codex_cli_turn','response_id':thread+'/attempt','usage_known':True,
            'input_tokens':inputs,'output_tokens':outputs,'cache_read_input_tokens':inputs//2}
    rows=[row('one',100,10),row('two',200,20),row('one',180,16)]
    original=deepcopy(rows)
    result=report.summarize(rows)
    assert result['by_role']['episode-agent']['reported_input_tokens']==380
    assert result['by_role']['episode-agent']['reported_output_tokens']==36
    normalized=report.normalize_cli_counters(rows)
    assert normalized[-1]['input_tokens']==80
    assert normalized[-1]['cache_read_input_tokens']==40
    assert report.normalize_cli_counters(normalized)==normalized
    assert rows==original
