"""Feed unresolved posture handoffs back to the existing stage patch loop."""
from novel_manga.application.packing.posture import fill
from novel_manga.planning.issues import PlanningCode, PlanningIssue


def check_draft(directory, shots, segments, *, previous=None, ask=None):
    states = fill(directory, script={'shots': shots}, segments=segments,
                  previous=previous, ask=ask, write=False)
    labels = {int(s.get('index', i)): s.get('label') for i, s in enumerate(shots, 1)}
    issues = [PlanningIssue(PlanningCode.POSTURE_CONTINUITY, row['detail'],
                           stage=labels[row['index']], field='start_state') for row in states['issues']]
    return states, issues
