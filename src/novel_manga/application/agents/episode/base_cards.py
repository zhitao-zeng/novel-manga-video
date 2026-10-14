"""Shared existing base-card preparation for production and input compilation."""
import os
from pathlib import Path
from novel_manga.util import read_json


class AssetsPending(Exception):
    def __init__(self,paths):
        self.paths=paths
        super().__init__('Shared batch assets are still being prepared')


def wait_for_shared_assets(ctx,plan):
    from novel_manga.application.production.asset_preparation import queued_asset_paths
    manifest=os.environ.get('NOVEL_ASSET_PREPARATION_MANIFEST')
    if manifest:
        paths=queued_asset_paths(read_json(Path(manifest),[]),plan,ctx.source_episode_dir.parent,ctx.novel_dir,
            state=read_json(Path(manifest).with_name('state.json'),{}))
        if paths:raise AssetsPending(paths)


def ensure_cards(ctx, plan):
    """Build shared base cards under the book's existing lock, then copy them in."""
    from novel_manga.application.preparation import actions
    from novel_manga.application.agents.episode.inputs import _catalog, _references
    wait_for_shared_assets(ctx,plan)
    original=ctx.source_episode_dir.parent
    bible=read_json(original/'story_bible.json',{})
    shared={('character',c['name']):f'character_{i:03d}' for i,c in enumerate(bible.get('characters',[]),1)}
    shared.update({('location',s.split('：',1)[0].strip()):f'location_{i:03d}'
                   for i,s in enumerate(bible.get('locations',[]),1)})
    needed={r['asset_id'] for r in _references(plan)
            if shared.get((r.get('role'),r.get('name')))==r.get('asset_id')
            and not (ctx.novel_dir/r['path']).is_file() and not (original/r['path']).is_file()}
    if needed:
        actions.run_tool(['scripts/build_cards_thin.py','--novel-dir',str(original),
                          '--assets',','.join(sorted(needed)),'--tier','fast','--review'])
    _catalog(ctx,list(_references(plan)))
    actions.ensure_cards(ctx.episode_dir,plan)

