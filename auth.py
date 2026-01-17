from mcp.server import Server
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
import uvicorn
import secrets
import time
import json
import os
from typing import Dict, Optional

# Initialize MCP Server
mcp_server = Server("oauth-mcp-server")

# OAuth Configuration
OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "mcp-client-id")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "mcp-client-secret")
TOKEN_EXPIRY = 3600  # 1 hour

# Token storage (use Redis/database in production)
tokens: Dict[str, dict] = {}

# ============================================================================
# OAuth 2.0 Endpoints
# ============================================================================

async def oauth_metadata(request: Request) -> JSONResponse:
    """OAuth 2.0 Authorization Server Metadata (RFC 8414)"""
    base_url = str(request.base_url).rstrip('/')
    
    return JSONResponse({
        "issuer": base_url,
        "token_endpoint": f"{base_url}/oauth/token",
        "grant_types_supported": ["client_credentials"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "response_types_supported": ["token"],
        "scopes_supported": ["mcp"]
    })

async def oauth_token_endpoint(request: Request) -> JSONResponse:
    """
    OAuth 2.0 Token Endpoint
    Handles client_credentials grant type
    """
    try:
        # Parse form data
        form_data = await request.form()
        grant_type = form_data.get("grant_type")
        client_id = form_data.get("client_id")
        client_secret = form_data.get("client_secret")
        
        # Validate grant type
        if grant_type != "client_credentials":
            return JSONResponse(
                {
                    "error": "unsupported_grant_type",
                    "error_description": "Only client_credentials grant type is supported"
                },
                status_code=400
            )
        
        # Validate client credentials
        if not client_id or not client_secret:
            return JSONResponse(
                {
                    "error": "invalid_request",
                    "error_description": "Missing client_id or client_secret"
                },
                status_code=400
            )
        
        if client_id != OAUTH_CLIENT_ID or client_secret != OAUTH_CLIENT_SECRET:
            return JSONResponse(
                {
                    "error": "invalid_client",
                    "error_description": "Invalid client credentials"
                },
                status_code=401,
                headers={"WWW-Authenticate": "Basic"}
            )
        
        # Generate access token
        access_token = secrets.token_urlsafe(32)
        expires_at = time.time() + TOKEN_EXPIRY
        
        # Store token
        tokens[access_token] = {
            "client_id": client_id,
            "expires_at": expires_at,
            "scope": "mcp"
        }
        
        # Return OAuth 2.0 token response
        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": TOKEN_EXPIRY,
            "scope": "mcp"
        })
        
    except Exception as e:
        return JSONResponse(
            {
                "error": "server_error",
                "error_description": str(e)
            },
            status_code=500
        )

# ============================================================================
# Authentication Verification
# ============================================================================

def verify_bearer_token(request: Request) -> Optional[dict]:
    """
    Verify Bearer token from Authorization header
    Returns token data if valid, None otherwise
    """
    auth_header = request.headers.get("Authorization", "")
    
    if not auth_header.startswith("Bearer "):
        return None
    
    token = auth_header[7:]  # Remove "Bearer " prefix
    
    # Check token existence
    token_data = tokens.get(token)
    if not token_data:
        return None
    
    # Check expiration
    if time.time() > token_data["expires_at"]:
        del tokens[token]
        return None
    
    return token_data

# ============================================================================
# MCP SSE Endpoints
# ============================================================================

async def handle_sse(request: Request) -> Response:
    """
    SSE endpoint for MCP protocol
    Requires valid Bearer token
    """
    # Verify authentication
    token_data = verify_bearer_token(request)
    if not token_data:
        return Response(
            content=json.dumps({"error": "Unauthorized"}),
            status_code=401,
            headers={"WWW-Authenticate": "Bearer realm=\"MCP Server\""}
        )
    
    # Create SSE transport
    sse = SseServerTransport("/messages")
    
    async with sse:
        await mcp_server.run(
            sse.read_stream,
            sse.write_stream,
            mcp_server.create_initialization_options()
        )
    
    return sse.response

async def handle_messages(request: Request) -> Response:
    """
    POST endpoint for client messages
    Requires valid Bearer token
    """
    # Verify authentication
    token_data = verify_bearer_token(request)
    if not token_data:
        return Response(
            content=json.dumps({"error": "Unauthorized"}),
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    # Message handling is done through SSE transport
    return Response(status_code=204)

# ============================================================================
# MCP Server Tools Definition
# ============================================================================

@mcp_server.list_tools()
async def list_tools():
    """List available MCP tools"""
    return [
        {
            "name": "echo",
            "description": "Echo back a message",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Message to echo"
                    }
                },
                "required": ["message"]
            }
        },
        {
            "name": "get_time",
            "description": "Get current server time",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        }
    ]

@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict):
    """Execute MCP tool"""
    if name == "echo":
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Echo: {arguments.get('message', '')}"
                }
            ]
        }
    elif name == "get_time":
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Current time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                }
            ]
        }
    
    raise ValueError(f"Unknown tool: {name}")

# ============================================================================
# Starlette App Setup
# ============================================================================

routes = [
    # OAuth 2.0 endpoints
    Route("/.well-known/oauth-authorization-server", endpoint=oauth_metadata),
    Route("/oauth/token", endpoint=oauth_token_endpoint, methods=["POST"]),
    
    # MCP SSE endpoints
    Route("/sse", endpoint=handle_sse),
    Route("/messages", endpoint=handle_messages, methods=["POST"]),
]

middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
]

app = Starlette(routes=routes, middleware=middleware)

# ============================================================================
# Run Server
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("MCP Server with OAuth 2.0 Authentication")
    print("=" * 60)
    print(f"\nOAuth Configuration:")
    print(f"  Client ID:     {OAUTH_CLIENT_ID}")
    print(f"  Client Secret: {OAUTH_CLIENT_SECRET}")
    print(f"\nEndpoints:")
    print(f"  OAuth Metadata: http://localhost:8000/.well-known/oauth-authorization-server")
    print(f"  Token:          http://localhost:8000/oauth/token")
    print(f"  SSE:            http://localhost:8000/sse")
    print(f"  Messages:       http://localhost:8000/messages")
    print(f"\nClaude Desktop config.json:")
    print(json.dumps({
        "mcpServers": {
            "my-oauth-server": {
                "url": "http://localhost:8000/sse",
                "transport": "sse",
                "oauth": {
                    "client_id": OAUTH_CLIENT_ID,
                    "client_secret": OAUTH_CLIENT_SECRET,
                    "token_url": "http://localhost:8000/oauth/token"
                }
            }
        }
    }, indent=2))
    print("\n" + "=" * 60)
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        ssl_keyfile="key.pem",
        ssl_certfile="cert.pem"
    )