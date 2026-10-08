#!/usr/bin/env python3
"""Prepare/apply a reversible patch inside the existing DS or Qwen container.

Only the Anthropic handler changes. Shared model files, credentials, launch
arguments and episode state are not inputs to this installer.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import re


OLD_MERGE = '        self._merge_inline_system = self._detect_merge_inline_system(chat_template)'
NEW_MERGE = '        self._merge_inline_system = False  # novel-cache-order: keep chronological system roles'
SUPER_CALL = '        super().__init__('
QWEN_SYSTEM = "{{- '<|im_start|>system\\n' + render_content(message['content'], false, true) + '<|im_end|>\\n' }}"
QWEN_GUARD = re.compile(r'''\{\{-?\s*raise_exception\(['"]System message must be at the beginning\.['"]\)\s*-?\}\}''')


def template_loader(path: Path) -> str:
    return ('        if chat_template is None:\n'
            '            from pathlib import Path\n'
            f'            chat_template = Path({str(path)!r}).read_text(encoding="utf-8")\n'
            '            if online_renderer is not None:\n'
            '                from copy import copy\n'
            '                online_renderer = copy(online_renderer)\n'
            '                online_renderer.chat_template = chat_template\n')


def patch_serving(source: str, template_path: Path | None = None) -> str:
    ast.parse(source)
    loader = template_loader(template_path) if template_path is not None else ''
    if NEW_MERGE in source and OLD_MERGE not in source:
        if loader and loader not in source:
            raise ValueError('adapter already patched with a different template selection')
        return source
    if source.count(OLD_MERGE) != 1:
        raise ValueError('unknown adapter layout: expected one inline-system merge setting')
    revised = source.replace(OLD_MERGE, NEW_MERGE)
    if loader:
        if revised.count(SUPER_CALL) != 1:
            raise ValueError('unknown adapter constructor: expected one superclass initialization')
        revised = revised.replace(SUPER_CALL, loader+SUPER_CALL)
    ast.parse(revised)
    return revised


def patch_qwen_template(template: str) -> str:
    if QWEN_SYSTEM in template and not QWEN_GUARD.search(template):
        return template
    revised, count = QWEN_GUARD.subn(lambda _: QWEN_SYSTEM, template)
    if count != 1:
        raise ValueError(f'unknown Qwen template: expected one system-position guard, got {count}')
    return revised


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.cache-order-tmp')
    temporary.write_text(value, encoding='utf-8')
    if path.exists():
        temporary.chmod(path.stat().st_mode & 0o777)
    os.replace(temporary, path)


def install_source(source: Path, patched: str, state: Path) -> None:
    before, after = state/'before.py', state/'after.py'
    current = source.read_text(encoding='utf-8')
    if before.exists():
        if not after.exists() or after.read_text(encoding='utf-8') != patched:
            raise ValueError('prepared patch changed; keep the existing rollback evidence')
        if current == patched:
            return
        if current != before.read_text(encoding='utf-8'):
            raise ValueError('adapter source changed since preparation; re-inspect before deployment')
    else:
        write_text(before, current)
        write_text(after, patched)
    write_text(source, patched)


def rollback_source(source: Path, state: Path) -> None:
    original = (state/'before.py').read_text(encoding='utf-8')
    current = source.read_text(encoding='utf-8')
    if current == original:
        return
    if current != (state/'after.py').read_text(encoding='utf-8'):
        raise ValueError('adapter changed after installation; rollback would overwrite another edit')
    write_text(source, original)


def prepare(backend: str, state: Path, model_dir: Path) -> dict:
    spec = importlib.util.find_spec('vllm.entrypoints.anthropic.serving')
    if spec is None or not spec.origin:
        raise ValueError('installed Anthropic adapter not found')
    source = Path(spec.origin)
    original = source.read_text(encoding='utf-8')
    template_path = None
    if backend == 'qwen':
        from transformers import AutoTokenizer
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=True)
        template = patch_qwen_template(tokenizer.get_chat_template())
        ImmutableSandboxedEnvironment().parse(template)
        template_path = state/'qwen-inline-system.jinja'
        write_text(template_path, template)
    revised = patch_serving(original, template_path)
    before, after = state/'before.py', state/'after.py'
    if before.exists():
        saved_before, saved_after = before.read_text(), after.read_text()
        if original not in (saved_before, saved_after) or revised != saved_after:
            raise ValueError('adapter/patch changed since the existing preparation')
    else:
        if NEW_MERGE in original:
            raise ValueError('already-patched adapter has no rollback snapshot')
        write_text(before, original)
        write_text(after, revised)
    record = {'backend': backend, 'source': str(source), 'template': str(template_path) if template_path else None,
              'state_dir': str(state), 'policy': 'chronological-system-v1'}
    write_text(state/'prepared.json', json.dumps(record, indent=2)+'\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'apply', 'rollback'))
    parser.add_argument('--backend', choices=('ds', 'qwen'))
    parser.add_argument('--state-dir', type=Path, default=Path('/opt/novel-llm-cache-order'))
    parser.add_argument('--model-dir', type=Path, default=Path('/root/models'))
    args = parser.parse_args()
    if args.action == 'prepare':
        if not args.backend:
            parser.error('prepare requires --backend')
        record = prepare(args.backend, args.state_dir, args.model_dir)
    else:
        record = json.loads((args.state_dir/'prepared.json').read_text())
        source = Path(record['source'])
        if args.action == 'apply':
            install_source(source, (args.state_dir/'after.py').read_text(), args.state_dir)
        else:
            rollback_source(source, args.state_dir)
    print(json.dumps({'action': args.action, **record}, ensure_ascii=False))


if __name__ == '__main__':
    main()
