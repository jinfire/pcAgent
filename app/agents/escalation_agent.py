from __future__ import annotations

from app.config import Settings
from app.llm.openai_client import chat


ESCALATION_INSTRUCTIONS = """
You are the senior adjudicator for a personal software-agent team. Review the Architect, Coding Agent, and Reviewer
positions. Decide which conclusion is better supported, explain the decisive evidence, and give one concrete course
of action. Do not claim to inspect local state; use only the evidence provided. Respond concisely in the user's language.
""".strip()


def run_escalation_agent(question: str, evidence: str, settings: Settings) -> str:
    return chat(
        [{"role": "user", "content": f"Question:\n{question}\n\nEvidence:\n{evidence}"}],
        settings.escalation,
        ESCALATION_INSTRUCTIONS,
        role="escalation",
    )
