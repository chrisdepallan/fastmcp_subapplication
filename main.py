from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
import asyncio
from server import SwaggerToMCPConverter

app = FastAPI(title="Dynamic FastMCP Server Manager")

# Dictionary to hold dynamically created sub-apps
# store metadata as { mcp_id: {"app": asgi_app, "mounted_at": mount_path} }
mcp_apps = {}

def create_mcp_app(mcp_id: str):
    """Create a FastMCP app dynamically based on ID"""
    # Create converter + MCP instance per mcp_id
    
    API_BASE_URL = "https://outbound.byteflow.bot"
    SWAGGER_DOC = {"openapi":"3.1.0","info":{"title":"AI Calling Dashboard","version":"2.1.0"},"paths":{"/transcript/update":{"post":{"summary":"Receive Transcript Update","description":"Receive transcript updates from the main API and broadcast to subscribed clients","operationId":"receive_transcript_update_transcript_update_post","responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}}}}},"/proxy/api/{path}":{"patch":{"summary":"Proxy Api","description":"Proxy API calls to the main API server","operationId":"proxy_api_proxy_api__path__patch","parameters":[{"name":"path","in":"path","required":"true","schema":{"type":"string","title":"Path"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}},"delete":{"summary":"Proxy Api","description":"Proxy API calls to the main API server","operationId":"proxy_api_proxy_api__path__patch","parameters":[{"name":"path","in":"path","required":"true","schema":{"type":"string","title":"Path"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}},"get":{"summary":"Proxy Api","description":"Proxy API calls to the main API server","operationId":"proxy_api_proxy_api__path__patch","parameters":[{"name":"path","in":"path","required":"true","schema":{"type":"string","title":"Path"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}},"put":{"summary":"Proxy Api","description":"Proxy API calls to the main API server","operationId":"proxy_api_proxy_api__path__patch","parameters":[{"name":"path","in":"path","required":"true","schema":{"type":"string","title":"Path"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}},"post":{"summary":"Proxy Api","description":"Proxy API calls to the main API server","operationId":"proxy_api_proxy_api__path__patch","parameters":[{"name":"path","in":"path","required":"true","schema":{"type":"string","title":"Path"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}}},"/broadcast/call-status":{"post":{"summary":"Broadcast Call Status","description":"Broadcast call status updates to all connected WebSocket clients","operationId":"broadcast_call_status_broadcast_call_status_post","responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}}}}},"/health":{"get":{"summary":"Health Check","description":"Dashboard health check","operationId":"health_check_health_get","responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}}}}},"/":{"get":{"summary":"Dashboard","description":"Serve the dashboard HTML","operationId":"dashboard__get","responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}}}}}},"components":{"schemas":{"HTTPValidationError":{"properties":{"detail":{"items":{"$ref":"#/components/schemas/ValidationError"},"type":"array","title":"Detail"}},"type":"object","title":"HTTPValidationError"},"ValidationError":{"properties":{"loc":{"items":{"anyOf":[{"type":"string"},{"type":"integer"}]},"type":"array","title":"Location"},"msg":{"type":"string","title":"Message"},"type":{"type":"string","title":"Error Type"}},"type":"object","required":["loc","msg","type"],"title":"ValidationError"}}}}
        

    converter = SwaggerToMCPConverter(SWAGGER_DOC, API_BASE_URL)
    mcp = FastMCP(f"MCP-{mcp_id}")

    # Register tools dynamically from the OpenAPI document
    tools = converter.parse_swagger_to_tools()
    for tool_def in tools:
        tool_name = tool_def["name"]
        tool_description = tool_def.get("description", "")

        def make_tool_handler(name: str):
            # Use a single dict parameter (no **kwargs) — compatible with FastMCP FunctionTool
            async def tool_handler(kwargs: dict = None) -> str:
                args = kwargs or {}
                return await converter.execute_tool(name, args)
            return tool_handler

        mcp.tool(name=tool_name, description=tool_description)(make_tool_handler(tool_name))

    # Create both apps:
    streamable_app = mcp.streamable_http_app()
    http_app = mcp.http_app()

    # Ensure OpenAPI is generated (force warm-up)
    try:
        # this returns the dict (or None) — forces generation of routes/openapi
        _openapi = http_app.openapi()
    except Exception:
        _openapi = None

    # Small wrapper FastAPI app that mounts both for easier testing
    wrapper = FastAPI(title=f"MCP-{mcp_id}-wrapper")
    wrapper.mount("/stream", streamable_app)   # real streamable-http entrypoint
    wrapper.mount("/http", http_app)           # regular HTTP endpoints (docs, direct calls)

    # Debug endpoints exposed on wrapper for inspection
    @wrapper.get("/debug/openapi")
    async def _debug_openapi():
        return http_app.openapi() or {}

    @wrapper.get("/debug/tools")
    async def _debug_tools():
        # list tool operationIds / names parsed from the swagger conversion
        return {"tools": [t.get("name") for t in tools], "tool_count": len(tools), "openapi_present": bool(_openapi)}

    async def _shutdown_converter():
        try:
            await converter.close()
        except Exception:
            import logging
            logging.exception("Error closing converter HTTP client")

    # ensure converter closed when wrapper shuts down
    wrapper.add_event_handler("shutdown", lambda: asyncio.create_task(_shutdown_converter()))

    # return wrapper ASGI app
    return wrapper


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
