from pathlib import Path
from types import SimpleNamespace

from novel_manga import qc


def test_silence_locations_distinguish_measurement_end_from_actual_gap(monkeypatch):
    stderr = ('[silencedetect] silence_start: 70.2\n'
              '[silencedetect] silence_end: 76.5315 | silence_duration: 6.3315\n'
              '[silencedetect] silence_start: 89\n'
              '[silencedetect] silence_end: 90 | silence_duration: 1\n')
    monkeypatch.setattr(qc.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stderr=stderr, returncode=0))
    checked = qc.inspect_silence(Path('existing.mp4'), 101.59, silent_outro_seconds=4)
    detail = checked['long_silence']['detail']
    assert checked['long_silence']['passed'] is False
    assert detail['end_seconds'] == 97.59
    assert detail['max_interval'] == {'start':70.2, 'end':76.5315, 'seconds':6.3315}
    assert detail['long_intervals'] == [detail['max_interval']]
