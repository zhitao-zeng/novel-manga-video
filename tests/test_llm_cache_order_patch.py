"""The deployment patch must preserve changing system messages and their priority."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1]/'scripts/llm_cache_order_patch.py'


def module():
    spec = importlib.util.spec_from_file_location('llm_cache_order_patch', SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ADAPTER = '''class Base:
    def __init__(self, **kwargs):
        self.chat_template = kwargs.get('chat_template')
        self.online_renderer = kwargs.get('online_renderer')

class AnthropicServingMessages(Base):
    def __init__(self, chat_template=None, online_renderer=None):
        super().__init__(chat_template=chat_template, online_renderer=online_renderer)
        self._merge_inline_system = self._detect_merge_inline_system(chat_template)

    @staticmethod
    def _detect_merge_inline_system(chat_template):
        return chat_template is None

    def convert(self, system, messages):
        leading = system
        if self._merge_inline_system:
            leading += ''.join(m['content'] for m in messages if m['role'] == 'system')
            messages = [m for m in messages if m['role'] != 'system']
        return [{'role':'system', 'content':leading}, *messages]
'''


def make_adapter(source, *args, **kwargs):
    namespace = {}
    exec(compile(source, 'adapter_fixture.py', 'exec'), namespace)
    return namespace['AnthropicServingMessages'](*args, **kwargs)


def test_budget_and_other_late_system_rules_stay_in_chronological_order():
    patch = module()
    before = make_adapter(ADAPTER)
    after = make_adapter(patch.patch_serving(ADAPTER))
    messages = [{'role':'user','content':'unchanged long chapter'},
                {'role':'assistant','content':'completed inspection'},
                {'role':'system','content':'<total_tokens>12345 tokens left</total_tokens>'},
                {'role':'system','content':'Preserve the original dialogue and evidence.'},
                {'role':'user','content':'Continue the same repair.'}]
    assert before.convert('Fixed rules.', messages)[0]['content'] != 'Fixed rules.'
    converted = after.convert('Fixed rules.', messages)
    assert converted == [{'role':'system','content':'Fixed rules.'}, *messages]
    assert converted[3]['role'] == 'system' and '12345' in converted[3]['content']


def test_qwen_handler_loads_only_its_derived_template(tmp_path):
    patch = module()
    template = tmp_path/'inline-system.jinja'
    template.write_text('derived template: retain every chronological system message')
    source = patch.patch_serving(ADAPTER, template_path=template)
    assert make_adapter(source).chat_template == template.read_text()
    assert make_adapter(source, 'explicit compatible template').chat_template == 'explicit compatible template'
    assert make_adapter(ADAPTER).chat_template is None
    assert patch.patch_serving(source, template_path=template) == source


def test_unknown_adapter_layout_is_rejected_before_source_is_changed():
    patch = module()
    with pytest.raises(ValueError, match='adapter'):
        patch.patch_serving(ADAPTER.replace('self._merge_inline_system =', 'self.other_setting ='))


def test_qwen_live_renderer_uses_derived_template_without_changing_openai_renderer(tmp_path):
    patch = module()
    template = tmp_path/'inline-system.jinja'
    template.write_text('derived chronological system template')
    shared_resource = object()
    original = SimpleNamespace(chat_template=None, renderer=shared_resource)
    after = make_adapter(patch.patch_serving(ADAPTER, template), online_renderer=original)
    assert after.online_renderer.chat_template == template.read_text()
    assert after.online_renderer is not original
    assert original.chat_template is None
    assert after.online_renderer.renderer is shared_resource


def test_qwen_template_replaces_only_the_late_system_rejection():
    patch = module()
    template = """{% for message in messages %}
{% if message['role'] == 'system' and not loop.first %}
{{- raise_exception('System message must be at the beginning.') }}
{% else %}{{ message['content'] }}{% endif %}
{% endfor %}
{{ raise_exception('unrelated validation must remain') }}"""
    revised = patch.patch_qwen_template(template)
    assert "message['content']" in revised and '<|im_start|>system' in revised
    assert "raise_exception('unrelated validation must remain')" in revised
    assert 'total_tokens' not in revised  # no budget-specific deletion/filtering
    assert patch.patch_qwen_template(revised) == revised
    with pytest.raises(ValueError, match='template'):
        patch.patch_qwen_template('unknown template layout')


def test_rollback_refuses_to_overwrite_a_later_unrelated_edit(tmp_path):
    patch = module()
    source = tmp_path/'serving.py';source.write_text(ADAPTER)
    state = tmp_path/'backup'
    patched = patch.patch_serving(ADAPTER)
    patch.install_source(source, patched, state)
    assert source.read_text() == patched
    source.write_text(patched+'\n# unrelated later operator change\n')
    with pytest.raises(ValueError, match='changed'):
        patch.rollback_source(source, state)
    assert 'unrelated later operator change' in source.read_text()
    source.write_text(patched)
    patch.rollback_source(source, state)
    assert source.read_text() == ADAPTER
