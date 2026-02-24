"""Placeholder analyzer that returns dummy results."""

from pathlib import Path

from ..models import AnalysisResult
from .base import ClipAnalyzer


class StubAnalyzer(ClipAnalyzer):
    """Returns placeholder analysis results. Replace with a real analyzer later."""

    def analyze(self, clip_path: str, keyframes: list[Path]) -> AnalysisResult:
        return AnalysisResult(
            agent="stub",
            map_name=None,
            tags=["unanalyzed"],
            summary=f"Stub analysis ({len(keyframes)} keyframes extracted)",
            confidence=0.0,
        )
