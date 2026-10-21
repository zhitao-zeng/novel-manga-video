from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import json
import shutil
import urllib.request

root = Path(__file__).resolve().parent
items = json.loads((root / 'apt-downloads.json').read_text())
dest = root / 'apt-debs'
dest.mkdir(exist_ok=True)


def download(item):
    target = dest / item['name']
    if target.is_file() and target.stat().st_size == item['size']:
        return
    partial = target.with_name(target.name + '.partial')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(item['url'], timeout=30) as response, partial.open('wb') as output:
        shutil.copyfileobj(response, output)
    if partial.stat().st_size != item['size']:
        raise RuntimeError('Incomplete package: ' + item['name'])
    partial.replace(target)


with ThreadPoolExecutor(max_workers=6) as executor:
    futures = [executor.submit(download, item) for item in items]
    for future in as_completed(futures):
        future.result()
print(json.dumps({'downloaded_packages': len(items),
                  'bytes': sum(item['size'] for item in items)}))
