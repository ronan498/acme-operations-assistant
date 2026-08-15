#!/usr/bin/env python3
"""Extract Mermaid blocks from ARCHITECTURE.md and render PNGs via the
mermaid-cli docker image. Run on a decent network: `make diagrams`."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUT = ROOT / "docs"
OUT.mkdir(exist_ok=True)
NAMES = ["architecture-system", "flow-request", "flow-authz-gates", "flow-denial", "flow-skill"]

blocks = re.findall(r"```mermaid\n(.*?)```", (ROOT / "ARCHITECTURE.md").read_text(), re.S)
for name, block in zip(NAMES, blocks):
    (OUT / f"{name}.mmd").write_text(block)
    subprocess.run(
        ["docker", "run", "--rm", "-v", f"{OUT}:/data", "minlag/mermaid-cli",
         "-i", f"/data/{name}.mmd", "-o", f"/data/{name}.png",
         "-b", "transparent", "-w", "1600"],
        check=True,
    )
    print(f"docs/{name}.png")
