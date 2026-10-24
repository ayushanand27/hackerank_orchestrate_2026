"""Make ``ghost_audit`` and the evaluation helpers importable from the tests.

Every test module calls :func:`ensure` before importing project code, so the suite runs both
via ``python3 -m unittest discover -s code/tests`` and as individual scripts."""
import os
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(TESTS)
REPO = os.path.dirname(CODE)
DATASET = os.path.join(REPO, "dataset")


def ensure():
    for p in (os.path.join(CODE, "evaluation"), CODE):
        if p not in sys.path:
            sys.path.insert(0, p)
