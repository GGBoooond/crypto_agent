"""Prompt builder with frozen snapshot layers and per-regime skill routing."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_MAX_INJECTED_SKILLS = 3
_BLOCKED_SKILL_STAGES = {"sunset", "discarded"}


@dataclass
class FrozenPromptSnapshot:
    """Cached static layer reused across calls until source files change."""

    built_at: str
    static_prompt: str
    user_memory: str
    system_memory: str
    skills_index: str
    skill_payloads: List[Dict[str, Any]] = field(default_factory=list)
    source_signature: Tuple[Tuple[str, float], ...] = field(default_factory=tuple)


class PromptBuilder:
    """Build static / regime / dynamic prompt layers for strategy LLM calls.

    The output is intentionally split into:
        - system message: identity + decision contract
        - user message: stitched layered context (static + regime + dynamic)
    so the BaseAIStrategy can hand a ready-to-send messages list to the SDK.
    """

    DEFAULT_SYSTEM_PROMPT = (
        "You are a conservative quantitative trading assistant. "
        "Follow the layered context strictly; if the [TRIGGER] section conflicts with "
        "[REGIME] or [SKILLS], prefer the safer choice. Always reply with a single JSON "
        "object that matches the [DECISION_SCHEMA] section."
    )

    def __init__(self, memory_dir: str = "memory"):
        self.memory_dir = Path(memory_dir)
        self.snapshot: Optional[FrozenPromptSnapshot] = None
        self.last_regime: Optional[str] = None
        self.regime_layer: str = ""
        self.last_injected_skill_ids: List[str] = []

    # ------------------------------------------------------------------
    # Static snapshot layer (MEMORY/USER/SKILLS index)
    # ------------------------------------------------------------------
    def _read_text(self, path: Path) -> str:
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except Exception:
                return ""
        return ""

    def _gather_skill_files(self) -> List[Path]:
        skills_dir = self.memory_dir / "skills"
        if not skills_dir.exists():
            return []
        return sorted(skills_dir.glob("*/SKILL.md"))

    def _current_source_signature(self) -> Tuple[Tuple[str, float], ...]:
        sources = [
            self.memory_dir / "MEMORY.md",
            self.memory_dir / "USER.md",
            self.memory_dir / "skill_lifecycle.json",
        ]
        sources.extend(self._gather_skill_files())
        sig: List[Tuple[str, float]] = []
        for path in sources:
            try:
                if path.exists():
                    sig.append((str(path), path.stat().st_mtime))
                else:
                    sig.append((str(path), 0.0))
            except Exception:
                sig.append((str(path), 0.0))
        return tuple(sig)

    def _parse_skill(self, path: Path) -> Optional[Dict[str, Any]]:
        """Parse YAML-ish frontmatter + body from a SKILL.md file.

        Skills are intentionally tolerant: malformed frontmatter is skipped
        rather than crashing the whole prompt build.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return None

        frontmatter: Dict[str, Any] = {}
        body = text
        if text.startswith("---"):
            end_idx = text.find("---", 3)
            if end_idx > 0:
                fm_text = text[3:end_idx].strip()
                body = text[end_idx + 3 :].lstrip("\n")
                frontmatter = self._parse_simple_yaml(fm_text)

        quant_meta = self._extract_quant_metadata(frontmatter)
        skill_name = str(frontmatter.get("name") or path.parent.name)
        return {
            "id": skill_name,
            "name": skill_name,
            "description": frontmatter.get("description", ""),
            "regimes": quant_meta.get("regime", []),
            "trigger_types": quant_meta.get("trigger_types", []),
            "tape_signatures": quant_meta.get("tape_signatures", []),
            "profit_factor": self._safe_float(quant_meta.get("profit_factor")),
            "body": body.strip(),
            "path": str(path),
        }

    def _parse_simple_yaml(self, fm_text: str) -> Dict[str, Any]:
        """Tiny YAML subset parser sufficient for our SKILL.md frontmatter.

        Supports key: value, nested two-level mappings, and inline list literals
        like ``regime: [strong_trend_up, ranging]``.
        """
        result: Dict[str, Any] = {}
        stack: List[Tuple[int, Dict[str, Any]]] = [(-1, result)]
        for raw_line in fm_text.splitlines():
            if not raw_line.strip() or raw_line.strip().startswith("#"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip(" "))
            line = raw_line.strip()
            while stack and indent <= stack[-1][0]:
                stack.pop()
            if not stack:
                stack = [(-1, result)]
            parent = stack[-1][1]

            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value == "":
                child: Dict[str, Any] = {}
                parent[key] = child
                stack.append((indent, child))
                continue
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                items = [v.strip().strip("\"'") for v in inner.split(",") if v.strip()]
                parent[key] = items
            else:
                parent[key] = value.strip("\"'")
        return result

    @staticmethod
    def _extract_quant_metadata(frontmatter: Dict[str, Any]) -> Dict[str, Any]:
        meta = frontmatter.get("metadata") or {}
        if not isinstance(meta, dict):
            return {}
        quant = meta.get("quant") or {}
        if not isinstance(quant, dict):
            return {}
        result: Dict[str, Any] = {}
        for key in ("regime", "trigger_types", "tape_signatures"):
            result[key] = PromptBuilder._coerce_string_list(quant.get(key))
        if "profit_factor" in quant:
            result["profit_factor"] = quant.get("profit_factor")
        return result

    @staticmethod
    def _extract_regime_tags(frontmatter: Dict[str, Any]) -> List[str]:
        return PromptBuilder._extract_quant_metadata(frontmatter).get("regime", [])

    @staticmethod
    def _coerce_string_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value]
        return []

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _load_skill_lifecycle(self) -> Dict[str, Dict[str, Any]]:
        path = self.memory_dir / "skill_lifecycle.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def rebuild_static_snapshot(self, force: bool = False) -> FrozenPromptSnapshot:
        signature = self._current_source_signature()
        if (
            not force
            and self.snapshot is not None
            and self.snapshot.source_signature == signature
        ):
            return self.snapshot

        mem = self._read_text(self.memory_dir / "MEMORY.md")
        usr = self._read_text(self.memory_dir / "USER.md")
        skill_paths = self._gather_skill_files()
        lifecycle = self._load_skill_lifecycle()
        skill_payloads: List[Dict[str, Any]] = []
        for sp in skill_paths:
            parsed = self._parse_skill(sp)
            if parsed is not None and not self._is_skill_blocked(parsed, lifecycle):
                skill_payloads.append(parsed)

        skills_index = "\n".join(
            f"- {sk['name']} (regime={','.join(sk['regimes']) or 'any'}): {sk['description']}"
            for sk in skill_payloads
        )

        self.snapshot = FrozenPromptSnapshot(
            built_at=datetime.utcnow().isoformat(),
            static_prompt=self.DEFAULT_SYSTEM_PROMPT,
            user_memory=usr,
            system_memory=mem,
            skills_index=skills_index,
            skill_payloads=skill_payloads,
            source_signature=signature,
        )
        return self.snapshot

    @staticmethod
    def _is_skill_blocked(
        skill: Dict[str, Any], lifecycle: Dict[str, Dict[str, Any]]
    ) -> bool:
        state = lifecycle.get(str(skill.get("id") or skill.get("name"))) or {}
        stage = str(state.get("stage", "")).lower()
        return stage in _BLOCKED_SKILL_STAGES

    # ------------------------------------------------------------------
    # Regime / skill selection
    # ------------------------------------------------------------------
    def update_regime_layer(
        self, regime: str, extra: Optional[str] = None
    ) -> None:
        if regime != self.last_regime or extra is not None:
            self.last_regime = regime
            self.regime_layer = f"Regime: {regime}"
            if extra:
                self.regime_layer += f"\n{extra}"

    def _select_relevant_skills(
        self,
        regime: str,
        strategy_payload: Optional[Dict[str, Any]] = None,
        kline_summary: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if self.snapshot is None:
            return []
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for skill in self.snapshot.skill_payloads:
            score = self._score_skill(skill, regime, strategy_payload, kline_summary)
            if score > 0:
                scored.append((score, skill))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [skill for _, skill in scored[:_MAX_INJECTED_SKILLS]]

    def _score_skill(
        self,
        skill: Dict[str, Any],
        regime: str,
        strategy_payload: Optional[Dict[str, Any]],
        kline_summary: Optional[Dict[str, Any]],
    ) -> float:
        score = self._regime_score(skill, regime)
        score += self._trigger_score(skill, strategy_payload)
        score += self._tape_score(skill, kline_summary)
        profit_factor = skill.get("profit_factor")
        if isinstance(profit_factor, (int, float)) and profit_factor > 1:
            score += min(float(profit_factor), 3.0) * 0.1
        return score

    @staticmethod
    def _regime_score(skill: Dict[str, Any], regime: str) -> float:
        regimes = skill.get("regimes") or []
        if not regimes:
            return 0.2
        return 2.0 if regime in regimes else 0.0

    @staticmethod
    def _trigger_score(
        skill: Dict[str, Any], strategy_payload: Optional[Dict[str, Any]]
    ) -> float:
        trigger_types = skill.get("trigger_types") or []
        if not trigger_types or not strategy_payload:
            return 0.0
        trigger_text = str(strategy_payload.get("trigger_reason", "")).lower()
        return 1.0 if any(str(item).lower() in trigger_text for item in trigger_types) else 0.0

    @staticmethod
    def _tape_score(
        skill: Dict[str, Any], kline_summary: Optional[Dict[str, Any]]
    ) -> float:
        signatures = skill.get("tape_signatures") or []
        if not signatures or not kline_summary:
            return 0.0
        current = str(kline_summary.get("tape_signature") or "")
        if not current:
            return 0.0
        best = max(
            SequenceMatcher(None, current.lower(), str(item).lower()).ratio()
            for item in signatures
        )
        return best

    @staticmethod
    def _summarise_skill_body(body: str, max_lines: int = 12) -> str:
        if not body:
            return ""
        lines = [line for line in body.splitlines() if line.strip()]
        return "\n".join(lines[:max_lines])

    # ------------------------------------------------------------------
    # Strategy payload rendering
    # ------------------------------------------------------------------
    @staticmethod
    def _format_indicators(indicators: Dict[str, Any]) -> str:
        if not indicators:
            return "n/a"
        return ", ".join(f"{k}={v}" for k, v in indicators.items())

    @staticmethod
    def _format_tape(tape: Optional[List[str]]) -> str:
        if not tape:
            return "n/a"
        return "\n".join(tape)

    def _render_trigger(self, payload: Optional[Dict[str, Any]]) -> str:
        if not payload:
            return "n/a"
        parts: List[str] = []
        for key in ("mode", "signal_dir", "trigger_reason"):
            if key in payload:
                parts.append(f"{key}={payload[key]}")
        if "indicators" in payload and isinstance(payload["indicators"], dict):
            parts.append(f"indicators=[{self._format_indicators(payload['indicators'])}]")
        for key in ("ref_tp", "ref_sl"):
            if key in payload and payload[key] is not None:
                parts.append(f"{key}={payload[key]}")
        return ", ".join(parts) if parts else "n/a"

    @staticmethod
    def _default_decision_schema(payload: Optional[Dict[str, Any]]) -> str:
        mode = (payload or {}).get("mode", "open")
        if mode == "position_check":
            return (
                '{\n'
                '  "action": "ADJUST | HOLD",\n'
                '  "reason": "string",\n'
                '  "tp_price": number,\n'
                '  "sl_price": number\n'
                '}'
            )
        return (
            '{\n'
            '  "action": "EXECUTE_LONG | EXECUTE_SHORT | WAIT | REJECT | EXECUTE | REJECT",\n'
            '  "fine_regime": "string",\n'
            '  "confidence": "HIGH | MEDIUM | LOW",\n'
            '  "confidence_breakdown": {"trend": 0.0, "momentum": 0.0, "support_resistance": 0.0},\n'
            '  "key_observations": ["string"],\n'
            '  "reason": "string",\n'
            '  "tp_price": number,\n'
            '  "sl_price": number\n'
            '}'
        )

    # ------------------------------------------------------------------
    # Public build APIs
    # ------------------------------------------------------------------
    def build(
        self,
        symbol: str,
        regime: str,
        kline_summary: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        strategy_payload: Optional[Dict[str, Any]] = None,
        regime_extra: Optional[str] = None,
    ) -> str:
        """Backward compatible string-prompt builder used for trace/log layer."""
        messages = self.build_messages(
            symbol=symbol,
            regime=regime,
            kline_summary=kline_summary,
            position=position,
            strategy_payload=strategy_payload,
            regime_extra=regime_extra,
        )
        return "\n\n".join(m["content"] for m in messages)

    def build_messages(
        self,
        symbol: str,
        regime: str,
        kline_summary: Dict[str, Any],
        position: Optional[Dict[str, Any]],
        strategy_payload: Optional[Dict[str, Any]] = None,
        regime_extra: Optional[str] = None,
        system_role_override: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Produce ready-to-send OpenAI-compatible messages list.

        返回 3 条消息（system + static-prefix user + dynamic-suffix user），
        利用 DeepSeek / OpenAI 兼容实现的自动前缀缓存（Automatic Prefix Caching）。
        静态前缀在 MEMORY/USER/SKILLS/regime/symbol 不变时字节完全一致，
        服务端自动命中缓存，仅对动态后缀计费，可节省 50%+ prompt token。
        """
        snapshot = self.rebuild_static_snapshot()
        self.update_regime_layer(regime, regime_extra)
        skills = self._select_relevant_skills(regime, strategy_payload, kline_summary)
        self.last_injected_skill_ids = [
            str(sk.get("id") or sk.get("name")) for sk in skills
        ]

        skill_section = ""
        if skills:
            blocks = []
            for sk in skills:
                blocks.append(
                    f"### {sk['name']} (regime={','.join(sk.get('regimes') or []) or 'any'})\n"
                    f"{self._summarise_skill_body(sk.get('body', ''))}"
                )
            skill_section = "\n\n".join(blocks)
        else:
            skill_section = "(no skill matches the current regime)"

        recent_tape = self._format_tape(kline_summary.get("last_n_compact"))
        indicators_inline = self._format_indicators(kline_summary.get("indicators") or {})
        tape_signature = kline_summary.get("tape_signature") or "n/a"
        volume_anomaly = "yes" if kline_summary.get("volume_anomaly") else "no"

        trigger_text = self._render_trigger(strategy_payload)
        decision_schema = (strategy_payload or {}).get("decision_schema") or \
            self._default_decision_schema(strategy_payload)

        position_text = self._format_position(position)

        user_instruction = (strategy_payload or {}).get("user_instruction")
        extra_context = (strategy_payload or {}).get("extra_context")
        role_constraints = (strategy_payload or {}).get("role_constraints")
        instruction_block = (
            f"[USER_INSTRUCTION]\n{user_instruction}\n\n" if user_instruction else ""
        )
        role_block = (
            f"[ROLE_CONSTRAINTS]\n{self._format_extra_context(role_constraints)}\n\n"
            if role_constraints else ""
        )
        extra_block = (
            f"[EXTRA_CONTEXT]\n{self._format_extra_context(extra_context)}\n\n"
            if extra_context else ""
        )

        # ── 静态前缀：仅在 MEMORY/USER/SKILL/symbol/regime 变更时变化 ──
        # 保持此字符串在多次调用间字节一致，是命中前缀缓存的关键。
        static_prefix = (
            f"[USER]\n{snapshot.user_memory or '(empty)'}\n\n"
            f"[MEMORY]\n{snapshot.system_memory or '(empty)'}\n\n"
            f"[SKILLS_INDEX]\n{snapshot.skills_index or '(empty)'}\n\n"
            f"[SKILLS]\n{skill_section}\n\n"
            f"[CONTEXT]\nsymbol={symbol}\n\n"
            f"[DECISION_SCHEMA]\n{decision_schema}\n\n"
            f"{role_block}"
            f"{instruction_block}"
        )

        # ── 动态后缀：每轮根据行情 / 持仓 / 触发条件变化 ──
        dynamic_suffix = (
            f"[REGIME]\n{self.regime_layer}\n\n"
            f"[KLINE_SUMMARY]\n"
            f"summary: {kline_summary.get('summary', 'n/a')}\n"
            f"tape_signature: {tape_signature}\n"
            f"volume_anomaly: {volume_anomaly}\n\n"
            f"[INDICATORS]\n{indicators_inline}\n\n"
            f"[RECENT_TAPE]\n{recent_tape}\n\n"
            f"[POSITION]\n{position_text}\n\n"
            f"{extra_block}"
            f"[TRIGGER]\n{trigger_text}\n\n"
            "Reply with the JSON only."
        )

        return [
            {
                "role": "system",
                "content": system_role_override or snapshot.static_prompt,
            },
            {"role": "user", "content": static_prefix},
            {"role": "user", "content": dynamic_suffix},
        ]

    @staticmethod
    def _format_position(position: Optional[Dict[str, Any]]) -> str:
        if not position:
            return "no position"
        keys = ("side", "size", "entry_price", "unrealized_pnl", "tp_price", "sl_price")
        items = []
        for key in keys:
            if key in position and position[key] is not None:
                items.append(f"{key}={position[key]}")
        if not items:
            return "position present (no detail)"
        return ", ".join(items)

    @staticmethod
    def _format_extra_context(extra: Any) -> str:
        """Render extra_context (dict / list / scalar) as readable lines.

        Lists become bullet rows; dicts become ``key: value`` lines so that
        nested structures (e.g. ``btc_trend / support / resistance``) keep
        their semantic shape inside the prompt.
        """
        if extra is None:
            return ""
        if isinstance(extra, dict):
            lines: List[str] = []
            for key, value in extra.items():
                if isinstance(value, (list, tuple)):
                    rendered = ", ".join(str(item) for item in value) or "(empty)"
                    lines.append(f"{key}: [{rendered}]")
                elif isinstance(value, dict):
                    inner = ", ".join(f"{k}={v}" for k, v in value.items())
                    lines.append(f"{key}: {{{inner}}}")
                else:
                    lines.append(f"{key}: {value}")
            return "\n".join(lines) if lines else "(empty)"
        if isinstance(extra, (list, tuple)):
            return "\n".join(f"- {item}" for item in extra) or "(empty)"
        return str(extra)

    @staticmethod
    def estimate_tokens(messages: List[Dict[str, str]]) -> int:
        text = "\n".join(m.get("content", "") for m in messages)
        if not text:
            return 0
        return max(1, int(len(text) / 4))


# Backwards-compatible export used by other modules / tests.
_REGIME_PATTERN = re.compile(r"^[a-z_]+$")
