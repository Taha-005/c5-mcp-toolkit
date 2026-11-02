"""MCP Host + Client: an OpenAI Agents SDK agent (running on Groq) that uses our MCP server.

Round trip shown live in the terminal:
    user -> Agent (LLM picks a tool) -> MCP Client -> MCP Server (server.py) -> Open-Meteo / plans file
         <- result back up the same chain <- final answer

Run:  uv run agent.py
"""

import ast
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import Agent, OpenAIChatCompletionsModel, Runner, set_tracing_disabled
from agents.mcp import MCPServerStdio

load_dotenv()
set_tracing_disabled(True)  # SDK tracing uploads to OpenAI; we use Groq, so we print the round trip locally instead.

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
MODEL_NAME = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
SERVER_PATH = Path(__file__).parent / "server.py"

CYAN, YELLOW, GREEN, RED, DIM, RESET = "\033[36m", "\033[33m", "\033[32m", "\033[31m", "\033[2m", "\033[0m"
ERROR_CODES = ("INVALID_INPUT", "CITY_NOT_FOUND", "SERVICE_UNAVAILABLE", "NO_SUITABLE_DAY", "STORAGE_ERROR")

INSTRUCTIONS = f"""You are a weather outing planner. Today is {date.today():%A %Y-%m-%d}.
For relative dates ("next Saturday", "this weekend"), work out the exact YYYY-MM-DD from today's date and weekday,
and state the weekday in your answer. If a relative date is ambiguous, ask the user to confirm the exact date.
Choose tools as follows:
- Questions about the weather or forecast only: call get_weather_forecast. Never invent weather data.
- The user names a specific date and wants an outing saved: call save_plan.
- The user wants you to choose the best day for an activity: call plan_outing (it checks the weather AND saves).
Allowed activities: hiking, beach, picnic, sightseeing. Map similar requests (e.g. "walk in the mountains" -> hiking).
If a tool returns an error such as [CITY_NOT_FOUND] or [INVALID_INPUT], explain it in plain words and suggest a fix.
Do not retry the same failing call. Keep answers short and concrete (dates, temperatures, rain %)."""


def _tool_text(output: object) -> str:
    """Extract the text payload from an MCP tool result (dict, list of dicts, or their string form)."""
    if isinstance(output, dict):
        return str(output.get("text", output))
    if isinstance(output, list):
        return " ".join(_tool_text(o) for o in output)
    text = str(output)
    if text.startswith(("{'type'", "[{'type'")):
        try:
            return _tool_text(ast.literal_eval(text))
        except (ValueError, SyntaxError):
            pass
    return text


def _short(text: object, limit: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + " ..."


async def main() -> None:
    os.system("")  # enables ANSI colours in the Windows console
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        sys.exit("GROQ_API_KEY is missing. Add it to the .env file.")

    model = OpenAIChatCompletionsModel(
        model=MODEL_NAME,
        openai_client=AsyncOpenAI(base_url=GROQ_BASE_URL, api_key=api_key),
    )

    # MCP Client: launches server.py as a subprocess and talks to it over STDIO.
    async with MCPServerStdio(
        name="weather-outing-planner",
        params={"command": sys.executable, "args": [str(SERVER_PATH)]},
        cache_tools_list=True,
        client_session_timeout_seconds=30,  # Open-Meteo calls can exceed the 5 s default
    ) as server:
        tools = await server.list_tools()
        print(f"{GREEN}Connected to MCP server. Discovered {len(tools)} tools:{RESET}")
        for t in tools:
            print(f"  - {t.name}: {(t.description or '').strip().splitlines()[0]}")
        print(f"{DIM}Model: {MODEL_NAME} via Groq. Type 'quit' to exit.{RESET}\n")

        agent = Agent(name="Outing Planner", instructions=INSTRUCTIONS, model=model, mcp_servers=[server])
        history: list = []

        while True:
            try:
                user = input(f"{CYAN}You > {RESET}").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if user.lower() in {"quit", "exit", "q"}:
                break
            if not user:
                continue

            result = Runner.run_streamed(agent, input=history + [{"role": "user", "content": user}])
            try:
                async for event in result.stream_events():
                    if event.type != "run_item_stream_event":
                        continue
                    item = event.item
                    if item.type == "tool_call_item":
                        raw = item.raw_item
                        print(f"{YELLOW}  [agent -> MCP client -> server] {raw.name}({raw.arguments}){RESET}")
                    elif item.type == "tool_call_output_item":
                        out = _tool_text(item.output)
                        color = RED if any(code in out for code in ERROR_CODES) else YELLOW
                        print(f"{color}  [server -> MCP client -> agent] {_short(out)}{RESET}")
                print(f"{GREEN}Agent > {RESET}{result.final_output}\n")
                history = result.to_input_list()
            except Exception as e:  # e.g. Groq rate limit or a malformed tool call from the model
                print(f"{RED}Run failed: {type(e).__name__}: {_short(e)}{RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())