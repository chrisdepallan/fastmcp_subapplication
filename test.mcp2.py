# ============================================================
# Dependencies
# ============================================================

import logging
import asyncio
import json
from typing import Any, Dict, List, Optional
from mcp.types import (
    Resource,
    Tool,
    TextContent,
)
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
import uvicorn
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openapi-mcp-sse")

# ============================================================
# OpenAPI to MCP Converter
# ============================================================

class OpenAPIConverter:
    """Converts OpenAPI specs to MCP tools and executes API calls"""
    
    def __init__(self, openapi_spec: Dict[str, Any], base_url: str):
        self.openapi_spec = openapi_spec
        self.base_url = base_url.rstrip('/')
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.components = openapi_spec.get("components", {})
        self.schemas = self.components.get("schemas", {})
        logger.info(f"Initialized converter for {base_url}")
    
    def resolve_ref(self, ref_or_schema: Any) -> Dict[str, Any]:
        """Resolve $ref references"""
        if isinstance(ref_or_schema, dict):
            if "$ref" in ref_or_schema:
                ref_path = ref_or_schema["$ref"]
                if ref_path.startswith("#/components/schemas/"):
                    schema_name = ref_path.split("/")[-1]
                    return self.schemas.get(schema_name, {})
            return ref_or_schema
        return {}
    
    def extract_properties(self, schema: Dict) -> tuple[Dict, List[str]]:
        """Extract properties and required fields from schema"""
        resolved = self.resolve_ref(schema)
        properties = {}
        required = resolved.get("required", [])
        
        for prop_name, prop_spec in resolved.get("properties", {}).items():
            resolved_prop = self.resolve_ref(prop_spec)
            prop_type = resolved_prop.get("type", "string")
            properties[prop_name] = {
                "type": prop_type,
                "description": resolved_prop.get("description", resolved_prop.get("title", ""))
            }
        
        return properties, required
    
    def create_tools(self) -> List[Tool]:
        """Convert OpenAPI paths to MCP Tool objects"""
        tools = []
        paths = self.openapi_spec.get("paths", {})
        
        logger.info(f"Found {len(paths)} paths in OpenAPI spec")
        
        for path, methods in paths.items():
            for method, spec in methods.items():
                if method.lower() not in ['get', 'post', 'put', 'delete', 'patch']:
                    continue
                
                operation_id = spec.get("operationId", f"{method}_{path.replace('/', '_').replace('{', '').replace('}', '')}")
                description = spec.get("summary", spec.get("description", f"{method.upper()} {path}"))
                
                # Build input schema
                input_schema = {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
                
                # Add path and query parameters
                for param in spec.get("parameters", []):
                    param_name = param["name"]
                    param_in = param.get("in")
                    
                    if param_in in ["query", "path"]:
                        param_schema = param.get("schema", {"type": "string"})
                        input_schema["properties"][param_name] = {
                            "type": param_schema.get("type", "string"),
                            "description": param.get("description", "")
                        }
                        if param.get("required", False):
                            input_schema["required"].append(param_name)
                
                # Add request body properties
                request_body = spec.get("requestBody", {})
                if request_body:
                    content = request_body.get("content", {})
                    json_content = content.get("application/json", {})
                    schema = json_content.get("schema", {})
                    
                    properties, required = self.extract_properties(schema)
                    input_schema["properties"].update(properties)
                    input_schema["required"].extend(required)
                
                tool = Tool(
                    name=operation_id,
                    description=description,
                    inputSchema=input_schema
                )
                
                tools.append(tool)
                logger.info(f"Created tool: {operation_id} ({method.upper()} {path})")
        
        logger.info(f"Total tools created: {len(tools)}")
        return tools
    
    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Execute API call for a tool"""
        paths = self.openapi_spec.get("paths", {})
        
        for path, methods in paths.items():
            for method, spec in methods.items():
                operation_id = spec.get("operationId", f"{method}_{path.replace('/', '_').replace('{', '').replace('}', '')}")
                
                if operation_id == tool_name:
                    return await self._make_api_call(path, method, spec, arguments)
        
        return json.dumps({"error": f"Tool {tool_name} not found"})
    
    async def _make_api_call(self, path: str, method: str, spec: Dict, arguments: Dict) -> str:
        """Make actual HTTP API call"""
        url = f"{self.base_url}{path}"
        
        # Separate parameters
        path_params = {}
        query_params = {}
        body_data = {}
        
        parameters = spec.get("parameters", [])
        path_param_names = {p["name"] for p in parameters if p.get("in") == "path"}
        query_param_names = {p["name"] for p in parameters if p.get("in") == "query"}
        
        for key, value in arguments.items():
            if key in path_param_names:
                path_params[key] = value
            elif key in query_param_names:
                query_params[key] = value
            else:
                body_data[key] = value
        
        # Replace path parameters
        for param_name, param_value in path_params.items():
            url = url.replace(f"{{{param_name}}}", str(param_value))
        
        logger.info(f"Calling {method.upper()} {url}")
        
        try:
            if method.lower() == "get":
                response = await self.http_client.get(url, params=query_params)
            elif method.lower() == "post":
                response = await self.http_client.post(url, json=body_data, params=query_params)
            elif method.lower() == "put":
                response = await self.http_client.put(url, json=body_data, params=query_params)
            elif method.lower() == "delete":
                response = await self.http_client.delete(url, params=query_params)
            elif method.lower() == "patch":
                response = await self.http_client.patch(url, json=body_data, params=query_params)
            else:
                return json.dumps({"error": f"Method {method} not supported"})
            
            response.raise_for_status()
            
            try:
                return json.dumps(response.json(), indent=2)
            except:
                return response.text
                
        except httpx.HTTPError as e:
            error_detail = {
                "error": str(e),
                "status_code": getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None,
            }
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail["response"] = e.response.text
                except:
                    pass
            return json.dumps(error_detail, indent=2)
    
    async def close(self):
        """Close HTTP client"""
        await self.http_client.aclose()

# ============================================================
# Store user converters and servers
# ============================================================
user_converters = {}
user_servers = {}

# ============================================================
# Temporary: Hardcoded OpenAPI specs (replace with DB later)
# ============================================================
HARDCODED_SPECS = {
    "1": {
        "openapi_spec": {
            "openapi": "3.1.0",
            "info": {"title": "AI Calling Dashboard", "version": "2.1.0"},
            "paths": {
                "/transcript/update": {
                    "post": {
                        "summary": "Receive Transcript Update",
                        "description": "Receive transcript updates from the main API and broadcast to subscribed clients",
                        "operationId": "receive_transcript_update_transcript_update_post",
                        "responses": {"200": {"description": "Successful Response"}}
                    }
                },
                "/proxy/api/{path}": {
                    "patch": {
                        "summary": "Proxy Api",
                        "description": "Proxy API calls to the main API server",
                        "operationId": "proxy_api_proxy_api__path__patch",
                        "parameters": [{
                            "name": "path",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "title": "Path"}
                        }],
                        "responses": {"200": {"description": "Successful Response"}}
                    },
                    "delete": {
                        "summary": "Proxy Api Delete",
                        "description": "Proxy API calls to the main API server",
                        "operationId": "proxy_api_delete",
                        "parameters": [{
                            "name": "path",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "title": "Path"}
                        }],
                        "responses": {"200": {"description": "Successful Response"}}
                    },
                    "get": {
                        "summary": "Proxy Api Get",
                        "description": "Proxy API calls to the main API server",
                        "operationId": "proxy_api_get",
                        "parameters": [{
                            "name": "path",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "title": "Path"}
                        }],
                        "responses": {"200": {"description": "Successful Response"}}
                    },
                    "put": {
                        "summary": "Proxy Api Put",
                        "description": "Proxy API calls to the main API server",
                        "operationId": "proxy_api_put",
                        "parameters": [{
                            "name": "path",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "title": "Path"}
                        }],
                        "responses": {"200": {"description": "Successful Response"}}
                    },
                    "post": {
                        "summary": "Proxy Api Post",
                        "description": "Proxy API calls to the main API server",
                        "operationId": "proxy_api_post",
                        "parameters": [{
                            "name": "path",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "title": "Path"}
                        }],
                        "responses": {"200": {"description": "Successful Response"}}
                    }
                },
                "/broadcast/call-status": {
                    "post": {
                        "summary": "Broadcast Call Status",
                        "description": "Broadcast call status updates to all connected WebSocket clients",
                        "operationId": "broadcast_call_status_broadcast_call_status_post",
                        "responses": {"200": {"description": "Successful Response"}}
                    }
                },
                "/health": {
                    "get": {
                        "summary": "Health Check",
                        "description": "Dashboard health check",
                        "operationId": "health_check_health_get",
                        "responses": {"200": {"description": "Successful Response"}}
                    }
                },
                "/": {
                    "get": {
                        "summary": "Dashboard",
                        "description": "Serve the dashboard HTML",
                        "operationId": "dashboard__get",
                        "responses": {"200": {"description": "Successful Response"}}
                    }
                }
            }
        },
        "base_url": "https://outbound.byteflow.bot"
    }
}

# Initialize hardcoded specs on startup
def init_hardcoded_specs():
    """Initialize servers from hardcoded specs"""
    for user_id, config in HARDCODED_SPECS.items():
        try:
            converter = OpenAPIConverter(config["openapi_spec"], config["base_url"])
            server = create_server_from_openapi(user_id, converter)
            user_converters[user_id] = converter
            user_servers[user_id] = server
            tools = converter.create_tools()
            logger.info(f"Initialized user {user_id} with {len(tools)} tools from hardcoded spec")
        except Exception as e:
            logger.error(f"Error initializing hardcoded spec for user {user_id}: {e}")

# ============================================================
# Create server from OpenAPI spec
# ============================================================
def create_server_from_openapi(user_id: str, converter: OpenAPIConverter) -> Server:
    """Create MCP server from OpenAPI converter"""
    server = Server(f"openapi-mcp-user-{user_id}")
    
    # Get all tools from the converter
    tools = converter.create_tools()
    logger.info(f"Server for user {user_id} has {len(tools)} tools")
    
    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        logger.info(f"list_tools called for user {user_id}, returning {len(tools)} tools")
        return tools
    
    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None):
        if arguments is None:
            arguments = {}
        
        logger.info(f"call_tool for user {user_id}: {name} with args: {arguments}")
        result = await converter.execute_tool(name, arguments)
        return [TextContent(type="text", text=result)]
    
    @server.list_resources()
    async def handle_list_resources() -> list[Resource]:
        return [
            Resource(
                uri=f"openapi://user-{user_id}/spec",
                name=f"OpenAPI Spec",
                description="The OpenAPI specification for this API",
                mimeType="application/json"
            )
        ]
    
    @server.read_resource()
    async def handle_read_resource(uri: str):
        if uri == f"openapi://user-{user_id}/spec":
            return json.dumps(converter.openapi_spec, indent=2)
        return f"Resource not found: {uri}"
    
    return server

# ============================================================
# SSE Transport
# ============================================================
sse = SseServerTransport("/messages/")

# ============================================================
# SSE Handler
# ============================================================
async def handle_sse(request: Request):
    """Handle SSE connections"""
    user_id = request.headers.get("user_id", "1")  # Default to user 1
    logger.info(f"SSE connection for user: {user_id}")
    
    if user_id not in user_servers:
        logger.error(f"No server found for user {user_id}. Available users: {list(user_servers.keys())}")
        return JSONResponse(
            {"error": f"No configuration found for user {user_id}. Available users: {list(user_servers.keys())}"},
            status_code=400
        )
    
    server = user_servers[user_id]
    
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(
            streams[0],
            streams[1],
            InitializationOptions(
                server_name=f"openapi-mcp-user-{user_id}",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )
    
    return Response()

# ============================================================
# Upload OpenAPI Spec
# ============================================================
async def upload_spec(request: Request):
    """Upload OpenAPI spec for a user"""
    try:
        data = await request.json()
        user_id = request.headers.get("user_id")
        
        if not user_id:
            return JSONResponse({"error": "user_id header required"}, status_code=400)
        
        openapi_spec = data.get("openapi_spec")
        base_url = data.get("base_url")
        
        if not openapi_spec:
            return JSONResponse({"error": "openapi_spec is required"}, status_code=400)
        
        if not base_url:
            return JSONResponse({"error": "base_url is required"}, status_code=400)
        
        # Clean up old converter if exists
        if user_id in user_converters:
            await user_converters[user_id].close()
        
        # Create new converter and server
        converter = OpenAPIConverter(openapi_spec, base_url)
        server = create_server_from_openapi(user_id, converter)
        
        user_converters[user_id] = converter
        user_servers[user_id] = server
        
        tools = converter.create_tools()
        
        logger.info(f"Created server for user {user_id} with {len(tools)} tools")
        
        return JSONResponse({
            "status": "success",
            "user_id": user_id,
            "base_url": base_url,
            "tools_count": len(tools),
            "tools": [{"name": t.name, "description": t.description} for t in tools]
        })
        
    except Exception as e:
        logger.error(f"Error uploading spec: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)

# ============================================================
# Health Check
# ============================================================
async def health_check(request: Request):
    """Health check"""
    users_info = {}
    for user_id in user_servers.keys():
        if user_id in user_converters:
            converter = user_converters[user_id]
            tools = converter.create_tools()
            users_info[user_id] = {
                "base_url": converter.base_url,
                "tools_count": len(tools),
                "tools": [t.name for t in tools]
            }
    
    return JSONResponse({
        "status": "healthy",
        "active_users": list(user_servers.keys()),
        "users_info": users_info
    })

# ============================================================
# Starlette App
# ============================================================
app = Starlette(
    debug=True,
    routes=[
        Route("/health", endpoint=health_check, methods=["GET"]),
        Route("/upload-spec", endpoint=upload_spec, methods=["POST"]),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
    ],
)

@app.on_event("startup")
async def startup():
    """Initialize hardcoded specs on startup"""
    init_hardcoded_specs()

@app.on_event("shutdown")
async def shutdown():
    for converter in user_converters.values():
        await converter.close()

# ============================================================
# Main
# ============================================================
def main():
    host = "0.0.0.0"
    port = 8000
    
    logger.info("=" * 60)
    logger.info("OpenAPI to MCP SSE Server")
    logger.info("=" * 60)
    logger.info(f"Server running on http://{host}:{port}")
    logger.info("")
    logger.info("Endpoints:")
    logger.info(f"  POST /upload-spec  - Upload OpenAPI spec (requires user_id header)")
    logger.info(f"  GET  /sse          - Connect to MCP server (requires user_id header)")
    logger.info(f"  GET  /health       - Health check and list users")
    logger.info("")
    logger.info("Pre-configured Users:")
    logger.info(f"  user_id: 1 - AI Calling Dashboard API")
    logger.info("")
    logger.info("Quick Start:")
    logger.info("  Connect with user_id=1 header to http://localhost:8000/sse")
    logger.info("  Or check /health to see available users and tools")
    logger.info("")
    logger.info("Note: Replace HARDCODED_SPECS with DB queries for production")
    logger.info("=" * 60)
    
    uvicorn.run(app, host=host, port=port, log_level="info")

if __name__ == "__main__":
    main()