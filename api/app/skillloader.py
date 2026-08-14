"""Skill loading — two-tier, per the progressive-disclosure pattern.

Tier 1 (always in the model's context): the frontmatter — name,
description, when_to_use — rendered into the Skill's tool schema.
Tier 2 (loaded only on invocation): the SKILL.md body, which becomes the
instructions for the Skill's own reasoning call.

The agent pays for the body only when the Skill actually fires.
"""

from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent / "skills"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    when_to_use: str
    version: str
    allowed_tools: tuple[str, ...]
    body: str

    @property
    def tool_description(self) -> str:
        """Tier 1 — what the agent sees before invoking."""
        return f"{self.description} When to use: {self.when_to_use} (v{self.version})"


def load_skill(dirname: str) -> Skill:
    text = (SKILLS_DIR / dirname / "SKILL.md").read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"skill {dirname}: SKILL.md must start with frontmatter")
    _, frontmatter, body = text.split("---", 2)

    fields: dict[str, str] = {}
    for line in frontmatter.strip().splitlines():
        key, _, value = line.partition(":")
        if key.strip() and value.strip():
            fields[key.strip()] = value.strip()

    for required in ("name", "description", "when_to_use", "version"):
        if required not in fields:
            raise ValueError(f"skill {dirname}: frontmatter missing '{required}'")

    return Skill(
        name=fields["name"],
        description=fields["description"],
        when_to_use=fields["when_to_use"],
        version=fields["version"],
        allowed_tools=tuple(
            t.strip() for t in fields.get("allowed_tools", "").split(",") if t.strip()
        ),
        body=body.strip(),
    )
