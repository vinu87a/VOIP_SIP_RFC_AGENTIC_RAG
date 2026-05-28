import json
import logging
import re
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import (
    AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage,
)
from langchain_core.tools import StructuredTool
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel, Field

from config import GROQ_API_KEY, GROQ_MODEL, OLLAMA_MODEL, OLLAMA_BASE_URL
from agent.prompts import SYSTEM_PROMPT
from agent.tools import execute_tool

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 14


# ── State schema ──────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # add_messages merges / deduplicates instead of blindly appending
    messages: Annotated[List[BaseMessage], add_messages]
    # Set to True by agent_node when Groq 429 triggers Ollama fallback
    groq_rate_limited: bool


# ── Pydantic input schemas (required by StructuredTool / ToolNode) ────────────

class _SearchRFCInput(BaseModel):
    query: str = Field(description=(
        "Precise natural-language query, e.g. 'Via header routing rules', "
        "'SRTP master key derivation', 'SDP offer answer re-INVITE'."
    ))
    rfc_filter: Optional[List[int]] = Field(default=None, description=(
        "Optional list of RFC numbers to restrict the search, e.g. [3261, 3665]."
    ))


class _SearchTraceInput(BaseModel):
    query: str = Field(description=(
        "What to look for in the trace, e.g. 'INVITE SDP offer media codecs', "
        "'488 Not Acceptable Here', 'BYE from alice', 'RTP streams SSRC'."
    ))


class _ReconstructCallFlowInput(BaseModel):
    call_id_filter: Optional[str] = Field(default=None, description=(
        "Optional Call-ID string to filter to a specific dialog. "
        "Omit to reconstruct all dialogs."
    ))


class _DiagnoseSIPErrorInput(BaseModel):
    response_code: int = Field(description="SIP response code, e.g. 401, 488, 503.")
    context: Optional[str] = Field(default=None, description=(
        "Optional context, e.g. 'received after REGISTER'."
    ))


class _CrossReferenceInput(BaseModel):
    observation: str = Field(description=(
        "What was observed in the trace, e.g. 'INVITE sent without a Contact header'."
    ))
    topic: Optional[str] = Field(default=None, description=(
        "Optional topic hint, e.g. 'authentication', 'SRTP key management'."
    ))


class _SearchDocsInput(BaseModel):
    query: str = Field(description=(
        "Natural-language search query for user-uploaded documents, "
        "e.g. 'SRTP key exchange procedure', 'codec negotiation rules'."
    ))
    doc_filter: Optional[List[str]] = Field(default=None, description=(
        "Optional list of doc_id strings to restrict the search to specific documents."
    ))


# ── LangChain StructuredTool factory ─────────────────────────────────────────

def _make_lc_tools(vector_store) -> List[StructuredTool]:
    """
    Wrap each existing execute_tool implementation in a LangChain StructuredTool.
    ToolNode requires BaseTool objects; StructuredTool is the most flexible subclass.
    Each function must return a string (ToolNode serialises it into a ToolMessage).
    """

    def search_rfc(query: str, rfc_filter: Optional[List[int]] = None) -> str:
        result = execute_tool("search_rfc", {"query": query, "rfc_filter": rfc_filter}, vector_store)
        return json.dumps(result)

    def search_trace(query: str) -> str:
        result = execute_tool("search_trace", {"query": query}, vector_store)
        return json.dumps(result)

    def reconstruct_call_flow(call_id_filter: Optional[str] = None) -> str:
        result = execute_tool("reconstruct_call_flow", {"call_id_filter": call_id_filter or ""}, vector_store)
        return json.dumps(result)

    def diagnose_sip_error(response_code: int, context: Optional[str] = None) -> str:
        result = execute_tool("diagnose_sip_error", {"response_code": response_code, "context": context or ""}, vector_store)
        return json.dumps(result)

    def cross_reference(observation: str, topic: Optional[str] = None) -> str:
        result = execute_tool("cross_reference", {"observation": observation, "topic": topic or ""}, vector_store)
        return json.dumps(result)

    def search_docs(query: str, doc_filter: Optional[List[str]] = None) -> str:
        result = execute_tool("search_docs", {"query": query, "doc_filter": doc_filter}, vector_store)
        return json.dumps(result)

    return [
        StructuredTool.from_function(
            func=search_rfc,
            name="search_rfc",
            description=(
                "Semantically search the RFC knowledge base (RFCs 3261, 3550, 3711, 3264, "
                "3665, 3428, 5630, 5922, 5923, 4572, 6904). Use for protocol rules, header "
                "definitions, normative requirements. Optionally restrict to specific RFCs."
            ),
            args_schema=_SearchRFCInput,
        ),
        StructuredTool.from_function(
            func=search_trace,
            name="search_trace",
            description=(
                "Semantically search the user-uploaded SIP trace. Finds SIP messages "
                "(INVITE, BYE, etc.) and RTP stream summaries. Only available when a "
                "trace file has been uploaded."
            ),
            args_schema=_SearchTraceInput,
        ),
        StructuredTool.from_function(
            func=reconstruct_call_flow,
            name="reconstruct_call_flow",
            description=(
                "Reconstruct the ordered SIP call flow from the uploaded trace, grouped "
                "by Call-ID and sorted by CSeq. Use to see the full dialog sequence: "
                "INVITE → 100 Trying → 180 Ringing → 200 OK → ACK → BYE."
            ),
            args_schema=_ReconstructCallFlowInput,
        ),
        StructuredTool.from_function(
            func=diagnose_sip_error,
            name="diagnose_sip_error",
            description=(
                "Retrieve the RFC definition, meaning, and common causes of a SIP "
                "response code. Use whenever a 4xx, 5xx, or 6xx code appears."
            ),
            args_schema=_DiagnoseSIPErrorInput,
        ),
        StructuredTool.from_function(
            func=cross_reference,
            name="cross_reference",
            description=(
                "Given an observation from the SIP trace, retrieve the governing RFC "
                "rule to determine whether the behaviour is RFC-compliant."
            ),
            args_schema=_CrossReferenceInput,
        ),
        StructuredTool.from_function(
            func=search_docs,
            name="search_docs",
            description=(
                "Semantically search user-uploaded documents (PDF, DOCX, HTML, text, URLs). "
                "Use when the user references content from their uploaded documents. "
                "Only available when documents have been uploaded via the sidebar."
            ),
            args_schema=_SearchDocsInput,
        ),
    ]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_rate_limit(exc: Exception) -> bool:
    """Return True when Groq raises an HTTP 429 rate-limit error."""
    msg = str(exc).lower()
    return "429" in msg or "rate_limit" in msg or "rate limit" in msg or "ratelimit" in msg


# ── Graph factory ─────────────────────────────────────────────────────────────

def _build_graph(vector_store):
    """
    Compile a LangGraph StateGraph using the prebuilt ToolNode.

    Primary LLM : Groq (fast cloud inference)
    Fallback LLM: Ollama gemma4:e4b — activated automatically on Groq HTTP 429

    Nodes
    -----
    agent — ChatGroq (+ Ollama fallback) with all 5 tools bound via bind_tools()
    tools — langgraph.prebuilt.ToolNode

    Edges
    -----
    START → agent
    agent → tools  (tools_condition: AIMessage has tool_calls)
    agent → END    (tools_condition: no tool_calls / stop)
    tools → agent  (always — loop back for next reasoning step)
    """
    lc_tools  = _make_lc_tools(vector_store)
    tool_node = ToolNode(lc_tools)

    # ── Primary: Groq ─────────────────────────────────────────────────────────
    groq_with_tools = ChatGroq(
        model=GROQ_MODEL,
        api_key=GROQ_API_KEY,
        temperature=0.1,
        max_tokens=4096,
    ).bind_tools(lc_tools)

    groq_plain = ChatGroq(
        model=GROQ_MODEL,
        api_key=GROQ_API_KEY,
        temperature=0.1,
        max_tokens=4096,
    )

    # ── Fallback: Ollama ──────────────────────────────────────────────────────
    ollama_with_tools = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.1,
    ).bind_tools(lc_tools)

    ollama_plain = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0.1,
    )

    def agent_node(state: AgentState) -> Dict:
        agent_turns = sum(1 for m in state["messages"] if isinstance(m, AIMessage))
        is_final    = agent_turns >= MAX_ITERATIONS

        invoke_msgs = state["messages"]
        if is_final:
            logger.warning("MAX_ITERATIONS reached — forcing final answer without tools")
            invoke_msgs = state["messages"] + [HumanMessage(
                content=(
                    "Based on all the information retrieved above, "
                    "provide your final, complete answer now."
                )
            )]

        primary  = groq_plain  if is_final else groq_with_tools
        fallback = ollama_plain if is_final else ollama_with_tools

        # 1. Try Groq ──────────────────────────────────────────────────────────
        try:
            response = primary.invoke(invoke_msgs)
            return {"messages": [response]}

        except Exception as exc:
            # 2a. Groq rate-limited → try Ollama ───────────────────────────────
            if _is_rate_limit(exc):
                logger.warning(
                    "Groq 429 rate limit — falling back to Ollama %s", OLLAMA_MODEL
                )
                try:
                    response = fallback.invoke(invoke_msgs)
                    return {"messages": [response], "groq_rate_limited": True}
                except Exception as exc2:
                    logger.error("Ollama fallback failed: %s", exc2)
                    return {
                        "messages": [AIMessage(content=f"Agent error: {exc2}")],
                        "groq_rate_limited": True,
                    }

            # 2b. Other Groq error → retry Groq without tools ──────────────────
            logger.error("LLM call failed (%s) — retrying Groq without tools", exc)
            try:
                response = groq_plain.invoke(invoke_msgs)
                return {"messages": [response]}
            except Exception as exc2:
                # Plain Groq also 429 → fall back to Ollama plain
                if _is_rate_limit(exc2):
                    logger.warning(
                        "Groq 429 on plain retry — falling back to Ollama %s", OLLAMA_MODEL
                    )
                    try:
                        response = ollama_plain.invoke(invoke_msgs)
                        return {"messages": [response], "groq_rate_limited": True}
                    except Exception as exc3:
                        return {
                            "messages": [AIMessage(content=f"Agent error: {exc3}")],
                            "groq_rate_limited": True,
                        }
                logger.error("Plain Groq fallback also failed: %s", exc2)
                return {"messages": [AIMessage(content=f"Agent error: {exc2}")]}

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)         # ← langgraph.prebuilt.ToolNode

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)   # → "tools" or END
    graph.add_edge("tools", "agent")           # always loop back

    return graph.compile()


# ── Answer sanitiser ─────────────────────────────────────────────────────────

_TOOL_LEAK = re.compile(
    r"\b(?:search_rfc|search_trace|reconstruct_call_flow"
    r"|diagnose_sip_error|cross_reference|search_docs)\b"
)


def _sanitize_answer(text: str) -> str:
    """
    Remove any line or sentence that leaks an internal tool name.

    List items (bullet / numbered) that mention a tool are dropped entirely.
    Prose sentences that mention a tool are also dropped.
    Multiple blank lines produced by the removals are collapsed.
    """
    clean_lines: List[str] = []
    for line in text.split("\n"):
        if not _TOOL_LEAK.search(line):
            clean_lines.append(line)
            continue

        # Drop entire bullet / numbered-list items
        if re.match(r"^\s*(?:[-*•]|\d+\.)\s", line):
            continue

        # For prose lines, drop individual sentences that contain a tool name
        sentences = re.split(r"(?<=[.!?])\s+", line)
        kept = [s for s in sentences if not _TOOL_LEAK.search(s)]
        if kept:
            clean_lines.append(" ".join(kept))

    result = "\n".join(clean_lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


# ── Public orchestrator class ─────────────────────────────────────────────────

class AgentOrchestrator:
    """
    Wrapper around the compiled LangGraph.

    run() returns the same dict as before so app.py is unchanged:
      answer          — model's final natural-language response
      reasoning_trace — [{tool, args, result_preview}] parsed from ToolMessages
      call_flow       — reconstruct_call_flow result dict (or None)
    """

    def __init__(self, vector_store) -> None:
        self._vs    = vector_store
        self._graph = _build_graph(vector_store)

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(
        self,
        query: str,
        trace_active: bool = False,
        docs_info: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        trace_count = self._vs.trace_count() if trace_active else 0
        if trace_active and trace_count > 0:
            trace_status = (
                f"\n\n## Trace Status\n"
                f"A SIP trace is currently loaded and contains **{trace_count} messages**.\n\n"
                f"**MANDATORY trace workflow — follow every time:**\n"
                f"1. Call `reconstruct_call_flow` first to see the full dialog sequence.\n"
                f"2. Call `search_trace` with targeted queries to retrieve the specific "
                f"messages relevant to the user's question "
                f"(e.g., the INVITE, the error response, the SDP bodies).\n"
                f"3. Base your entire answer on the actual header values, SDP fields, "
                f"IP addresses, response codes, and message content you retrieved from "
                f"the trace — not on generic protocol descriptions.\n\n"
                f"**Forbidden responses when a trace is loaded:**\n"
                f"- Do NOT say 'we need to examine the trace' — YOU must examine it "
                f"using the tools and report the findings directly.\n"
                f"- Do NOT answer from RFC theory alone when the answer exists in the trace.\n"
                f"- Do NOT stop after a single tool call if the trace message you need "
                f"requires multiple search_trace calls to retrieve.\n"
                f"- Do NOT write a 'Diagnostic Approach' section or describe which tools "
                f"you plan to call. Call them immediately and report the actual findings.\n"
                f"- Do NOT mention tool names (search_rfc, search_trace, "
                f"reconstruct_call_flow, diagnose_sip_error, cross_reference) anywhere "
                f"in your final answer."
            )
        else:
            trace_status = (
                "\n\n## Trace Status\n"
                "No SIP trace is currently loaded. Answer from the RFC knowledge base only. "
                "If the user asks about a trace, politely explain they need to upload one "
                "using the sidebar uploader."
            )

        if docs_info:
            doc_list = "\n".join(
                f"  - {d['doc_name']} (doc_id: {d['doc_id']}, type: {d['doc_type']}, {d['chunk_count']} chunks)"
                for d in docs_info
            )
            doc_status = (
                f"\n\n## User Documents\n"
                f"{len(docs_info)} document(s) are currently available for search:\n{doc_list}\n\n"
                f"Use `search_docs` to retrieve relevant content from these documents when the "
                f"user asks about them. Cite the document name and chunk in your answer."
            )
        else:
            doc_status = (
                "\n\n## User Documents\n"
                "No user documents are currently uploaded."
            )

        final_state = self._graph.invoke(
            {
                "messages": [
                    SystemMessage(content=SYSTEM_PROMPT + trace_status + doc_status),
                    HumanMessage(content=query),
                ],
                "groq_rate_limited": False,
            },
            config={"recursion_limit": MAX_ITERATIONS * 2 + 5},
        )

        messages = final_state["messages"]

        # ── Parse reasoning_trace from message history ────────────────────────
        # Build a lookup: tool_call_id → {name, args} from AIMessages
        tc_lookup: Dict[str, Dict] = {}
        for msg in messages:
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    tc_lookup[tc["id"]] = {"name": tc["name"], "args": tc["args"]}

        reasoning_trace: List[Dict] = []
        call_flow_result: Optional[Dict] = None

        for msg in messages:
            if not isinstance(msg, ToolMessage):
                continue
            tc_info   = tc_lookup.get(msg.tool_call_id, {})
            tool_name = tc_info.get("name") or msg.name or "unknown"
            tool_args = tc_info.get("args", {})

            reasoning_trace.append({
                "tool":           tool_name,
                "args":           tool_args,
                "result_preview": (msg.content or "")[:500],
            })

            if tool_name == "reconstruct_call_flow":
                try:
                    data = json.loads(msg.content)
                    if "dialogs" in data:
                        call_flow_result = data
                except (json.JSONDecodeError, TypeError):
                    pass

        last_msg = messages[-1]
        raw_answer = last_msg.content if hasattr(last_msg, "content") else ""
        answer     = _sanitize_answer(raw_answer)

        rate_limited = final_state.get("groq_rate_limited", False)
        return {
            "answer":            answer,
            "reasoning_trace":   reasoning_trace,
            "call_flow":         call_flow_result,
            "groq_rate_limited": rate_limited,
            "ollama_model":      OLLAMA_MODEL if rate_limited else "",
        }

    # ── Graph visualisation ───────────────────────────────────────────────────

    def get_graph_png(self, path: str) -> None:
        """Save the LangGraph diagram to *path* as a PNG."""
        png_bytes = self._graph.get_graph().draw_mermaid_png()
        with open(path, "wb") as fh:
            fh.write(png_bytes)
        logger.info("LangGraph diagram saved to %s", path)

    def get_graph_mermaid(self) -> str:
        return self._graph.get_graph().draw_mermaid()
