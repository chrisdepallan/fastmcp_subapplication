import json
import asyncio
from typing import Any, Dict, List, Optional
import httpx
from mcp.server.fastmcp import FastMCP
import os
import logging

# Configuration - provide sensible defaults for remote deployments
API_BASE_URL    = "https://outbound.byteflow.bot"
# Allow specifying an explicit openapi file path; default to the repo file if not set
# OPENAPI_FILE_PATH = os.getenv("OPENAPI_FILE", "outbound.byteflow.bot_openapi.json")

# Load the swagger doc from the file path (fail-fast with clear message)

SWAGGER_DOC = {"openapi":"3.1.0","info":{"title":"AI Calling Dashboard","version":"2.1.0"},"paths":{"/transcript/update":{"post":{"summary":"Receive Transcript Update","description":"Receive transcript updates from the main API and broadcast to subscribed clients","operationId":"receive_transcript_update_transcript_update_post","responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}}}}},"/proxy/api/{path}":{"patch":{"summary":"Proxy Api","description":"Proxy API calls to the main API server","operationId":"proxy_api_proxy_api__path__patch","parameters":[{"name":"path","in":"path","required":"true","schema":{"type":"string","title":"Path"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}},"delete":{"summary":"Proxy Api","description":"Proxy API calls to the main API server","operationId":"proxy_api_proxy_api__path__patch","parameters":[{"name":"path","in":"path","required":"true","schema":{"type":"string","title":"Path"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}},"get":{"summary":"Proxy Api","description":"Proxy API calls to the main API server","operationId":"proxy_api_proxy_api__path__patch","parameters":[{"name":"path","in":"path","required":"true","schema":{"type":"string","title":"Path"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}},"put":{"summary":"Proxy Api","description":"Proxy API calls to the main API server","operationId":"proxy_api_proxy_api__path__patch","parameters":[{"name":"path","in":"path","required":"true","schema":{"type":"string","title":"Path"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}},"post":{"summary":"Proxy Api","description":"Proxy API calls to the main API server","operationId":"proxy_api_proxy_api__path__patch","parameters":[{"name":"path","in":"path","required":"true","schema":{"type":"string","title":"Path"}}],"responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}},"422":{"description":"Validation Error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/HTTPValidationError"}}}}}}},"/broadcast/call-status":{"post":{"summary":"Broadcast Call Status","description":"Broadcast call status updates to all connected WebSocket clients","operationId":"broadcast_call_status_broadcast_call_status_post","responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}}}}},"/health":{"get":{"summary":"Health Check","description":"Dashboard health check","operationId":"health_check_health_get","responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}}}}},"/":{"get":{"summary":"Dashboard","description":"Serve the dashboard HTML","operationId":"dashboard__get","responses":{"200":{"description":"Successful Response","content":{"application/json":{"schema":{}}}}}}}},"components":{"schemas":{"HTTPValidationError":{"properties":{"detail":{"items":{"$ref":"#/components/schemas/ValidationError"},"type":"array","title":"Detail"}},"type":"object","title":"HTTPValidationError"},"ValidationError":{"properties":{"loc":{"items":{"anyOf":[{"type":"string"},{"type":"integer"}]},"type":"array","title":"Location"},"msg":{"type":"string","title":"Message"},"type":{"type":"string","title":"Error Type"}},"type":"object","required":["loc","msg","type"],"title":"ValidationError"}}}}
        

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
        """Extract properties and required fields from a schema, ensuring all objects have additionalProperties: false"""
        resolved_schema = self.resolve_ref(schema)
        
        # Handle allOf/anyOf/oneOf by merging/flattening (simplified: take first object schema)
        if "allOf" in resolved_schema:
            # Merge all schemas in allOf
            merged_props = {}
            merged_required = []
            for sub_schema in resolved_schema["allOf"]:
                sub_resolved = self.resolve_ref(sub_schema)
                merged_props.update(sub_resolved.get("properties", {}))
                merged_required.extend(sub_resolved.get("required", []))
            resolved_schema = {
                "type": "object",
                "properties": merged_props,
                "required": list(set(merged_required)),
                "additionalProperties": False
            }
        elif "anyOf" in resolved_schema or "oneOf" in resolved_schema:
            # Take the first non-null object schema
            options = resolved_schema.get("anyOf") or resolved_schema.get("oneOf")
            for option in options:
                opt_resolved = self.resolve_ref(option)
                if opt_resolved.get("type") == "object" or "properties" in opt_resolved:
                    resolved_schema = opt_resolved
                    break
        
        properties = {}
        required = resolved_schema.get("required", [])
        
        schema_props = resolved_schema.get("properties", {})
        for prop_name, prop_spec in schema_props.items():
            # Resolve any $ref in the property spec
            resolved_prop = self.resolve_ref(prop_spec)
            
            # If this property is itself an object with properties, preserve the full schema
            if resolved_prop.get("type") == "object" or "properties" in resolved_prop:
                nested_schema = {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False
                }
                # Recursively extract nested properties
                nested_props, nested_required = self.extract_properties_from_schema(resolved_prop)
                nested_schema["properties"] = nested_props
                if nested_required:
                    nested_schema["required"] = nested_required
                if "description" in resolved_prop or "title" in resolved_prop:
                    nested_schema["description"] = resolved_prop.get("description") or resolved_prop.get("title", "")
                properties[prop_name] = nested_schema
                continue
            
            # Handle anyOf/oneOf at property level (e.g., nullable fields)
            if "anyOf" in resolved_prop or "oneOf" in resolved_prop:
                # Take the first non-null type
                options = resolved_prop.get("anyOf") or resolved_prop.get("oneOf")
                prop_type = "string"
                for option in options:
                    opt_type = option.get("type")
                    if opt_type and opt_type != "null":
                        prop_type = opt_type
                        break
            else:
                prop_type = resolved_prop.get("type", "string")
            
            properties[prop_name] = {
                "type": prop_type,
                "description": resolved_prop.get("title", resolved_prop.get("description", ""))
            }
        
        return properties, required
    
    def parse_swagger_to_tools(self) -> List[Dict[str, Any]]:
        """Parse Swagger doc and create tool definitions"""
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
    
    def _create_tool_from_spec(self, path: str, method: str, spec: Dict) -> Optional[Dict[str, Any]]:
        """Create a single tool definition from OpenAPI spec"""
        operation_id = spec.get("operationId", f"{method}_{path.replace('/', '_')}")
        description = spec.get("summary", spec.get("description", f"{method.upper()} {path}"))
        
        # Build input schema - ensure top-level object has additionalProperties: false
        input_schema = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False
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
        
        return {
            "name": operation_id,
            "description": description,
            "inputSchema": input_schema,
            "path": path,
            "method": method,
            "spec": spec
        }
    
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
                "status_code": getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None,
                "details": getattr(e.response, 'text', None) if hasattr(e, 'response') else None
            })
    
    async def close(self):
        """Close HTTP client"""
        await self.http_client.aclose()

# Determine port early and initialize FastMCP Server in streamable-http mode
port_env = os.environ.get("PORT")
try:
    PORT = int(port_env) if port_env is not None else 10000
except Exception:
    logging.warning("Invalid PORT %s, falling back to 10000", port_env)
    PORT = 10000

converter = SwaggerToMCPConverter(SWAGGER_DOC, API_BASE_URL)
# Initialize FastMCP Server with host/port so mcp.run can start it directly.
mcp = FastMCP("swagger-api-mcp", host="0.0.0.0", port=PORT)


# Dynamically register tools from OpenAPI spec
def register_tools():
    """Register all tools from the OpenAPI spec"""
    tools = converter.parse_swagger_to_tools()
    
    for tool_def in tools:
        tool_name = tool_def["name"]
        tool_description = tool_def["description"]
        
        # Create a closure to capture tool_name for each tool
        def make_tool_handler(name: str):
            async def tool_handler(**handler_kwargs) -> str:
                """Dynamic tool handler that accepts either a single 'kwargs' dict
                or flat keyword args from different client invocations."""
                # Support callers that send {"kwargs": {...}}
                if len(handler_kwargs) == 1 and 'kwargs' in handler_kwargs and isinstance(handler_kwargs['kwargs'], dict):
                    args = handler_kwargs['kwargs']
                else:
                    args = handler_kwargs
                return await converter.execute_tool(name, args)
            return tool_handler
        
        # Register the tool with FastMCP
        mcp.tool(
            name=tool_name,
            description=tool_description
        )(make_tool_handler(tool_name))


# Register all tools on startup
register_tools()

# Ensure converter http client is closed when the MCP shuts down
async def _shutdown_hook():
    try:
        await converter.close()
    except Exception:
        logging.exception("Error closing converter HTTP client")

def _parse_port(val: str | int | None, default: int = 10000) -> int:
    try:
        if val is None:
            return default
        return int(val)
    except Exception:
        logging.warning("Invalid PORT %s, falling back to %s", val, default)
        return default


app = mcp.streamable_http_app()


if __name__ == "__main__":
    # Run the FastMCP server in streamable-http transport mode (matching working sample)
    mcp.run(transport="streamable-http")