#!/usr/bin/env python3
"""
Production-Ready MCP Server with Header-Based Authentication
Install: pip install mcp pyjwt cryptography python-dotenv
"""

import asyncio
import jwt
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, INVALID_PARAMS, INTERNAL_ERROR
from mcp.server.session import ServerSession

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(_name_)


@dataclass
class UserContext:
    """User context extracted from headers"""
    user_id: str
    role: str
    email: Optional[str] = None
    permissions: List[str] = None
    
    def _post_init_(self):
        if self.permissions is None:
            self.permissions = []


class AuthenticationError(Exception):
    """Raised when authentication fails"""
    pass


class AuthorizationError(Exception):
    """Raised when user lacks permission"""
    pass


class HeaderAuthenticator:
    """Handles authentication and authorization from headers"""
    
    def _init_(self, jwt_secret: str):
        self.jwt_secret = jwt_secret
        self.role_permissions = {
            "admin": ["basic_calculator", "advanced_calculator", "admin_reset", "user_profile", "data_export"],
            "developer": ["basic_calculator", "advanced_calculator", "user_profile"],
            "user": ["basic_calculator", "user_profile"],
            "guest": ["basic_calculator"]
        }
    
    def authenticate_from_headers(self, headers: Dict[str, str]) -> UserContext:
        """
        Authenticate user from headers.
        Supports multiple authentication methods.
        """
        # Method 1: JWT Token
        auth_header = headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            return self._authenticate_jwt(auth_header[7:])
        
        # Method 2: API Key
        api_key = headers.get("x-api-key")
        if api_key:
            return self._authenticate_api_key(api_key)
        
        # Method 3: Basic Auth (for demo/development)
        user_id = headers.get("x-user-id")
        user_role = headers.get("x-user-role")
        if user_id and user_role:
            logger.warning("Using basic header auth - not recommended for production")
            return self._authenticate_basic(user_id, user_role)
        
        raise AuthenticationError("No valid authentication credentials provided")
    
    def _authenticate_jwt(self, token: str) -> UserContext:
        """Authenticate using JWT token"""
        try:
            payload = jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
            
            # Verify token expiration
            if "exp" in payload and payload["exp"] < datetime.utcnow().timestamp():
                raise AuthenticationError("Token expired")
            
            user_id = payload.get("sub") or payload.get("user_id")
            role = payload.get("role", "guest")
            email = payload.get("email")
            custom_permissions = payload.get("permissions", [])
            
            if not user_id:
                raise AuthenticationError("Invalid token: missing user_id")
            
            # Get base permissions for role and add custom permissions
            permissions = list(set(
                self.role_permissions.get(role, []) + custom_permissions
            ))
            
            logger.info(f"JWT auth successful for user: {user_id}, role: {role}")
            return UserContext(
                user_id=user_id,
                role=role,
                email=email,
                permissions=permissions
            )
        
        except jwt.InvalidTokenError as e:
            logger.error(f"JWT validation failed: {e}")
            raise AuthenticationError(f"Invalid token: {str(e)}")
    
    def _authenticate_api_key(self, api_key: str) -> UserContext:
        """Authenticate using API key"""
        # In production, look up API key in database
        # This is a simplified example
        api_key_db = {
            "dev-key-12345": {"user_id": "dev-user", "role": "developer"},
            "admin-key-67890": {"user_id": "admin-user", "role": "admin"},
            "user-key-11111": {"user_id": "regular-user", "role": "user"}
        }
        
        if api_key not in api_key_db:
            raise AuthenticationError("Invalid API key")
        
        user_data = api_key_db[api_key]
        permissions = self.role_permissions.get(user_data["role"], [])
        
        logger.info(f"API key auth successful for user: {user_data['user_id']}")
        return UserContext(
            user_id=user_data["user_id"],
            role=user_data["role"],
            permissions=permissions
        )
    
    def _authenticate_basic(self, user_id: str, role: str) -> UserContext:
        """Basic header authentication (development only)"""
        if role not in self.role_permissions:
            role = "guest"
        
        permissions = self.role_permissions.get(role, [])
        return UserContext(
            user_id=user_id,
            role=role,
            permissions=permissions
        )
    
    def authorize_tool(self, user_context: UserContext, tool_name: str) -> bool:
        """Check if user has permission for tool"""
        return tool_name in user_context.permissions


class UserAwareMCPServer:
    """Production MCP Server with header-based authentication"""
    
    def _init_(self, jwt_secret: str):
        self.server = Server("secure-mcp-server")
        self.authenticator = HeaderAuthenticator(jwt_secret)
        self.user_contexts: Dict[str, UserContext] = {}  # session_id -> UserContext
        
        # Define all available tools
        self.all_tools = {
            "basic_calculator": Tool(
                name="basic_calculator",
                description="Basic calculator for simple math operations",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string", "enum": ["add", "subtract"]},
                        "a": {"type": "number"},
                        "b": {"type": "number"}
                    },
                    "required": ["operation", "a", "b"]
                }
            ),
            "advanced_calculator": Tool(
                name="advanced_calculator",
                description="Advanced calculator with multiply, divide, power",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "operation": {"type": "string", "enum": ["multiply", "divide", "power"]},
                        "a": {"type": "number"},
                        "b": {"type": "number"}
                    },
                    "required": ["operation", "a", "b"]
                }
            ),
            "admin_reset": Tool(
                name="admin_reset",
                description="Admin only: Reset system state",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "confirm": {"type": "boolean"},
                        "reason": {"type": "string"}
                    },
                    "required": ["confirm"]
                }
            ),
            "user_profile": Tool(
                name="user_profile",
                description="Get user profile information",
                inputSchema={
                    "type": "object",
                    "properties": {}
                }
            ),
            "data_export": Tool(
                name="data_export",
                description="Admin only: Export system data",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "format": {"type": "string", "enum": ["json", "csv"]},
                        "include_sensitive": {"type": "boolean"}
                    },
                    "required": ["format"]
                }
            )
        }
        
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Setup MCP request handlers"""
        
        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            """Return tools based on user permissions"""
            try:
                user_context = self._get_current_user_context()
                allowed_tools = [
                    self.all_tools[name] 
                    for name in user_context.permissions 
                    if name in self.all_tools
                ]
                
                logger.info(f"User {user_context.user_id} listed {len(allowed_tools)} tools")
                return allowed_tools
            
            except AuthenticationError as e:
                logger.error(f"Authentication failed in list_tools: {e}")
                return []
        
        @self.server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
            """Execute tool with authorization check"""
            try:
                user_context = self._get_current_user_context()
                
                # Authorization check
                if not self.authenticator.authorize_tool(user_context, name):
                    logger.warning(
                        f"Unauthorized tool access attempt: "
                        f"user={user_context.user_id}, tool={name}"
                    )
                    raise AuthorizationError(
                        f"User '{user_context.user_id}' with role '{user_context.role}' "
                        f"is not authorized to access tool '{name}'"
                    )
                
                # Execute tool
                logger.info(f"User {user_context.user_id} executing tool: {name}")
                result = await self._execute_tool(name, arguments, user_context)
                
                return [TextContent(type="text", text=str(result))]
            
            except AuthenticationError as e:
                logger.error(f"Authentication error: {e}")
                return [TextContent(
                    type="text",
                    text=f"Authentication failed: {str(e)}"
                )]
            
            except AuthorizationError as e:
                logger.error(f"Authorization error: {e}")
                return [TextContent(
                    type="text",
                    text=f"Authorization failed: {str(e)}"
                )]
            
            except Exception as e:
                logger.error(f"Tool execution error: {e}", exc_info=True)
                return [TextContent(
                    type="text",
                    text=f"Error executing tool: {str(e)}"
                )]
    
    def authenticate_session(self, session_id: str, headers: Dict[str, str]):
        """Authenticate and store user context for session"""
        try:
            user_context = self.authenticator.authenticate_from_headers(headers)
            self.user_contexts[session_id] = user_context
            logger.info(
                f"Session {session_id} authenticated: "
                f"user={user_context.user_id}, role={user_context.role}"
            )
        except AuthenticationError as e:
            logger.error(f"Session authentication failed: {e}")
            raise
    
    def _get_current_user_context(self) -> UserContext:
        """Get current user context from session"""
        # In production, extract session_id from current request context
        # For this example, we'll use a default session
        session_id = "current_session"
        
        if session_id not in self.user_contexts:
            raise AuthenticationError("No authenticated session found")
        
        return self.user_contexts[session_id]
    
    async def _execute_tool(
        self, 
        name: str, 
        arguments: Dict[str, Any],
        user_context: UserContext
    ) -> str:
        """Execute the requested tool"""
        if name == "basic_calculator":
            return self._basic_calc(arguments)
        elif name == "advanced_calculator":
            return self._advanced_calc(arguments)
        elif name == "admin_reset":
            return self._admin_reset(arguments, user_context)
        elif name == "user_profile":
            return self._user_profile(user_context)
        elif name == "data_export":
            return self._data_export(arguments, user_context)
        else:
            raise ValueError(f"Unknown tool: {name}")
    
    def _basic_calc(self, args: Dict[str, Any]) -> str:
        """Basic calculator implementation"""
        a, b = args["a"], args["b"]
        op = args["operation"]
        
        if op == "add":
            result = a + b
        elif op == "subtract":
            result = a - b
        else:
            raise ValueError("Invalid operation")
        
        return f"{a} {op} {b} = {result}"
    
    def _advanced_calc(self, args: Dict[str, Any]) -> str:
        """Advanced calculator implementation"""
        a, b = args["a"], args["b"]
        op = args["operation"]
        
        if op == "multiply":
            result = a * b
        elif op == "divide":
            if b == 0:
                raise ValueError("Division by zero")
            result = a / b
        elif op == "power":
            result = a ** b
        else:
            raise ValueError("Invalid operation")
        
        return f"{a} {op} {b} = {result}"
    
    def _admin_reset(self, args: Dict[str, Any], user: UserContext) -> str:
        """Admin reset implementation"""
        if not args.get("confirm"):
            return "Reset cancelled - confirmation required"
        
        reason = args.get("reason", "No reason provided")
        logger.warning(f"System reset by {user.user_id}: {reason}")
        
        return f"System reset executed by {user.user_id}. Reason: {reason}"
    
    def _user_profile(self, user: UserContext) -> str:
        """Get user profile"""
        return (
            f"User Profile:\n"
            f"  ID: {user.user_id}\n"
            f"  Role: {user.role}\n"
            f"  Email: {user.email or 'N/A'}\n"
            f"  Permissions: {', '.join(user.permissions)}"
        )
    
    def _data_export(self, args: Dict[str, Any], user: UserContext) -> str:
        """Export data (admin only)"""
        format_type = args["format"]
        include_sensitive = args.get("include_sensitive", False)
        
        logger.info(
            f"Data export by {user.user_id}: "
            f"format={format_type}, sensitive={include_sensitive}"
        )
        
        return (
            f"Data export initiated:\n"
            f"  Format: {format_type}\n"
            f"  Include sensitive: {include_sensitive}\n"
            f"  Requested by: {user.user_id}"
        )


async def main():
    """Main entry point with header authentication"""
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    # Load JWT secret from environment
    jwt_secret = os.getenv("JWT_SECRET", "your-secret-key-change-in-production")
    
    if jwt_secret == "your-secret-key-change-in-production":
        logger.warning("Using default JWT secret - CHANGE THIS IN PRODUCTION!")
    
    # Create server
    mcp_server = UserAwareMCPServer(jwt_secret)
    
    # Simulate extracting headers from the connection
    # In production, these would come from the actual MCP client connection
    headers = {
        # Option 1: JWT token
        # "authorization": "Bearer eyJ0eXAi...",
        
        # Option 2: API key
        # "x-api-key": "dev-key-12345",
        
        # Option 3: Basic headers (dev only)
        "x-user-id": os.getenv("MCP_USER_ID", "user123"),
        "x-user-role": os.getenv("MCP_USER_ROLE", "user")
    }
    
    # Authenticate the session
    try:
        mcp_server.authenticate_session("current_session", headers)
        logger.info("Server initialized and user authenticated")
    except AuthenticationError as e:
        logger.error(f"Failed to authenticate: {e}")
        return
    
    # Run the server
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.server.run(
            read_stream,
            write_stream,
            mcp_server.server.create_initialization_options()
        )


if _name_ == "_main_":
    asyncio.run(main())