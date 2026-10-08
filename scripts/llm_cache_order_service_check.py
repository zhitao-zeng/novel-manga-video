#!/usr/bin/env python3
"""CPU contract check against the installed vLLM adapter and tokenizer, no inference."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def adapter(state: Path, variant: str):
    import vllm.entrypoints.anthropic.serving as installed
    filename = (Path(installed.__file__) if variant == 'installed' else
                state/('before.py' if variant == 'before' else 'after.py'))
    namespace = {'__name__': 'vllm.entrypoints.anthropic.cache_order_check',
                 '__package__': 'vllm.entrypoints.anthropic'}
    exec(compile(filename.read_text(), str(filename), 'exec'), namespace)
    cls = namespace['AnthropicServingMessages']
    shared_renderer = SimpleNamespace(chat_template=None)
    with patch.object(namespace['OpenAIServingChat'], '__init__', return_value=None) as base:
        instance = cls(None, None, 'assistant', online_renderer=shared_renderer, request_logger=None,
                       chat_template=None, chat_template_content_format='auto')
    template = base.call_args.kwargs['online_renderer'].chat_template
    assert shared_renderer.chat_template is None, 'shared OpenAI renderer changed'
    assert template == base.call_args.kwargs.get('chat_template'), 'live renderer has a different template'
    convert = getattr(cls, 'to_chat_completion_request', None) or cls._convert_anthropic_to_openai_request
    return instance, convert, template


def check(state: Path, variant: str, model_dir: Path):
    from vllm.entrypoints.anthropic.protocol import AnthropicMessagesRequest
    metadata = json.loads((state/'prepared.json').read_text())
    instance, convert, template = adapter(state, variant)
    model = 'DeepSeek-V4.1-Flash' if metadata['backend'] == 'ds' else 'Qwen3.8-Flash-Next'
    if metadata['backend'] == 'ds':
        from vllm.tokenizers.deepseek_v41 import DeepseekV41Tokenizer
        tokenizer = DeepseekV41Tokenizer.from_pretrained(str(model_dir), local_files_only=True)
    else:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=True)
        if variant != 'before':
            assert template == Path(metadata['template']).read_text(), 'Qwen handler did not load the derived template'
    base_system = 'Keep the original facts and obey the current instruction hierarchy.'
    fixed = 'Stable reference material for the same episode. '*400
    messages = [{'role':'user','content':fixed},
                {'role':'assistant','content':'Reference read.'},
                {'role':'system','content':'<total_tokens>120000 tokens left</total_tokens>'},
                {'role':'system','content':'The next answer must retain the speaker identity.'},
                {'role':'user','content':'Continue.'}]
    tools = [{'name':'inspect_reference','description':'Read one existing reference.',
              'input_schema':{'type':'object','properties':{'name':{'type':'string'}},'required':['name']}}]
    def request(rows):
        value = AnthropicMessagesRequest(model=model, max_tokens=64,
            system=[{'type':'text','text':'x-anthropic-billing-header: diagnostic-placeholder'},
                    {'type':'text','text':base_system}], messages=rows, tools=tools)
        return convert(value, merge_inline_system=instance._merge_inline_system)
    first = request(messages)
    assert first.messages == [{'role':'system','content':base_system}, *messages], 'late system content was moved before history'
    later = [*messages, {'role':'assistant','content':'Continuing the same task.'},
             {'role':'system','content':'<total_tokens>110000 tokens left</total_tokens>'},
             {'role':'user','content':'Continue once more.'}]
    second = request(later)
    assert second.messages[:len(first.messages)] == first.messages, 'previous request content changed'
    def encode(chat, content_blocks=False):
        definitions = [t.model_dump(exclude_none=True) for t in (chat.tools or [])]
        options = {'chat_template':template} if template is not None else {}
        rows = deepcopy(chat.messages)
        if metadata['backend'] == 'qwen':
            for row in rows:
                for call in row.get('tool_calls') or []:
                    fn = call['function']
                    if isinstance(fn.get('arguments'), str):
                        fn['arguments'] = json.loads(fn['arguments'])
        if content_blocks:
            # Qwen's real vLLM renderer normalizes text to OpenAI content blocks.
            for row in rows:
                if isinstance(row.get('content'), str):
                    row['content'] = [{'type':'text','text':row['content']}]
        result = tokenizer.apply_chat_template(rows, tools=definitions, tokenize=True,
            add_generation_prompt=True, enable_thinking=False, **options)
        if hasattr(result, 'keys'):
            result = result['input_ids']
        if result and isinstance(result[0], list):
            result = result[0]
        assert len(result)>100 and all(isinstance(x,int) for x in result)
        return result
    old, new = encode(first), encode(second)
    if metadata['backend'] == 'qwen':
        assert encode(first, content_blocks=True) == old, 'live content blocks differ from plain text'
        assert encode(second, content_blocks=True) == new, 'continued content blocks differ from plain text'
    common = next((i for i,(x,y) in enumerate(zip(old,new)) if x!=y), min(len(old),len(new)))
    assert common >= len(old)-16, 'new system notice invalidates the old conversation prefix'
    # Mixed tool and later-system messages must retain the call and tool result.
    tool_messages = [*messages,
        {'role':'assistant','content':[{'type':'tool_use','id':'tool_test','name':'inspect_reference','input':{'name':'actor'}}]},
        {'role':'user','content':[{'type':'tool_result','tool_use_id':'tool_test','content':'Reference identity verified.'}]},
        {'role':'system','content':'<total_tokens>100000 tokens left</total_tokens>'}]
    converted = request(tool_messages)
    assert any(m.get('role')=='tool' and m.get('tool_call_id')=='tool_test' for m in converted.messages)
    assert converted.messages[-1] == tool_messages[-1]
    assert converted.max_tokens == 64
    assert len(converted.tools)==1 and converted.tools[0].function.name=='inspect_reference'
    if metadata['backend'] == 'qwen':
        assert encode(converted, content_blocks=True) == encode(converted), 'tool round-trip template differs'
    return {'backend':metadata['backend'],'variant':variant,'passed':True,'inference_requests':0,
            'checks':['budget text and role','other late system rules','stable old-message prefix',
                      'native tokenizer/template','tool call/result','output limit and tool schema',
                      'Qwen plain-text/content-block equivalence'],
            'tokens':{'previous':len(old),'current':len(new),'common_prefix':common}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path('/opt/novel-llm-cache-order'))
    parser.add_argument('--model-dir', type=Path, default=Path('/root/models'))
    parser.add_argument('--variant', choices=('before','candidate','installed'), required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.state_dir,args.variant,args.model_dir),ensure_ascii=False))


if __name__ == '__main__':
    main()
