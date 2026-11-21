import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
frontend_files = list(Path(PROJECT_ROOT / "frontend").rglob("*"))
for f in frontend_files:
    if f.is_file() and f.suffix in [".html", ".js"]:
        content = f.read_text(encoding="utf-8", errors="ignore")
        if "simulated" in content.lower() or "simulation" in content.lower():
            # Replace simulation wording with real
            new_content = content.replace("Simulated", "Real").replace("simulated", "real")
            f.write_text(new_content, encoding="utf-8")
print("Frontend updated to remove simulation branding.")
