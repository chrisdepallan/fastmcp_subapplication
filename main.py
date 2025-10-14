from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
import asyncio
from server import SwaggerToMCPConverter

app = FastAPI(title="Dynamic FastMCP Server Manager")

# Dictionary to hold dynamically created sub-apps
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
    
    # Create the streamable-http FastAPI sub-app (required for streamable-http transport)
    mcp_app = mcp.streamable_http_app()

    async def _shutdown_converter():
        try:
            await converter.close()
        except Exception:
            import logging
            logging.exception("Error closing converter HTTP client")

    mcp_app.add_event_handler("shutdown", lambda: asyncio.create_task(_shutdown_converter()))

    # return ASGI app instance (do not call it)
    return mcp_app


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
    app.mount(f"/api/{mcp_id}/mcp", mcp_app)
    mcp_apps[mcp_id] = mcp_app

    return {"message": f"MCP server '{mcp_id}' mounted at /api/{mcp_id}/mcp"}


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
