"""Use Codex Luna for one ordinary episode-agent command and its Python helpers."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[3]
ADAPTER = Path(__file__).resolve().parent

def main(argv=None):
    paths = [str(ADAPTER), str(ROOT/'src')]
    sys.path[:0] = paths
    # This process and its children only; the usual launcher remains unchanged.
    os.environ['CODEX_LUNA_ADAPTER'] = '1'
    os.environ['PYTHONPATH'] = os.pathsep.join(paths + ([os.environ['PYTHONPATH']] if os.environ.get('PYTHONPATH') else []))
    import luna_transport
    import luna_session
    luna_transport.install()
    luna_session.install()
    from novel_manga.application.agents.episode.cli import main as episode_main
    return episode_main(argv)

if __name__ == '__main__':
    raise SystemExit(main())
