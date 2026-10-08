"""Planning input and explicit storyboard-choice outcomes."""

class PlanningInputError(ValueError):
    pass


class StoryboardAwaitingChoice(PlanningInputError):
    """A sandbox-planned chapter has candidates and nobody has chosen one yet.

    Not a failure: the backend asks for a storyboard, a person picks the take, and planning resumes
    from the recorded choice.  It is its own type so a batch can tell "waiting for me" apart from
    "this chapter is broken".
    """
