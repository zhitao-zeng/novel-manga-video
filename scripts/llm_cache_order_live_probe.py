#!/usr/bin/env python3
"""Bounded synthetic probe, run inside the existing model container.

Authentication is read in memory from that container, never recorded. Candidate
mode sends the derived token stream to completions; live mode exercises messages.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from llm_cache_order_service_check import adapter


def api_key():
    value = os.environ.get('VLLM_API_KEY')
    if value:
        return value
    args = Path('/proc/1/cmdline').read_bytes().decode().split('\0')
    for i, arg in enumerate(args):
        if arg == '--api-key':
            return args[i+1]
        if arg.startswith('--api-key='):
            return arg.split('=', 1)[1]
    raise RuntimeError('existing service authentication not found; no request sent')


def run(state: Path, mode: str):
    metadata = json.loads((state/'prepared.json').read_text())
    backend = metadata['backend']
    model = 'DeepSeek-V4.1-Flash' if backend == 'ds' else 'Qwen3.8-Flash-Next'
    secret = api_key()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def call(path, data=None, authenticated=True):
        headers = {'Content-Type':'application/json', 'anthropic-version':'2023-06-01'}
        if authenticated:
            headers['Authorization'] = 'Bearer '+secret
        req = urllib.request.Request('http://127.0.0.1:8000'+path,
            data=json.dumps(data).encode() if data is not None else None, headers=headers)
        started = time.monotonic()
        try:
            with opener.open(req, timeout=120) as response:
                return response.status, json.load(response), round(time.monotonic()-started, 3)
        except urllib.error.HTTPError as exc:
            # Do not echo arbitrary server errors, which may contain request data.
            return exc.code, {}, round(time.monotonic()-started, 3)
    status, available, _ = call('/v1/models')
    assert status == 200 and model in [x['id'] for x in available['data']]
    status, _, _ = call('/v1/models', authenticated=False)
    assert status in (401, 403), f'unauthenticated request returned {status}'
    fixed = '\n'.join(f'Reference {i}: Lin lives in the northern city. His coat is blue. The stone room has one window.' for i in range(384))
    system = 'Synthetic cache-order check. Follow the current output code in system messages. Reply with that code only, without explanations.'
    messages = [{'role':'user', 'content':fixed},
        {'role':'assistant', 'content':'References read.'},
        {'role':'system', 'content':'<total_tokens>120000 tokens left</total_tokens>'},
        {'role':'system', 'content':'The current output code is CACHE_ALPHA.'},
        {'role':'user', 'content':'Reply with the current output code.'}]
    later = [*messages, {'role':'assistant', 'content':'CACHE_ALPHA'},
        {'role':'system', 'content':'<total_tokens>110000 tokens left</total_tokens>'},
        {'role':'system', 'content':'The current output code is CACHE_BETA.'},
        {'role':'user', 'content':'Reply with the current output code.'}]
    def payload(rows):
        return {'model':model, 'max_tokens':96, 'temperature':0, 'system':system,
                'messages':rows, 'chat_template_kwargs':{'enable_thinking':False}}
    encode = None
    if mode == 'candidate':
        from vllm.entrypoints.anthropic.protocol import AnthropicMessagesRequest
        instance, convert, template = adapter(state, 'candidate')
        if backend == 'ds':
            from vllm.tokenizers.deepseek_v41 import DeepseekV41Tokenizer
            tokenizer = DeepseekV41Tokenizer.from_pretrained('/root/models', local_files_only=True)
        else:
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained('/root/models', local_files_only=True, trust_remote_code=True)
        def encode(body):
            chat = convert(AnthropicMessagesRequest.model_validate(body), merge_inline_system=instance._merge_inline_system)
            options = {'chat_template':template} if template is not None else {}
            ids = tokenizer.apply_chat_template(chat.messages, tokenize=True,
                add_generation_prompt=True, enable_thinking=False, **options)
            if hasattr(ids, 'keys'):
                ids = ids['input_ids']
            if ids and isinstance(ids[0], list):
                ids = ids[0]
            assert len(ids)>100 and all(isinstance(x,int) for x in ids)
            return {'model':model, 'prompt':ids, 'temperature':0, 'max_tokens':96}
    results = []
    # DS has three independent replicas. Repeated requests permit warming without
    # claiming affinity or silently changing its routing/concurrency settings.
    repeats = 3 if mode == 'live' and backend == 'ds' else 1
    cases = [('first', messages, 'CACHE_ALPHA')]*repeats + [('continued', later, 'CACHE_BETA')]*repeats
    for label, rows, expected in cases:
        body = payload(rows)
        status, data, elapsed = call('/v1/completions' if encode else '/v1/messages', encode(body) if encode else body)
        assert status == 200, f'{mode} {label} returned HTTP {status}'
        output = (data['choices'][0]['text'] if encode else
                  ''.join(b.get('text','') for b in data['content'] if b['type']=='text'))
        record = {'case':label, 'seconds':elapsed, 'usage':data.get('usage'),
                  'output':output, 'expected':expected, 'passed':output.strip()==expected}
        results.append(record)
        (state/(mode+'-probe.json')).write_text(json.dumps({'backend':backend,'mode':mode,'results':results},indent=2))
        print(json.dumps(record), flush=True)
        assert record['passed'], f'{label} did not obey the current system instruction'
    if mode == 'live':
        status, counted, _ = call('/v1/messages/count_tokens', payload(later))
        assert status == 200 and counted['input_tokens'] > 100
        body = payload([{'role':'user', 'content':'Inspect the actor reference using the provided tool.'}])
        body['system'] = 'Use the requested tool with name actor.'
        body['tools'] = [{'name':'inspect_reference','description':'Read a reference.',
            'input_schema':{'type':'object','properties':{'name':{'type':'string'}},'required':['name']}}]
        body['tool_choice'] = {'type':'tool','name':'inspect_reference'}
        status, data, elapsed = call('/v1/messages', body)
        assert status == 200, f'tool request returned HTTP {status}'
        calls = [x for x in data['content'] if x['type']=='tool_use']
        assert len(calls)==1 and calls[0]['name']=='inspect_reference' and calls[0]['input']=={'name':'actor'}
        results.append({'case':'tool', 'passed':True, 'seconds':elapsed, 'usage':data.get('usage')})
        follow = payload([*body['messages'], {'role':'assistant','content':data['content']},
            {'role':'user','content':[{'type':'tool_result','tool_use_id':calls[0]['id'],'content':'Actor reference verified.'}]},
            {'role':'system','content':'<total_tokens>100000 tokens left</total_tokens>'},
            {'role':'system','content':'The current output code is CACHE_DONE.'},
            {'role':'user','content':'Reply with the current output code.'}])
        follow['tools'] = body['tools']
        follow['tool_choice'] = {'type':'none'}
        status, data, elapsed = call('/v1/messages', follow)
        assert status == 200, f'tool continuation returned HTTP {status}'
        output = ''.join(x.get('text','') for x in data['content'] if x['type']=='text')
        assert output.strip()=='CACHE_DONE', 'tool result and later system message failed'
        results.append({'case':'tool_continued','passed':True,'seconds':elapsed,'usage':data.get('usage'),'output':output})
        report = {'backend':backend,'mode':mode,'passed':True,'count_tokens':counted,
            'auth_verified':True,'inference_requests':len(results),'results':results}
        (state/'live-probe.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({'passed':True,'count_tokens':counted,'tool_passed':True}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path('/opt/novel-llm-cache-order'))
    parser.add_argument('--mode', choices=('baseline','candidate','live'), required=True)
    args = parser.parse_args()
    run(args.state_dir,args.mode)
