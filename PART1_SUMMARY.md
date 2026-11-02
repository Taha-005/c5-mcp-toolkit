
# Part 1: Summary of MCP and the Fetch MCP Server

**Article:** Ksenia Se, "What Is MCP? Model Context Protocol in Agentic AI, Explained," Turing Post / Hugging Face, March 2025.
**Server explored:** Fetch MCP Server, official `modelcontextprotocol/servers` repository (`mcp-server-fetch`).

## Key MCP concepts from the article

The article argues that LLMs are capable but isolated from data, and that connecting them to tools used to mean a custom integration for every model–service pair. MCP, an open standard Anthropic released in November 2024, replaces this with one protocol, turning an N×M integration problem into N+M (the "USB-C of agentic AI" from our lecture). MCP uses JSON-RPC 2.0 between three roles. The **Host** is the LLM application that initiates connections, such as Claude Desktop or an IDE. Inside it, each **Client** is a connector holding a one-to-one link with a single **Server**, and each Server exposes capabilities as tools (model-controlled), resources (application-controlled), and prompts (user-controlled). Because clients discover a server's capabilities at runtime, a new server becomes usable without changing the agent's code.

Two points stood out to me. First, MCP is not an orchestration engine: it is the integration layer for an agent's actions, while frameworks still decide when and why tools are called. Compared with A2A, MCP is the agent's "hands" and A2A its "language" for talking to other agents. Second, the article admits that exposing tools does not guarantee the model uses them well; success depends on the quality of tool descriptions, alongside server-management overhead, security, and maturity concerns.

## The Fetch server, critiqued with the class design principles

Fetch exposes one tool, `fetch`, with four parameters: `url` (required), `max_length` (default 5000), `start_index` (default 0), and `raw` (default false), plus a `fetch` prompt.

- **Well-defined interface:** few parameters, each documented with defaults.
- **Isolated functionality:** one responsibility, retrieving a page in a model-friendly form. One could argue the `raw` flag bundles a second operation (download vs. Markdown conversion), but I see it as an output-format option on one cohesive task; splitting it would force two calls for every page.
- **Predictable behavior:** long pages are truncated with an explicit instruction to continue from a given `start_index`, and robots.txt is enforced for model-initiated requests with a clear error.

Its weaknesses are inherent rather than design flaws: live web content is non-deterministic, and the README warns the server can reach local or internal IP addresses, so it must be deployed carefully.
