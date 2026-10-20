"""§5.7 headless batch mode — the agent-driven render lane.

`--batch` replaces the interactive preview gate with a machine gate:

  1. Cost report: per-project estimate of paid-API spend BEFORE rendering.
     ElevenLabs pricing (2026-10, turbo_v2_5 ≈ $0.00005/char ≈ 5¢/1k chars;
     eleven_v3 ≈ $0.0001/char). fal OmniHuman ≈ $0.05/s of video, only on
     lipsync slides. Everything local (say/SAPI/espeak-ng, ffmpeg, backdrops) = $0.
  2. Spend ceiling: auto-proceed only while the RUNNING TOTAL stays under
     the declared ceiling. A project that would push the batch over the
     ceiling HALTS the batch (the spend law) and maps to the Yellow gate:
     the render is skipped, the batch reports which project crossed it, and
     a human approves before that spend moves.

Config: `batch: {"spend_ceiling_usd": 2.00}` in any project's slides.py, or
`--spend-ceiling` on the CLI. Env override: RENDERMILL_SPEND_CEILING.
Default ceiling: $1.00 (deliberately low; raise it explicitly).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# 2026-10 list prices, USD per character of narration text.
ELEVEN_PRICE_PER_CHAR = {
    "eleven_turbo_v2_5": 0.00005,
    "eleven_multilingual_v2": 0.00010,
    "eleven_v3": 0.00010,
}
DEFAULT_PRICE_PER_CHAR = 0.00010
FAL_OMNIHUMAN_USD_PER_S = 0.05  # lipsync video seconds, paid lane only

DEFAULT_CEILING_USD = 1.00


class CeilingExceeded(RuntimeError):
    """The next render would push the batch over its spend ceiling.

    Maps to the Yellow gate: halt, report, wait for human approval.
    The caller must NOT render the offending project.
    """


@dataclass
class CostReport:
    project: str
    provider: str
    chars: int
    est_usd_tts: float
    lipsync_s: float
    est_usd_lipsync: float

    @property
    def est_usd(self) -> float:
        return self.est_usd_tts + self.est_usd_lipsync


def resolve_ceiling(cfg_ceiling: Optional[float], cli_ceiling: Optional[float]) -> float:
    """CLI flag wins > project config > env > default."""
    if cli_ceiling is not None:
        return float(cli_ceiling)
    if cfg_ceiling is not None:
        return float(cfg_ceiling)
    env = os.environ.get("RENDERMILL_SPEND_CEILING")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return DEFAULT_CEILING_USD


def estimate_project(slides: List[Dict[str, Any]], config: Dict[str, Any],
                     project_name: str = "project") -> CostReport:
    """Estimate paid-API spend for one project. Local providers (say/SAPI/espeak-ng)
    and free-mode renders estimate $0. Uses the same provider-resolution
    logic as the render itself; safe to call pre-render (no network)."""
    from . import tts as tts_mod  # local import: avoid cycles in tests

    tts_cfg = config.get("tts") or {}
    chars = sum(len(s.get("narration") or "") for s in slides)
    lipsync_s = 0.0
    if config.get("mode") == "free":
        provider = "local(free)"
        est_tts = 0.0
    else:
        try:
            provider = tts_mod.resolve_provider(tts_cfg)
        except RuntimeError:
            provider = "local(fallback)"
        if provider == "local":
            est_tts = 0.0
        else:
            model = tts_cfg.get("model", "eleven_turbo_v2_5")
            price = ELEVEN_PRICE_PER_CHAR.get(model, DEFAULT_PRICE_PER_CHAR)
            est_tts = chars * price
            for s in slides:
                if s.get("lipsync"):
                    # ~1s of video per 150 chars of narration, plus pad.
                    lipsync_s += len(s.get("narration") or "") / 150.0 + 0.15
    return CostReport(project=project_name, provider=provider, chars=chars,
                      est_usd_tts=round(est_tts, 4),
                      lipsync_s=round(lipsync_s, 2),
                      est_usd_lipsync=round(lipsync_s * FAL_OMNIHUMAN_USD_PER_S, 4))


def format_report(reports: List[CostReport], ceiling: float,
                  spent: float) -> str:
    lines = ["[batch] cost report:"]
    total = 0.0
    for r in reports:
        total += r.est_usd
        lines.append(
            f"  {r.project:30s} {r.chars:6d} chars via {r.provider:12s} "
            f"≈ ${r.est_usd:.4f}"
            + (f" (+${r.est_usd_lipsync:.4f} lipsync)" if r.est_usd_lipsync else "")
        )
    lines.append(f"[batch] total ≈ ${total:.4f}   ceiling ${ceiling:.2f}   "
                 f"spent ${spent:.4f}")
    return "\n".join(lines)


def check_ceiling(ceiling_usd: float, spent_usd: float,
                  next_report: CostReport) -> None:
    """the spend law: auto-proceed only under the ceiling. Halt (raise)
    when the next project would cross it."""
    if spent_usd + next_report.est_usd > ceiling_usd:
        raise CeilingExceeded(
            f"[batch][yellow-gate] HALT: rendering "
            f"'{next_report.project}' (≈ ${next_report.est_usd:.4f}) would "
            f"push the batch to ${spent_usd + next_report.est_usd:.4f}, over "
            f"the ${ceiling_usd:.2f} spend ceiling. Approve the spend " 
            f"or raise the ceiling to proceed."
        )


def load_project(path: Path):
    """Load a project directory's slides+config (shared by the CLI)."""
    from . import config as cfg_mod
    return cfg_mod.load(Path(path))


def run_batch(project_dirs: List[Path], *, ceiling: float,
              render_fn=None, out_override=None, yes=False) -> Dict[str, Any]:
    """Render a queue of projects under the ceiling. `render_fn(project_dir)`
    performs one render (the CLI injects cmd_render's core). Projects are
    rendered in order; a CeilingExceeded halts the queue with the status.
    Free-mode projects don't count against the ceiling ($0)."""
    from . import config as cfg_mod

    projects = []
    for pd in project_dirs:
        proj = cfg_mod.load(Path(pd))
        reports = estimate_project(proj.slides, proj.config,
                                   project_name=Path(pd).name)
        projects.append((proj, reports))

    spent = 0.0
    rendered: List[str] = []
    skipped: List[str] = []
    for proj, rep in projects:
        if rep.est_usd > 0:
            check_ceiling(ceiling, spent, rep)
        if render_fn is not None:
            render_fn(proj)
        spent += rep.est_usd
        rendered.append(rep.project)
    return {
        "ceiling_usd": ceiling,
        "spent_usd": round(spent, 4),
        "rendered": rendered,
        "skipped": skipped,
        "reports": [rep.est_usd for _, rep in projects],
    }