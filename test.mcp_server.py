import json
import asyncio
from typing import Any, Dict, List, Optional
import httpx

from mcp.types import Tool

# Configuration
API_BASE_URL = "http://127.0.0.1:8000"  # Change this to your API base URL

# Your OpenAPI spec
SWAGGER_DOC = {
    "openapi": "3.1.0",
    "info": {
        "title": "Simple User API",
        "description": "A dummy FastAPI server demonstrating user endpoints",
        "version": "1.0.0"
    },
    "paths": {
        "/add_user": {
            "post": {
                "summary": "Add User",
                "operationId": "add_user_add_user_post",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/UserAddRequest"
                            }
                        }
                    },
                    "required": True
                },
                "responses": {
                    "200": {
                        "description": "Successful Response",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/MessageResponse"
                                }
                            }
                        }
                    }
                }
            }
        },
        "/get_users": {
            "get": {
                "summary": "Get Users",
                "operationId": "get_users_get_users_get",
                "responses": {
                    "200": {
                        "description": "Successful Response",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "items": {
                                        "$ref": "#/components/schemas/UserResponse"
                                    },
                                    "type": "array"
                                }
                            }
                        }
                    }
                }
            }
        },
        "/delete_user/{user_id}": {
            "delete": {
                "summary": "Delete User",
                "operationId": "delete_user_delete_user__user_id__delete",
                "parameters": [
                    {
                        "name": "user_id",
                        "in": "path",
                        "required": True,
                        "schema": {
                            "type": "integer",
                            "title": "User Id"
                        }
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Successful Response"
                    }
                }
            }
        }
    },
    "components": {
        "schemas": {
            "UserAddRequest": {
                "properties": {
                    "name": {"type": "string", "title": "Name"},
                    "email": {"type": "string", "title": "Email"},
                    "age": {"anyOf": [{"type": "integer"}, {"type": "null"}], "title": "Age"}
                },
                "type": "object",
                "required": ["name", "email"],
                "title": "UserAddRequest"
            },
            "MessageResponse": {
                "properties": {
                    "message": {"type": "string", "title": "Message"}
                },
                "type": "object",
                "required": ["message"],
                "title": "MessageResponse"
            },
            "UserResponse": {
                "properties": {
                    "id": {"type": "integer", "title": "Id"},
                    "name": {"type": "string", "title": "Name"},
                    "email": {"type": "string", "title": "Email"},
                    "age": {"anyOf": [{"type": "integer"}, {"type": "null"}], "title": "Age"}
                },
                "type": "object",
                "required": ["id", "name", "email"],
                "title": "UserResponse"
            }
        }
    }
}


class SwaggerToMCPConverter:
    """Converts Swagger/OpenAPI docs to MCP tools with $ref resolution"""
    
    def __init__(self, swagger_doc: Dict[str, Any], base_url: str):
        self.swagger_doc = swagger_doc
        self.base_url = base_url.rstrip('/')
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.components = swagger_doc.get("components", {})
        self.schemas = self.components.get("schemas", {})
    
    def resolve_ref(self, ref_or_schema: Any) -> Dict[str, Any]:
        """Resolve $ref references to actual schemas"""
        if isinstance(ref_or_schema, dict):
            if "$ref" in ref_or_schema:
                ref_path = ref_or_schema["$ref"]
                # Handle #/components/schemas/SchemaName format
                if ref_path.startswith("#/components/schemas/"):
                    schema_name = ref_path.split("/")[-1]
                    return self.schemas.get(schema_name, {})
            return ref_or_schema
        return {}
    
    def extract_properties_from_schema(self, schema: Dict) -> tuple[Dict, List[str]]:
        """Extract properties and required fields from a schema"""
        resolved_schema = self.resolve_ref(schema)
        
        properties = {}
        required = resolved_schema.get("required", [])
        
        schema_props = resolved_schema.get("properties", {})
        for prop_name, prop_spec in schema_props.items():
            # Handle anyOf (e.g., nullable fields)
            if "anyOf" in prop_spec:
                # Take the first non-null type
                for option in prop_spec["anyOf"]:
                    if option.get("type") != "null":
                        prop_type = option.get("type", "string")
                        break
                else:
                    prop_type = "string"
            else:
                prop_type = prop_spec.get("type", "string")
            
            properties[prop_name] = {
                "type": prop_type,
                "description": prop_spec.get("title", prop_spec.get("description", ""))
            }
        
        return properties, required
    
    def parse_swagger_to_tools(self) -> List[Tool]:
        """Parse Swagger doc and create MCP tools"""
        tools = []
        paths = self.swagger_doc.get("paths", {})
        
        for path, methods in paths.items():
            for method, spec in methods.items():
                if method.lower() not in ['get', 'post', 'put', 'delete', 'patch']:
                    continue
                
                tool = self._create_tool_from_spec(path, method, spec)
                if tool:
                    tools.append(tool)
        
        return tools
    
    def _create_tool_from_spec(self, path: str, method: str, spec: Dict) -> Optional[Tool]:
        """Create a single MCP tool from OpenAPI spec"""
        operation_id = spec.get("operationId", f"{method}_{path.replace('/', '_')}")
        description = spec.get("summary", spec.get("description", f"{method.upper()} {path}"))
        
        # Build input schema
        input_schema = {
            "type": "object",
            "properties": {},
            "required": []
        }
        
        # Parse path parameters
        parameters = spec.get("parameters", [])
        for param in parameters:
            param_name = param["name"]
            param_in = param.get("in")
            
            if param_in in ["query", "path"]:
                param_schema = param.get("schema", {"type": "string"})
                input_schema["properties"][param_name] = {
                    "type": param_schema.get("type", "string"),
                    "description": param.get("description", param_schema.get("title", ""))
                }
                if param.get("required", False):
                    input_schema["required"].append(param_name)
        
        # Parse request body with $ref resolution
        request_body = spec.get("requestBody", {})
        if request_body:
            content = request_body.get("content", {})
            json_content = content.get("application/json", {})
            schema = json_content.get("schema", {})
            
            # Resolve the schema if it's a $ref
            properties, required = self.extract_properties_from_schema(schema)
            input_schema["properties"].update(properties)
            input_schema["required"].extend(required)
        
        return Tool(
            name=operation_id,
            description=description,
            inputSchema=input_schema
        )
    
    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Execute API call for a tool"""
        paths = self.swagger_doc.get("paths", {})
        
        for path, methods in paths.items():
            for method, spec in methods.items():
                operation_id = spec.get("operationId", f"{method}_{path.replace('/', '_')}")
                if operation_id == tool_name:
                    return await self._make_api_call(path, method, spec, arguments)
        
        return json.dumps({"error": f"Tool {tool_name} not found"})
    
    async def _make_api_call(self, path: str, method: str, spec: Dict, arguments: Dict) -> str:
        """Make the actual API call"""
        url = f"{self.base_url}{path}"
        
        # Separate path, query params and body params
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
        
        # Replace path parameters in URL
        for param_name, param_value in path_params.items():
            url = url.replace(f"{{{param_name}}}", str(param_value))
        
        try:
            if method.lower() == "get":
                response = await self.http_client.get(url, params=query_params)
            elif method.lower() == "post":
                response = await self.http_client.post(url, json=body_data, params=query_params)
            elif method.lower() == "put":
                response = await self.http_client.put(url, json=body_data, params=query_params)
            elif method.lower() == "delete":
                response = await self.http_client.delete(url, params=query_params)
            else:
                return json.dumps({"error": f"Method {method} not supported"})
            
            response.raise_for_status()
            
            # Try to return JSON, fallback to text
            try:
                return json.dumps(response.json(), indent=2)
            except:
                return response.text
                
        except httpx.HTTPError as e:
            return json.dumps({
                "error": str(e),
                "status_code": getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None, #type:ignore
                "details": getattr(e.response, 'text', None) if hasattr(e, 'response') else None #type:ignore
            })
    
    async def close(self):
        """Close HTTP client"""
        await self.http_client.aclose()

from mcp.server import Server
from mcp.types import TextContent, Tool
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
import json
from typing import Any, List, Optional
from collections.abc import AsyncIterator

# Initialize MCP Server
mcp_server = Server("swagger-api-mcp")

# Store active sessions per user
user_sessions = {}


async def get_user_tools(uid: str) -> List[Tool]:
    """Get tools specific to a user based on their UID"""
    if str(uid) == '1':
        return [Tool(
            name='first_user_tool',
            description="Tool for first user",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Query parameter"}
                },
                "required": ["query"]
            }
        )]
    elif str(uid) == '2':
        return [Tool(
            name='second_user_tool',
            description="Tool for second user",
            inputSchema={
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "Data parameter"}
                },
                "required": ["data"]
            }
        )]
    else:
        # Default tools for any user
        return [Tool(
            name='general_tool',
            description=f"General tool for user {uid}",
            inputSchema={
                "type": "object",
                "properties": {
                    "input": {"type": "string", "description": "Input parameter"}
                },
                "required": ["input"]
            }
        )]


async def call_tool(uid: str, name: str, arguments: Any) -> List[TextContent]:
    """Execute a tool (API call) for a specific user"""
    # Add user-specific logic here
    result = f"Tool '{name}' executed successfully for user {uid} with arguments: {json.dumps(arguments)}"
    return [TextContent(type="text", text=result)]


# Create FastAPI app
app = FastAPI(title="Multi-User Swagger MCP Server")

# Add CORS middleware to handle OPTIONS requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (restrict in production)
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods including OPTIONS
    allow_headers=["*"],  # Allow all headers
)


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "running",
        "server": "swagger-api-mcp",
        "version": "1.0.0",
        "usage": {
            "sse": "/{uid}/sse",
            "messages": "/{uid}/messages"
        },
        "example": "http://localhost:8000/user123/sse"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


async def sse_handler(request: Request, uid: str):
    """Handle SSE connections for MCP with user separation"""
    
    async def event_stream() -> AsyncIterator[str]:
        """Generate SSE events"""
        session_id = f"{uid}_{id(request)}"
        
        try:
            # Store session for this user
            if uid not in user_sessions:
                user_sessions[uid] = []
            user_sessions[uid].append(session_id)
            
            print(f"SSE connection established for user: {uid}, session: {session_id}")
            
            # Get the host from request headers or use default
            host = request.headers.get("host", "localhost:8000")
            protocol = "https" if request.url.scheme == "https" else "http"
            
            # Send initial endpoint event with proper formatting
            # The endpoint should be the full URL to the messages endpoint
            endpoint_url = f"{protocol}://{host}/{uid}/messages"
            yield f"event: endpoint\ndata: {endpoint_url}\n\n"
            
            print(f"Sent endpoint URL: {endpoint_url}")
            
            # Keep connection alive with heartbeats
            heartbeat_count = 0
            while not await request.is_disconnected():
                # Send periodic heartbeat to keep connection alive
                heartbeat_count += 1
                yield f": heartbeat {heartbeat_count} for user {uid}\n\n"
                await asyncio.sleep(30)  # Send heartbeat every 30 seconds
                
        except asyncio.CancelledError:
            print(f"SSE connection cancelled for user: {uid}")
        except Exception as e:
            print(f"SSE error for user {uid}: {e}")
        finally:
            # Clean up session
            if uid in user_sessions and session_id in user_sessions[uid]:
                user_sessions[uid].remove(session_id)
                if not user_sessions[uid]:
                    del user_sessions[uid]
            print(f"SSE connection closed for user: {uid}, session: {session_id}")
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*"
        }
    )


# Support both GET and POST for SSE endpoint
@app.get("/{uid}/sse")
async def handle_sse_get(request: Request, uid: str):
    """Handle GET SSE connections"""
    return await sse_handler(request, uid)


@app.post("/{uid}/sse")
async def handle_sse_post(request: Request, uid: str):
    """Handle POST SSE connections (used by some clients)"""
    return await sse_handler(request, uid)


@app.options("/{uid}/sse")
async def handle_sse_options(uid: str):
    """Handle OPTIONS preflight requests for SSE"""
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*"
        }
    )


@app.post("/{uid}/messages")
async def handle_messages(request: Request, uid: str):
    """Handle JSON-RPC messages from MCP clients for specific user"""
    try:
        message = await request.json()
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params", {})
        
        print(f"User {uid} - Received: {json.dumps(message, indent=2)}")
        
        # Handle initialize
        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {},
                        "prompts": {},
                        "resources": {}
                    },
                    "serverInfo": {
                        "name": f"swagger-api-mcp-user-{uid}",
                        "version": "1.0.0"
                    }
                }
            }
            print(f"User {uid} - Sending initialize response")
            return response
        
        # Handle initialized notification
        elif method == "notifications/initialized":
            print(f"User {uid} initialized successfully")
            # Return empty response for notification
            return Response(content="", status_code=200)
        
        # Handle tools/list
        elif method == "tools/list":
            tools = await get_user_tools(uid)
            
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": tool.inputSchema
                        }
                        for tool in tools
                    ]
                }
            }
            print(f"User {uid} - Returned {len(tools)} tools")
            return response
        
        # Handle tools/call
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            
            print(f"User {uid} - Calling tool: {name} with args: {arguments}")
            result = await call_tool(uid, name, arguments)
            
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": content.type,
                            "text": content.text
                        }
                        for content in result
                    ]
                }
            }
            print(f"User {uid} - Tool response sent")
            return response
        
        # Handle prompts/list (even if empty)
        elif method == "prompts/list":
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "prompts": []
                }
            }
            return response
        
        # Handle resources/list (even if empty)
        elif method == "resources/list":
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "resources": []
                }
            }
            return response
        
        # Handle ping
        elif method == "ping":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {}
            }
        
        # Handle other notifications (no response needed)
        elif method.startswith("notifications/"):
            print(f"User {uid} - Received notification: {method}")
            return Response(content="", status_code=200)
        
        # Unknown method
        else:
            print(f"User {uid} - Unknown method: {method}")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }
            
    except Exception as e:
        print(f"User {uid} - Error handling message: {e}")
        import traceback
        traceback.print_exc()
        
        # Check if we have message and id
        error_id = None
        if "message" in locals() and isinstance(message, dict):
            error_id = message.get("id")
        
        return {
            "jsonrpc": "2.0",
            "id": error_id,
            "error": {
                "code": -32603,
                "message": f"Internal error: {str(e)}"
            }
        }


@app.options("/{uid}/messages")
async def handle_messages_options(uid: str):
    """Handle OPTIONS preflight requests for messages"""
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "*"
        }
    )


@app.get("/admin/sessions")
async def get_active_sessions():
    """Admin endpoint to view active user sessions"""
    return {
        "active_users": list(user_sessions.keys()),
        "sessions": {
            uid: len(sessions) 
            for uid, sessions in user_sessions.items()
        },
        "total_sessions": sum(len(sessions) for sessions in user_sessions.values())
    }


if __name__ == "__main__":
    import sys
    
    # Get port from command line or use default
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    
    print("=" * 60)
    print(f"Starting Multi-User MCP Server on http://0.0.0.0:{port}")
    print("=" * 60)
    print("\nUsage in Claude Desktop or Cursor:")
    print(f"  User 1: http://localhost:{port}/1/sse")
    print(f"  User 2: http://localhost:{port}/2/sse")
    print(f"  Custom: http://localhost:{port}/your-user-id/sse")
    print("\nAdmin endpoints:")
    print(f"  Sessions: http://localhost:{port}/admin/sessions")
    print(f"  Health:   http://localhost:{port}/health")
    print("\nPress Ctrl+C to stop the server")
    print("=" * 60)
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True
    )