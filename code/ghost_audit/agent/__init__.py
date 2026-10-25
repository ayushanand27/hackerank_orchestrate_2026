"""Optional LLM review agent.

The deterministic pipeline (``ghost_audit.engine``) decides every row of ``output.csv``. This
package adds a Claude tool-calling *reviewer* on top of it: for each case the model investigates
the evidence through read-only tools, then submits a structured review. Code validates every
review against the evidence; the model can confirm a row or flag it for human review, but it can
never change a status, a step or an evidence id (a deterministic gate, see ``investigator.py``).

Off by default; enable with ``python3 code/main.py --agent review`` and ``ANTHROPIC_API_KEY``.
"""
from .investigator import CaseInvestigator, ReviewResult
from .llm_client import AnthropicClient, ScriptedClient, UsageMeter

__all__ = ["CaseInvestigator", "ReviewResult", "AnthropicClient", "ScriptedClient", "UsageMeter"]
