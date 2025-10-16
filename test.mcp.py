# ============================================================
# Dependencies
# ============================================================

import logging
import asyncio
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    Prompt,
    PromptMessage,
    PromptArgument,
    GetPromptResult
)
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sample-tools-mcp-sse")

# ============================================================
# User-specific configuration
# ============================================================

# Store active servers per user
user_servers = {}

# Define user-specific resources
USER_RESOURCES = {
    "1": [
        Resource(
            uri="sample://user1/python_basics",
            name="Python Basics (User 1)",
            description="Python examples for user 1",
            mimeType="text/plain"
        ),
        Resource(
            uri="sample://user1/data_analysis",
            name="Data Analysis Guide",
            description="Data analysis tutorials for user 1",
            mimeType="text/plain"
        ),
    ],
    "2": [
        Resource(
            uri="sample://user2/web_dev",
            name="Web Development (User 2)",
            description="Web dev resources for user 2",
            mimeType="text/plain"
        ),
        Resource(
            uri="sample://user2/api_design",
            name="API Design Guide",
            description="API design patterns for user 2",
            mimeType="text/plain"
        ),
    ],
    "default": [
        Resource(
            uri="sample://default/general",
            name="General Resources",
            description="General purpose resources",
            mimeType="text/plain"
        ),
    ]
}

# Define user-specific tools
USER_TOOLS = {
    "1": [
        {
            "name": "calculate_sum",
            "description": "Calculate sum of two numbers (User 1 only)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "First number"},
                    "b": {"type": "number", "description": "Second number"}
                },
                "required": ["a", "b"]
            }
        },
        {
            "name": "get_user_data",
            "description": "Get user-specific data (User 1)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "data_type": {"type": "string", "description": "Type of data to retrieve"}
                },
                "required": ["data_type"]
            }
        },
    ],
    "2": [
        {
            "name": "send_notification",
            "description": "Send notification (User 2 only)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Notification message"},
                    "priority": {"type": "string", "description": "Priority level"}
                },
                "required": ["message"]
            }
        },
        {
            "name": "query_database",
            "description": "Query database (User 2)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "SQL query to execute"}
                },
                "required": ["query"]
            }
        },
    ],
    "default": [
        {
            "name": "echo",
            "description": "Simple echo tool for all users",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to echo back"}
                },
                "required": ["text"]
            }
        },
    ]
}

# ============================================================
# Create user-specific server
# ============================================================
def create_user_server(user_id: str) -> Server:
    """Create a server instance for a specific user"""
    server = Server(f"sample-tools-sse-user-{user_id}")
    
    # Get user-specific resources and tools
    resources = USER_RESOURCES.get(user_id, USER_RESOURCES["default"])
    tools_config = USER_TOOLS.get(user_id, USER_TOOLS["default"])
    
    # Register list_tools handler
    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return [Tool(**tool_config) for tool_config in tools_config]
    
    # Register call_tool handler
    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None):
        if arguments is None:
            arguments = {}
        
        logger.info(f"User {user_id} calling tool: {name} with args: {arguments}")
        
        # User 1 specific tools
        if user_id == "1":
            if name == "calculate_sum":
                a = arguments.get("a", 0)
                b = arguments.get("b", 0)
                result = a + b
                return [TextContent(type="text", text=f"Sum of {a} and {b} is {result}")]
            
            elif name == "get_user_data":
                data_type = arguments.get("data_type", "unknown")
                return [TextContent(type="text", text=f"User 1 data for type '{data_type}': Sample data here...")]
        
        # User 2 specific tools
        elif user_id == "2":
            if name == "send_notification":
                message = arguments.get("message", "")
                priority = arguments.get("priority", "normal")
                return [TextContent(type="text", text=f"Notification sent with priority '{priority}': {message}")]
            
            elif name == "query_database":
                query = arguments.get("query", "")
                return [TextContent(type="text", text=f"Query executed for User 2: {query}\nResults: [Sample results...]")]
        
        # Default tool (echo)
        if name == "echo":
            text = arguments.get("text", "")
            return [TextContent(type="text", text=f"Echo for user {user_id}: {text}")]
        
        raise ValueError(f"Unknown tool: {name}")
    
    # Register list_resources handler
    @server.list_resources()
    async def handle_list_resources() -> list[Resource]:
        return resources
    
    # Register read_resource handler
    @server.read_resource()
    async def handle_read_resource(uri: str):
        for res in resources:
            if res.uri == uri:
                return f"Content of '{res.name}' for user {user_id}: Lorem ipsum dolor sit amet..."
        return f"Resource '{uri}' not found for user {user_id}."
    
    # Register list_prompts handler
    @server.list_prompts()
    async def handle_list_prompts() -> list[Prompt]:
        return [
            Prompt(
                name="summarize",
                description=f"Summarize content (User {user_id})",
                arguments=[PromptArgument(name="resource_uri", description="Resource to summarize", required=True)]
            ),
        ]
    
    # Register get_prompt handler
    @server.get_prompt()
    async def handle_get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        if arguments is None:
            arguments = {}
        resource_uri = arguments.get("resource_uri", "unknown resource")
        
        if name == "summarize":
            return GetPromptResult(
                description=f"Summarize content for user {user_id}",
                messages=[
                    PromptMessage(role="assistant", content=TextContent(type="text", text="Provide a clear summary.")),
                    PromptMessage(role="user", content=TextContent(type="text", text=f"Summarize: {resource_uri}"))
                ]
            )
        else:
            raise ValueError(f"Unknown prompt: {name}")
    
    return server

# ============================================================
# SSE Transport Setup
# ============================================================
# Create SSE transport - this will handle messages at /messages/
sse = SseServerTransport("/messages/")

# ============================================================
# SSE Endpoint Handler
# ============================================================
async def handle_sse(request: Request):
    """Handle SSE connections with user-specific servers"""
    
    # Get user_id from header
    user_id = request.headers.get("user_id", "default")
    logger.info(f"New SSE connection received for user: {user_id}")
    
    # Create or get user-specific server
    if user_id not in user_servers:
        user_servers[user_id] = create_user_server(user_id)
        logger.info(f"Created new server instance for user: {user_id}")
    
    server = user_servers[user_id]
    
    # Use connect_sse as an async context manager to get streams
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(
            streams[0],
            streams[1],
            InitializationOptions(
                server_name=f"sample-tools-mcp-sse-user-{user_id}",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )
    
    # Return empty response after connection ends
    return Response()

# ============================================================
# Health check endpoint
# ============================================================
async def health_check(request: Request):
    """Health check endpoint"""
    return JSONResponse({
        "status": "healthy",
        "active_users": list(user_servers.keys()),
        "total_connections": len(user_servers)
    })

# ============================================================
# Starlette App Setup
# ============================================================
app = Starlette(
    debug=True,
    routes=[
        Route("/health", endpoint=health_check, methods=["GET"]),
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
    ],
)

# ============================================================
# Run server
# ============================================================
def main():
    """Run the SSE server"""
    host = "0.0.0.0"  # Listen on all interfaces for remote access
    port = 8000
    
    logger.info(f"Starting MCP SSE server on {host}:{port}")
    logger.info(f"SSE endpoint available at: http://{host}:{port}/sse")
    logger.info(f"Message endpoint available at: http://{host}:{port}/messages/")
    logger.info(f"Health check available at: http://{host}:{port}/health")
    logger.info("")
    logger.info("Usage:")
    logger.info("  - Connect with user_id=1 header for User 1 tools (calculate_sum, get_user_data)")
    logger.info("  - Connect with user_id=2 header for User 2 tools (send_notification, query_database)")
    logger.info("  - Connect without header for default tools (echo)")
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )

if __name__ == "__main__":
    main()