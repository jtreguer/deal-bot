"""Normalization and validation of extracted specs against the model knowledge base."""

from __future__ import annotations

import re

from deal_bot.config import GpuPattern, ModelInfo
from deal_bot.models import ExtractedSpec, Validation

_INTEL_CORE = re.compile(r"\bi([3579])\s*-?\s*(1[0-4]\d{3})\s*(hx|h|u|p)?\b")
_CORE_ULTRA = re.compile(r"\b(?:core\s*)?(?:ultra\s*|u)([579])\s*-?\s*(\d{3})\s*(hx|h|u|v)?\b")


class KnowledgeBase:
    def __init__(self, models: dict[str, ModelInfo], gpu_patterns: list[GpuPattern]):
        self.models = models
        self._aliases = [(k, re.compile(a, re.I)) for k, m in models.items() for a in m.aliases]
        self._gpus = [(p, re.compile(p.pattern, re.I)) for p in gpu_patterns]

    def match_model(self, text: str) -> str | None:
        for key, rx in self._aliases:
            if rx.search(text):
                return key
        return None

    def mentions_any_model(self, text: str) -> bool:
        return self.match_model(text) is not None

    def normalize_gpu(self, text: str | None) -> tuple[str | None, str | None]:
        """Return (canonical key, warning). Unknown strings give (None, None)."""
        if not text:
            return None, None
        for p, rx in self._gpus:
            if rx.search(text):
                return p.key, p.warn
        return None, None

    @staticmethod
    def normalize_cpu(text: str | None) -> tuple[str | None, str | None]:
        """Return (canonical key, class), e.g. ('i7-13800h', 'i7') or ('core-ultra-7-165h', 'ultra-7')."""
        if not text:
            return None, None
        t = text.lower()
        if m := _INTEL_CORE.search(t):
            return f"i{m.group(1)}-{m.group(2)}{m.group(3) or 'h'}", f"i{m.group(1)}"
        if m := _CORE_ULTRA.search(t):
            return f"core-ultra-{m.group(1)}-{m.group(2)}{m.group(3) or 'h'}", f"ultra-{m.group(1)}"
        if m := re.search(r"\bi([3579])\b", t):
            return None, f"i{m.group(1)}"
        if m := re.search(r"\bultra\s*([579])\b|\bu([579])\b", t):
            return None, f"ultra-{m.group(1) or m.group(2)}"
        return None, None

    def validate(self, spec: ExtractedSpec, title: str) -> Validation:
        v = Validation()
        v.model_key = self.match_model(spec.model or "") or self.match_model(title)
        v.cpu_key, v.cpu_class = self.normalize_cpu(spec.cpu)
        v.gpu_key, warn = self.normalize_gpu(spec.gpu)
        if warn:
            v.warnings.append(warn)
        if spec.gpu and not v.gpu_key:
            v.warnings.append(f"unrecognised GPU name {spec.gpu!r}")

        info = self.models.get(v.model_key or "")
        if not info:
            return v

        def invalid(field: str, message: str) -> None:
            v.invalid_fields.append(field)
            v.invalid.append(message)

        if v.cpu_key and v.cpu_key not in info.cpus:
            invalid("cpu", f"CPU {v.cpu_key} was never offered in the {info.label}")
        if v.gpu_key and v.gpu_key not in info.gpus:
            invalid("gpu", f"GPU {spec.gpu!r} was never offered in the {info.label}")
        if spec.ram_gb and spec.ram_gb not in info.ram_gb:
            invalid("ram", f"{spec.ram_gb} GB RAM is not a factory option (RAM is soldered)")
        return v
