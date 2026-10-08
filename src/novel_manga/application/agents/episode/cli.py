"""Normal prepare/run/status entrypoint for isolated episode production."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from novel_manga.application.production.common import parse_chapters
from novel_manga.application.agents.sandbox import load_config

from .context import create_context
from .flow import process_environment, read_status, run_episode
from .inputs import prepare_inputs


def parser():
    result = argparse.ArgumentParser(description='Prepare, run or inspect one sandbox agent per episode.')
    commands = result.add_subparsers(dest='command', required=True)
    for name, help_text in (('prepare', 'Copy the episode inputs only; no models or rendering.'),
                            ('run', 'Run the episode agent and its existing host media tools.'),
                            ('status', 'Read the existing episode run state.')):
        command = commands.add_parser(name, help=help_text)
        command.add_argument('--novel-dir', type=Path, required=True)
        selection = command.add_mutually_exclusive_group(required=True)
        selection.add_argument('--episode', help='Episode directory name or chapter number.')
        selection.add_argument('--chapters', help='Existing chapter range syntax, for example 1-3,7.')
        command.add_argument('--input-mode', choices=('auto', 'source', 'script'), default='auto')
        command.add_argument('--output-root', type=Path)
        command.add_argument('--project-root', type=Path,
                             help='Runtime project containing .env, configuration and media helpers.')
        command.add_argument('--target-seconds', type=float, nargs='+', metavar='SECONDS',
                             help='Source episode duration or minimum and maximum duration.')
        if name == 'run':
            command.add_argument('--config', type=Path, help='Task-specific sandbox configuration.')
            command.add_argument('--retry-issues', action='store_true',
                                 help='Retry a stopped episode, retaining media and video budgets; session policy follows config.')
            command.add_argument('--timeout', type=int,
                                 help='Total operation continuation time; defaults to sandbox configuration.')
    return result


def main(argv=None):
    arguments = parser().parse_args(argv)
    results = []
    try:
        episodes = parse_chapters(arguments.chapters) if arguments.chapters is not None else [arguments.episode]
        if not episodes:
            raise ValueError('choose at least one episode')
        target = arguments.target_seconds
        if target is not None:
            if len(target) not in (1, 2):
                raise ValueError('--target-seconds needs one duration or a minimum/maximum pair')
            target = target[0] if len(target) == 1 else tuple(target)
        overrides = ({'NOVEL_PROJECT_ROOT': str(arguments.project_root.expanduser().resolve())}
                     if arguments.project_root else {})
        with process_environment(overrides):
            for episode in episodes:
                try:
                    context = create_context(arguments.novel_dir, episode,
                        output_root=arguments.output_root, input_mode=arguments.input_mode,
                        target_seconds=target)
                    if arguments.command == 'prepare':
                        prepare_inputs(context)
                        result = {'episode': context.source_episode_dir.name, 'status': 'prepared',
                                  'input_mode': context.input_mode, 'run_dir': str(context.run_dir), 'exit_code': 0}
                    elif arguments.command == 'status':
                        result = read_status(context)
                    else:
                        options = {'retry_issues': True} if arguments.retry_issues else {}
                        if arguments.config:
                            options['config'] = load_config(arguments.config)
                        result = run_episode(context, timeout=arguments.timeout, **options)
                    results.append(result)
                except (OSError, ValueError, RuntimeError) as error:
                    results.append({'episode': str(episode), 'status': 'blocked', 'passed': False,
                                    'exit_code': 2, 'reason': f'{type(error).__name__}: {error}'})
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps({'command': arguments.command, 'episodes': results}, ensure_ascii=False, indent=2))
    return max((row.get('exit_code', 0) for row in results), default=0)


if __name__ == '__main__':
    raise SystemExit(main())
