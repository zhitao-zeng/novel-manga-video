"""Count shared-resource queue intervals separately from execution time."""
from __future__ import annotations

import fcntl
import itertools
import json
import os
from pathlib import Path
import subprocess
import time

_IDS=itertools.count()


class RequestDeadline:
    def __init__(self,seconds):
        self.at=time.monotonic()+seconds

    def remaining(self):
        return self.at-time.monotonic()

    def waited(self,seconds):
        self.at+=seconds


class QueueWait:
    def __init__(self,resource,*,enabled=True):
        raw=os.environ.get('NOVEL_RESOURCE_WAIT_FILE') if enabled else None
        self.path=Path(raw) if raw else None
        self.ident=f'{os.getpid()}-{next(_IDS)}'
        self.resource=resource
        self.started=None
        self.seconds=0.0

    def _write(self,**event):
        if self.path is None:return
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open('a') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX)
            stream.write(json.dumps({'id':self.ident,'pid':os.getpid(),'resource':self.resource,**event})+'\n')
            stream.flush()

    def begin(self):
        if self.started is None:
            self.started=time.monotonic()
            self._write(start=self.started)

    def finish(self):
        if self.started is not None:
            end=time.monotonic()
            self.seconds=max(0,end-self.started)
            self._write(end=end)
            self.started=None

    def __enter__(self):return self
    def __exit__(self,*unused):self.finish()


def wait_seconds(path,*,since,until=None):
    """Union overlapping waits; never credit the same wall second twice."""
    until=time.monotonic() if until is None else until
    path=Path(path)
    try:
        with path.open() as stream:
            fcntl.flock(stream,fcntl.LOCK_SH)
            rows=[json.loads(line) for line in stream if line.strip()]
    except FileNotFoundError:return 0.0
    spans={}
    for row in rows:spans.setdefault(row['id'],{}).update(row)
    intervals=[]
    for row in spans.values():
        if 'start' not in row:continue
        if 'end' not in row and row.get('pid') and not Path(f"/proc/{row['pid']}").exists():continue
        start=max(since,row['start']);end=min(until,row.get('end',until))
        if end>start:intervals.append((start,end))
    total=0.0;edge=since
    for start,end in sorted(intervals):
        total+=max(0,end-max(edge,start));edge=max(edge,end)
    return total


def active_seconds(start):
    end=time.monotonic()
    path=os.environ.get('NOVEL_RESOURCE_WAIT_FILE')
    return max(0,end-start-(wait_seconds(path,since=start,until=end) if path else 0))


def run_with_active_timeout(command,*,timeout,**kwargs):
    """A planner helper may queue between bounded model calls."""
    path=os.environ.get('NOVEL_RESOURCE_WAIT_FILE')
    if timeout is None or not path:
        return subprocess.run(command,timeout=timeout,**kwargs)
    start=time.monotonic()
    with subprocess.Popen(command,**kwargs) as child:
        while True:
            left=timeout-(time.monotonic()-start)+wait_seconds(path,since=start)
            if left<=0:
                child.kill();child.wait()
                raise subprocess.TimeoutExpired(command,timeout)
            try:
                code=child.wait(timeout=min(1,left))
            except subprocess.TimeoutExpired:continue
            return subprocess.CompletedProcess(command,code)
