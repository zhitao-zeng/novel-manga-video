"""The additional provider preserves request limits and existing runtime routes."""
from types import SimpleNamespace
import pytest

from novel_manga.llm import client,config
from novel_manga.application.production.conductor_workers import server_env
from novel_manga.application.agents import sandbox


def test_reasoning_only_truncation_gets_the_existing_bounded_retry(monkeypatch):
    calls=[]
    def respond(http,bases,headers,payload,**kwargs):
        calls.append(payload['max_tokens'])
        return {'choices':[{'message':{'content':'' if len(calls)==1 else '{"ok":true}'},
                            'finish_reason':'length' if len(calls)==1 else 'stop'}]}
    monkeypatch.setattr(client,'post_any',respond)
    result=client.ask_json([{'type':'text','text':'test'}],client.obj({'ok':{'type':'boolean'}}),
        name='synthetic',max_tokens=128,settings=config.JsonEndpoint('glm-5.3-flash',('http://unused.invalid',)))
    assert result=={'ok':True} and calls==[128,256]


def test_repeated_empty_truncation_fails_instead_of_claiming_valid_json(monkeypatch):
    calls=[]
    def respond(http,bases,headers,payload,**kwargs):
        calls.append(payload['max_tokens'])
        return {'choices':[{'message':{'content':''},'finish_reason':'length'}]}
    monkeypatch.setattr(client,'post_any',respond)
    with pytest.raises(ValueError,match='JSON truncated'):
        client.ask_json([{'type':'text','text':'test'}],client.obj({'ok':{'type':'boolean'}}),
            name='synthetic',max_tokens=128,settings=config.JsonEndpoint('glm-5.3-flash',('http://unused.invalid',)))
    assert calls==[128,256]


def test_provider_flags_reach_conductor_workers_without_affecting_ds():
    conductor=SimpleNamespace(workspaces=None)
    value=server_env(conductor,{'base':'http://127.0.0.1:8789/v1','model':'glm-5.3-flash',
        'key_var':'PHANTHY_API_KEY','endpoint':'phanthy'})
    assert value['QWEN38_LOCAL_STREAM']=='1'
    assert value['QWEN38_LOCAL_MIN_MAX_TOKENS']=='16000'
    ds=server_env(conductor,{'base':'http://172.28.4.52:8831/v1','model':'DeepSeek-V4.1-Flash',
        'key_var':'GPU52_DEEPSEEK_API_KEY','endpoint':'deepseek'})
    assert ds['QWEN38_LOCAL_STREAM']=='0'


def test_shared_sandbox_arguments_are_provider_specific():
    assert sandbox.provider_container_args({})==[]
    args=sandbox.provider_container_args({'docker_network':'host','extra_body':{'reasoning_effort':'low'}})
    assert args[:2]==['--network','host']
    assert any(v.startswith('CLAUDE_CODE_EXTRA_BODY=') for v in args)
