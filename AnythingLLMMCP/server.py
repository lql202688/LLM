import os

import httpx
from dotenv import load_dotenv
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

load_dotenv()

BASE_URL = os.environ["ANYTHINGLLM_BASE_URL"].rstrip("/")
API_KEY = os.environ["ANYTHINGLLM_API_KEY"]

mcp = MCPServer("anythingllm-workspace")


@mcp.tool()
async def ask_first_workspace(question: str) -> str:
    """Answer a question using the documents in the first AnythingLLM workspace."""
    headers = {"Authorization": f"Bearer {API_KEY}"}

    try:
        async with httpx.AsyncClient(
            base_url=BASE_URL,
            headers=headers,
            timeout=60,
        ) as client:
            workspaces_response = await client.get("/v1/workspaces")
            workspaces_response.raise_for_status()
            workspaces = workspaces_response.json().get("workspaces", [])

            if not workspaces:
                raise ToolError("No AnythingLLM workspace was found.")

            workspace = workspaces[0]
            chat_response = await client.post(
                f"/v1/workspace/{workspace['slug']}/chat",
                json={"message": question, "mode": "query"},
            )
            chat_response.raise_for_status()
            result = chat_response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.json().get("message", exc.response.text)
        raise ToolError(
            f"AnythingLLM API error ({exc.response.status_code}): {detail}"
        ) from exc
    except httpx.RequestError as exc:
        raise ToolError(f"Cannot connect to AnythingLLM: {exc}") from exc

    answer = result.get("textResponse")
    if not answer:
        raise ToolError(result.get("error") or "AnythingLLM returned no answer.")

    titles = [
        source["title"]
        for source in result.get("sources", [])
        if source.get("title")
    ]
    if titles:
        return f"{answer}\n\nSources:\n" + "\n".join(f"- {title}" for title in titles)
    return answer


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("MCP_PORT", "8000")),
        streamable_http_path=os.getenv("MCP_PATH", "/mcp"),
        json_response=True,
        stateless_http=True,
    )
