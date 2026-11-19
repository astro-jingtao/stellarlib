"""Print the deployed skills root (python -m stellarlib.skills)."""

from stellarlib.skills import root

print(root)
print(f"Deployment guide: {root / 'SKILL-DEPLOY.md'}")
