"""Pure net-delivery rates and observed time windows."""

def net_rates(samples: list[dict]) -> dict:
    result = {}
    for minutes in (15,60):
        if len(samples)<2:
            result[str(minutes)]=None
            continue
        last=samples[-1];cutoff=last['at']-minutes*60
        prior=[s for s in samples[:-1] if s['at']<=cutoff]
        first=prior[-1] if prior else samples[0]
        seconds=last['at']-first['at']
        result[str(minutes)]=({'delta':last['passed']-first['passed'],'minutes':round(seconds/60,1),
                              'per_hour':round((last['passed']-first['passed'])*3600/seconds,1),
                              'complete_window':minutes*60<=seconds<=minutes*72}
                             if seconds>=300 else None)
    return result


def net_windows(samples: list[dict]) -> dict:
    """Observed count changes per time bucket; missing history is not zero.

    Adjacent full buckets share their boundary observation. An incomplete
    bucket reports only its measured interval and is explicitly marked partial.
    Nothing before the first sample is reconstructed from file creation times.
    """
    if not samples:
        return {}
    result = {}
    latest = samples[-1]['at']
    for hours in (1, 6, 24):
        step = hours * 3600 / 12
        end = (latest // step + 1) * step
        rows = []
        for i in range(12):
            left = end - hours * 3600 + i * step
            right = left + step
            within = [s for s in samples if left < s['at'] <= right]
            previous = next((s for s in reversed(samples) if s['at'] <= left), None)
            observed = ([previous] if previous and left - previous['at'] <= 180 else []) + within
            valid = len(observed) >= 2
            complete = (valid and observed[0]['at'] <= left and right <= latest
                        and right - observed[-1]['at'] <= 180
                        and all(b['at'] - a['at'] <= 180 for a, b in zip(observed, observed[1:])))
            rows.append({'start': left, 'end': right,
                         'value': observed[-1]['passed'] - observed[0]['passed'] if valid else None,
                         'partial': not complete,
                         'observed_start': observed[0]['at'] if valid else None,
                         'observed_end': observed[-1]['at'] if valid else None})
        result[str(hours)] = {'hours': hours, 'step_minutes': int(step / 60), 'buckets': rows}
    return result


