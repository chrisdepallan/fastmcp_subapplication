from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
import asyncio
from server import SwaggerToMCPConverter, SWAGGER_DOC, API_BASE_URL
import json

app = FastAPI(title="Dynamic FastMCP Server Manager")

# Dictionary to hold dynamically created sub-apps
# store metadata as { mcp_id: {"app": asgi_app, "mounted_at": mount_path} }
mcp_apps = {}

def create_mcp_app(mcp_id: str):
    """Create a FastMCP app dynamically based on ID (behaves like standalone mcp.run app)"""
    # Create converter + MCP instance per mcp_id
    converter = SwaggerToMCPConverter(SWAGGER_DOC, API_BASE_URL)
    mcp = FastMCP(f"MCP-{mcp_id}")

    # Register tools dynamically from the OpenAPI document
    tools = converter.parse_swagger_to_tools()
    for tool_def in tools:
        tool_name = tool_def["name"]
        tool_description = tool_def.get("description", "")

        # Use a single dict parameter (no **kwargs) — compatible with FastMCP FunctionTool
        def make_tool_handler(name: str):
            async def tool_handler(kwargs: dict = None) -> str:
                args = kwargs or {}
                return await converter.execute_tool(name, args)
            return tool_handler

        mcp.tool(name=tool_name, description=tool_description)(make_tool_handler(tool_name))

    # Use the same ASGI app the standalone server uses so behavior matches
    streamable_app = mcp.streamable_http_app()

    # attach shutdown hook to close the converter http client
    async def _shutdown_converter():
        try:
            await converter.close()
        except Exception:
            import logging
            logging.exception("Error closing converter HTTP client")

    # FastAPI app produced by mcp.streamable_http_app supports add_event_handler
    try:
        streamable_app.add_event_handler("shutdown", lambda: asyncio.create_task(_shutdown_converter()))
    except Exception:
        # fallback: nothing to attach
        pass

    # Store app, converter and mcp for inspect / server-side calls
    mount_path = f"/api/{mcp_id}/mcp"
    mcp_apps[mcp_id] = {
        "app": streamable_app,
        "mounted_at": mount_path,
        "converter": converter,
        "mcp": mcp,
        "tools": [t.get("name") for t in tools]
    }

    return streamable_app


@app.get("/")
def root():
    return {"message": "FastMCP Dynamic Manager is running 🚀"}


@app.post("/api/load_mcp/{mcp_id}")
async def load_mcp_server(mcp_id: str):
    """Load and mount an MCP sub-application dynamically"""
    if mcp_id in mcp_apps:
        return {"message": f"MCP server '{mcp_id}' already loaded."}

    # Create and mount new MCP server
    mcp_app = create_mcp_app(mcp_id)
    mount_path = f"/api/{mcp_id}/mcp"
    app.mount(mount_path, mcp_app)
    # store app and mount path for inspection
    mcp_apps[mcp_id] = {"app": mcp_app, "mounted_at": mount_path}

    return {"message": f"MCP server '{mcp_id}' mounted at {mount_path}"}


@app.get("/api/inspect_mcp/{mcp_id}")
async def inspect_mcp(mcp_id: str):
    """Return whether an MCP is loaded and list its routes for debugging."""
    entry = mcp_apps.get(mcp_id)
    if not entry:
        return JSONResponse(status_code=404, content={"loaded": False, "error": "MCP not found"})

    sub = entry["app"]
    # Try to collect routes; different ASGI apps expose routes differently
    routes = []
    try:
        for r in getattr(sub, "routes", []):
            routes.append({
                "path": getattr(r, "path", None),
                "name": getattr(r, "name", None),
                "methods": list(getattr(r, "methods", [])) if getattr(r, "methods", None) else None
            })
    except Exception:
        # fallback: represent the app
        routes = [str(sub)]

    return {"loaded": True, "mounted_at": entry.get("mounted_at"), "routes": routes}


@app.get("/api/list_mcp")
async def list_mcp_servers():
    """List all currently active MCP sub-applications"""
    return {"active_mcp_servers": list(mcp_apps.keys())}


@app.delete("/api/unload_mcp/{mcp_id}")
async def unload_mcp_server(mcp_id: str):
    """Unload an MCP sub-application"""
    if mcp_id not in mcp_apps:
        return JSONResponse(status_code=404, content={"error": "MCP not found"})

    # Remove from dictionary — note: FastAPI cannot truly unmount at runtime
    del mcp_apps[mcp_id]
    return {"message": f"MCP server '{mcp_id}' unloaded (logical removal only)"}


# Run using: uvicorn main:app --reload
