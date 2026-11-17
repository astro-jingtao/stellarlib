"""Skills shipped with the stellarlib package.

Run ``python -m stellarlib.skills`` to print the skills directory's
location and the deployment guide pointer.
"""

from pathlib import Path

root = Path(__file__).parent.resolve()

__all__ = ["root"]
