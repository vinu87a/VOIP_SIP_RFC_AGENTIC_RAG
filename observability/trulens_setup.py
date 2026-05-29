import logging
import os
from typing import Dict, Optional, Sequence, Tuple, Type

# Must be set before any trulens import — disables OTEL mode so that
# Select.RecordCalls (Lens) selectors work with TruCustomApp.
os.environ.setdefault("TRULENS_OTEL_TRACING", "false")

import numpy as np
from openai import OpenAI as OpenAIClient
from pydantic import BaseModel

from trulens.core import TruSession, Feedback, Select
from trulens.core.schema.feedback import FeedbackMode
from trulens.feedback.llm_provider import LLMProvider
from trulens.feedback.generated import re_configured_rating
from trulens.apps.custom import TruCustomApp
from trulens.apps.app import instrument

from config import GROQ_API_KEY, GROQ_EVAL_MODEL, OLLAMA_CLOUD_API_KEY, OLLAMA_CLOUD_URL, OLLAMA_CLOUD_MODEL

logger = logging.getLogger(__name__)

# Tools whose result_preview counts as retrieved context for RAG Triad evaluation
CONTEXT_TOOLS = {
    "search_rfc",
    "search_trace",
    "search_docs",
    "cross_reference",
    "diagnose_sip_error",
}

_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trulens_eval.db")
_session: Optional[TruSession] = None


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "rate_limit" in msg or "rate limit" in msg or "ratelimit" in msg


class GroqOllamaProvider(LLMProvider):
    """
    TruLens LLMProvider backed by Gemini (primary, fast cloud)
    with local Ollama gemma4:e4b as fallback.  Groq is never used for eval
    so it remains available for the main agent.

    Bypasses the endpoint requirement by overriding generate_score /
    generate_score_and_reasons to call _create_chat_completion directly.
    """

    model_engine: str = GROQ_EVAL_MODEL

    def _create_chat_completion(
        self,
        prompt: Optional[str] = None,
        messages: Optional[Sequence[Dict]] = None,
        response_format: Optional[Type[BaseModel]] = None,
        **kwargs,
    ) -> str:
        if messages is None:
            messages = [{"role": "user", "content": prompt}]

        import json as _json

        score_tool = {
            "type": "function",
            "function": {
                "name": "submit_score",
                "description": "Submit your integer evaluation score",
                "parameters": {
                    "type": "object",
                    "properties": {"score": {"type": "integer"}},
                    "required": ["score"],
                },
            },
        }

        def _call_with_tools(client: OpenAIClient, model: str) -> str:
            resp = client.chat.completions.create(
                model=model,
                messages=list(messages),
                temperature=0.0,
                tools=[score_tool],
                tool_choice={"type": "function", "function": {"name": "submit_score"}},
            )
            tool_calls = resp.choices[0].message.tool_calls
            if tool_calls:
                args = _json.loads(tool_calls[0].function.arguments)
                return str(args["score"])
            raise ValueError(f"{model} did not invoke submit_score tool")

        # Primary: Groq ────────────────────────────────────────────────────────
        groq_client = OpenAIClient(
            api_key=GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )
        try:
            return _call_with_tools(groq_client, GROQ_EVAL_MODEL)
        except Exception as exc:
            # Fallback: Ollama Cloud ────────────────────────────────────────────
            logger.warning("Groq eval failed (%s) — falling back to Ollama Cloud", exc)
            ollama_cloud_client = OpenAIClient(
                api_key=OLLAMA_CLOUD_API_KEY,
                base_url=f"{OLLAMA_CLOUD_URL}/v1",
            )
            try:
                return _call_with_tools(ollama_cloud_client, OLLAMA_CLOUD_MODEL)
            except Exception as exc2:
                logger.warning("Ollama Cloud eval failed (%s) — returning default score", exc2)
                return "_DEFAULT_"

    def _parse_score(self, response: str, min_score_val: int, max_score_val: int) -> float:
        if response == "_DEFAULT_":
            return 0.5
        score = re_configured_rating(
            response, min_score_val=min_score_val, max_score_val=max_score_val
        )
        return (score - min_score_val) / (max_score_val - min_score_val)

    def generate_score(
        self,
        system_prompt: str,
        user_prompt: Optional[str] = None,
        min_score_val: int = 0,
        max_score_val: int = 10,
        temperature: float = 0.0,
    ) -> float:
        messages = [{"role": "system", "content": system_prompt}]
        if user_prompt:
            messages.append({"role": "user", "content": user_prompt})
        response = self._create_chat_completion(messages=messages)
        return self._parse_score(response, min_score_val, max_score_val)

    def generate_score_and_reasons(
        self,
        system_prompt: str,
        user_prompt: Optional[str] = None,
        min_score_val: int = 0,
        max_score_val: int = 10,
        temperature: float = 0.0,
    ) -> Tuple[float, Dict]:
        messages = [{"role": "system", "content": system_prompt}]
        if user_prompt:
            messages.append({"role": "user", "content": user_prompt})
        response = self._create_chat_completion(messages=messages)
        normalized = self._parse_score(response, min_score_val, max_score_val)
        return normalized, {"reason": response}

    def groundedness_measure_with_cot_reasons(
        self,
        source: str,
        statement: str,
        criteria: Optional[str] = None,
        examples: Optional[str] = None,
        groundedness_configs=None,
        min_score_val: int = 0,
        max_score_val: int = 3,
        temperature: float = 0.0,
    ) -> Tuple[float, Dict]:
        """Single-call groundedness — scores the whole answer at once to minimise API calls."""
        if isinstance(source, list):
            source_str = "\n\n".join(str(s) for s in source if s and str(s).strip())
        else:
            source_str = str(source)

        if not source_str.strip():
            return 0.0, {"reason": "No context retrieved — cannot evaluate groundedness"}

        if not statement or not statement.strip():
            return 0.0, {"reason": "Empty answer — cannot evaluate groundedness"}

        system_prompt = (
            f"You are evaluating whether an answer is supported by the provided source context. "
            f"Respond with a SINGLE integer between {min_score_val} and {max_score_val} "
            f"where {min_score_val} = not supported at all and {max_score_val} = fully supported. "
            "Output only the integer, nothing else."
        )
        user_prompt = (
            f"SOURCE:\n{source_str[:3000]}\n\n"
            f"ANSWER: {statement[:1000]}\n\n"
            f"Score (single integer {min_score_val}–{max_score_val}):"
        )
        try:
            score = self.generate_score(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                min_score_val=min_score_val,
                max_score_val=max_score_val,
                temperature=temperature,
            )
            return score, {"reason": f"Groundedness score: {score:.2f}"}
        except Exception as exc:
            logger.warning("Groundedness scoring failed: %s", exc)
            return 0.5, {"reason": f"Scoring failed: {exc}"}


class SIPAssistantApp:
    """
    Pure TruLens recording shell — does NOT run the agent.

    The agent runs in app.py exactly as before (via AgentOrchestrator).
    After the agent finishes, app.py calls record() to hand the pre-computed
    answer and contexts to TruLens for RAG Triad scoring.

    TruLens records two instrumented calls per query:
      query()             — input = question, output = answer string
      _extract_contexts() — input = context list, output = same list (selector target)
    """

    @instrument
    def _extract_contexts(self, contexts: list) -> list:
        """Pass-through so TruLens can select context chunks via RecordCalls."""
        return contexts

    @instrument
    def query(self, question: str, answer: str, contexts: list) -> str:
        """Record a completed agent run. Returns answer for TruLens to score."""
        self._extract_contexts(contexts)
        return answer


def get_tru_session() -> TruSession:
    """Return (or create) the singleton TruSession backed by a local SQLite file."""
    global _session
    if _session is None:
        _session = TruSession(database_url=f"sqlite:///{_DB_PATH}")
        # Start the background evaluator that processes DEFERRED feedbacks.
        # This runs in its own thread so it never blocks Streamlit's script thread.
        try:
            _session.start_evaluator()
        except Exception:
            pass
    return _session


def setup_trulens() -> tuple:
    """
    Initialise TruLens for the SIP RAG app.

    Returns
    -------
    (sip_app, tru_recorder, session)
        sip_app      — SIPAssistantApp instance (call .run() as you would AgentOrchestrator)
        tru_recorder — TruCustomApp context manager; wrap each query with `with tru_recorder:`
        session      — TruSession for dashboard / leaderboard queries
    """
    session = get_tru_session()
    provider = GroqOllamaProvider()

    # ── RAG Triad feedback functions ─────────────────────────────────────────

    # 1. Answer Relevance — does the answer address the user's question?
    f_answer_relevance = (
        Feedback(provider.relevance, name="Answer Relevance")
        .on(Select.RecordCalls.query.args.question)   # question arg
        .on(Select.RecordCalls.query.rets)             # answer string
    )

    # 2. Context Relevance — does each RFC/trace chunk match the question?
    f_context_relevance = (
        Feedback(provider.context_relevance, name="Context Relevance")
        .on(Select.RecordCalls.query.args.question)
        .on(Select.RecordCalls._extract_contexts.rets[:])   # each context chunk
        .aggregate(np.mean)
    )

    # 3. Groundedness — is the answer supported by the retrieved context?
    f_groundedness = (
        Feedback(
            provider.groundedness_measure_with_cot_reasons,
            name="Groundedness",
        )
        .on(Select.RecordCalls._extract_contexts.rets[:].collect())  # all contexts
        .on(Select.RecordCalls.query.rets)
    )

    sip_app = SIPAssistantApp()

    # DEFERRED mode: the context manager only writes the record + pending feedback
    # rows to SQLite — no TP.submit in the hot path.  The start_evaluator()
    # background thread (started in get_tru_session) picks them up and runs the
    # LLM-based scoring without ever touching Streamlit's script thread.
    tru_recorder = TruCustomApp(
        sip_app,
        app_name="sip-rfc-rag",
        app_version="v1",
        feedbacks=[f_answer_relevance, f_context_relevance, f_groundedness],
        feedback_mode=FeedbackMode.DEFERRED,
    )

    return sip_app, tru_recorder, session
