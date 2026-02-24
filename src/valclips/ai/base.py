"""Abstract base class for clip analyzers."""

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import AnalysisResult


class ClipAnalyzer(ABC):
    """Interface for AI-powered clip analysis."""

    @abstractmethod
    def analyze(self, clip_path: str, keyframes: list[Path]) -> AnalysisResult:
        """Analyze a clip given its path and extracted keyframes.

        Args:
            clip_path: Path to the video file.
            keyframes: Paths to extracted keyframe images.

        Returns:
            AnalysisResult with agent name, detected map, tags, and summary.
        """
        ...
