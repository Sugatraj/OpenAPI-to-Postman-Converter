#!/usr/bin/env python3
"""
OpenAPI to Postman Converter
Converts OpenAPI/Swagger specifications to Postman Collection v2.1 format
with automatic bearer token management and environment variables.
"""

import json
import requests
from typing import Dict, List, Any, Optional


class OpenAPIToPostmanConverter:
    """Converts OpenAPI specs to Postman collections with auto-token management."""
    
    def __init__(self, config_file: str = "config.json"):
        """Initialize converter with configuration file."""
        self.config = self._load_config(config_file)
        self.openapi_spec = None
        self.postman_collection = None
        self.postman_environment = None
    
    def _load_config(self, config_file: str) -> Dict:
        """Load configuration from JSON file."""
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            print(f"✓ Configuration loaded from {config_file}")
            return config
        except FileNotFoundError:
            print(f"✗ Configuration file not found: {config_file}")
            raise
        except json.JSONDecodeError as e:
            print(f"✗ Invalid JSON in configuration file: {e}")
            raise
    
    def fetch_openapi_spec(self) -> bool:
        """Fetch OpenAPI specification from configured URL."""
        try:
            print(f"Fetching OpenAPI spec from {self.config['openapi_url']}...")
            response = requests.get(self.config['openapi_url'], timeout=10)
            response.raise_for_status()
            self.openapi_spec = response.json()
            
            api_title = self.openapi_spec.get('info', {}).get('title', 'API')
            api_version = self.openapi_spec.get('info', {}).get('version', 'unknown')
            print(f"✓ Successfully fetched: {api_title} v{api_version}")
            return True
        except requests.RequestException as e:
            print(f"✗ Error fetching OpenAPI spec: {e}")
            return False
        except json.JSONDecodeError:
            print("✗ Invalid JSON response from OpenAPI URL")
            return False
    
    def _resolve_schema_ref(self, schema: Dict) -> Dict:
        """Resolve $ref references in schemas."""
        if "$ref" in schema:
            ref_path = schema["$ref"].split("/")
            resolved = self.openapi_spec
            for part in ref_path:
                if part != "#":
                    resolved = resolved.get(part, {})
            return resolved
        return schema
    
    def _generate_example_body(self, schema: Dict) -> Any:
        """Generate example request body from OpenAPI schema."""
        schema = self._resolve_schema_ref(schema)
        
        properties = schema.get("properties", {})
        required_fields = schema.get("required", [])
        example = {}
        
        for prop_name, prop_details in properties.items():
            # Check for existing examples
            if "example" in prop_details:
                example[prop_name] = prop_details["example"]
                continue
            
            # Handle anyOf type definitions
            if "anyOf" in prop_details:
                types = [t.get("type") for t in prop_details["anyOf"] if "type" in t]
                prop_type = types[0] if types else "string"
            else:
                prop_type = prop_details.get("type", "string")
            
            # Generate example based on type
            if prop_type == "string":
                if "enum" in prop_details:
                    example[prop_name] = prop_details["enum"][0]
                else:
                    example[prop_name] = f"<{prop_name}>"
            elif prop_type == "integer":
                example[prop_name] = 0
            elif prop_type == "number":
                example[prop_name] = 0.0
            elif prop_type == "boolean":
                example[prop_name] = False
            elif prop_type == "array":
                items_schema = prop_details.get("items", {})
                if items_schema:
                    example[prop_name] = [self._generate_example_body(items_schema)]
                else:
                    example[prop_name] = []
            elif prop_type == "object":
                example[prop_name] = self._generate_example_body(prop_details)
        
        return example
    
    def _convert_endpoint_to_postman(self, path: str, method: str, details: Dict) -> Dict:
        """Convert a single API endpoint to Postman request format."""
        request_item = {
            "name": details.get("summary", f"{method.upper()} {path}"),
            "request": {
                "method": method.upper(),
                "header": [
                    {
                        "key": "Content-Type",
                        "value": "application/json",
                        "type": "text"
                    }
                ],
                "url": {
                    "raw": f"{{{{base_url}}}}{path}",
                    "host": ["{{base_url}}"],
                    "path": [p for p in path.split("/") if p]
                }
            }
        }
        
        # Add description if available
        if details.get("description"):
            request_item["request"]["description"] = details["description"]
        
        # Add authentication for secured endpoints
        if "security" in details and details["security"]:
            request_item["request"]["auth"] = {
                "type": "bearer",
                "bearer": [
                    {
                        "key": "token",
                        "value": "{{access_token}}",
                        "type": "string"
                    }
                ]
            }
        
        # Handle path and query parameters
        if "parameters" in details:
            query_params = []
            path_variables = []
            
            for param in details["parameters"]:
                param_in = param.get("in")
                param_name = param.get("name")
                param_desc = param.get("description", "")
                param_required = param.get("required", False)
                
                if param_in == "query":
                    query_params.append({
                        "key": param_name,
                        "value": "",
                        "description": param_desc,
                        "disabled": not param_required
                    })
                elif param_in == "path":
                    path_variables.append({
                        "key": param_name,
                        "value": "",
                        "description": param_desc
                    })
            
            if query_params:
                request_item["request"]["url"]["query"] = query_params
            if path_variables:
                request_item["request"]["url"]["variable"] = path_variables
        
        # Handle request body
        if "requestBody" in details:
            request_body = details["requestBody"]
            content = request_body.get("content", {})
            
            if "application/json" in content:
                json_content = content["application/json"]
                schema = json_content.get("schema", {})
                
                if schema:
                    example_body = self._generate_example_body(schema)
                    request_item["request"]["body"] = {
                        "mode": "raw",
                        "raw": json.dumps(example_body, indent=2),
                        "options": {
                            "raw": {
                                "language": "json"
                            }
                        }
                    }
        
        # Add empty response array (Postman format)
        request_item["response"] = []
        
        return request_item
    
    def _create_collection_scripts(self) -> List[Dict]:
        """Create collection-level pre-request and test scripts."""
        return [
            {
                "listen": "prerequest",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "// Pre-request Script: Token Validation",
                        "// This script runs before every request in the collection",
                        "",
                        "const token = pm.environment.get('access_token');",
                        "const tokenExpiry = pm.environment.get('token_expiry');",
                        "",
                        "// Check if token exists and is not expired",
                        "if (!token) {",
                        "    console.log('⚠️  No access token found. Please login first.');",
                        "} else if (tokenExpiry && Date.now() > parseInt(tokenExpiry)) {",
                        "    console.log('⚠️  Access token has expired. Please login again.');",
                        "    pm.environment.set('access_token', '');",
                        "} else {",
                        "    console.log('✓ Access token is valid');",
                        "}"
                    ]
                }
            },
            {
                "listen": "test",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "// Test Script: Auto-save Authentication Token",
                        "// This script runs after every response in the collection",
                        "",
                        "if (pm.response.code === 200) {",
                        "    try {",
                        "        const response = pm.response.json();",
                        "        ",
                        "        // Check for access_token in response root",
                        "        if (response.access_token) {",
                        "            pm.environment.set('access_token', response.access_token);",
                        "            console.log('✓ Access token saved to environment');",
                        "            ",
                        "            // Set token expiry (default: 24 hours)",
                        "            const expiryTime = Date.now() + (24 * 60 * 60 * 1000);",
                        "            pm.environment.set('token_expiry', expiryTime.toString());",
                        "            console.log('✓ Token expiry set to 24 hours from now');",
                        "        }",
                        "        ",
                        "        // Check for access_token in nested data object",
                        "        if (response.data && response.data.access_token) {",
                        "            pm.environment.set('access_token', response.data.access_token);",
                        "            console.log('✓ Access token saved from data field');",
                        "            ",
                        "            const expiryTime = Date.now() + (24 * 60 * 60 * 1000);",
                        "            pm.environment.set('token_expiry', expiryTime.toString());",
                        "        }",
                        "        ",
                        "        // Auto-save user_id if present",
                        "        if (response.user_id) {",
                        "            pm.environment.set('user_id', response.user_id.toString());",
                        "            console.log('✓ User ID saved to environment');",
                        "        }",
                        "        ",
                        "        if (response.data && response.data.user_id) {",
                        "            pm.environment.set('user_id', response.data.user_id.toString());",
                        "            console.log('✓ User ID saved from data field');",
                        "        }",
                        "    } catch (e) {",
                        "        // Response is not JSON or doesn't contain expected fields",
                        "    }",
                        "}"
                    ]
                }
            }
        ]
    
    def convert_to_postman(self) -> bool:
        """Convert OpenAPI specification to Postman Collection v2.1 format."""
        if not self.openapi_spec:
            print("✗ No OpenAPI specification loaded")
            return False
        
        print("Converting to Postman collection format...")
        
        info = self.openapi_spec.get("info", {})
        paths = self.openapi_spec.get("paths", {})
        
        # Initialize Postman collection structure
        self.postman_collection = {
            "info": {
                "name": info.get("title", "API Collection"),
                "description": info.get("description", "Imported from OpenAPI specification"),
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                "_exporter_id": "openapi-converter"
            },
            "item": [],
            "auth": {
                "type": "bearer",
                "bearer": [
                    {
                        "key": "token",
                        "value": "{{access_token}}",
                        "type": "string"
                    }
                ]
            },
            "event": self._create_collection_scripts(),
            "variable": [
                {
                    "key": "base_url",
                    "value": self.config["base_url"],
                    "type": "string"
                }
            ]
        }
        
        # Group endpoints by tags
        tag_groups = {}
        endpoint_count = 0
        
        for path, methods in paths.items():
            for method, details in methods.items():
                # Only process HTTP methods
                if method.lower() not in ["get", "post", "put", "delete", "patch", "options", "head"]:
                    continue
                
                # Get tag for grouping
                tags = details.get("tags", ["Default"])
                tag = tags[0] if tags else "Default"
                
                # Create tag group if it doesn't exist
                if tag not in tag_groups:
                    tag_groups[tag] = {
                        "name": tag,
                        "item": []
                    }
                
                # Convert endpoint to Postman format
                postman_request = self._convert_endpoint_to_postman(path, method, details)
                tag_groups[tag]["item"].append(postman_request)
                endpoint_count += 1
        
        # Add grouped items to collection
        self.postman_collection["item"] = list(tag_groups.values())
        
        print(f"✓ Converted {endpoint_count} endpoints into {len(tag_groups)} groups")
        return True
    
    def generate_environment(self) -> bool:
        """Generate Postman environment file."""
        if not self.openapi_spec:
            print("✗ No OpenAPI specification loaded")
            return False
        
        api_title = self.openapi_spec.get("info", {}).get("title", "API")
        
        self.postman_environment = {
            "id": "auto-generated-env",
            "name": f"{api_title} Environment",
            "values": [
                {
                    "key": "base_url",
                    "value": self.config["base_url"],
                    "type": "default",
                    "enabled": True
                },
                {
                    "key": "access_token",
                    "value": "",
                    "type": "secret",
                    "enabled": True
                },
                {
                    "key": "token_expiry",
                    "value": "",
                    "type": "default",
                    "enabled": True
                },
                {
                    "key": "user_id",
                    "value": "",
                    "type": "default",
                    "enabled": True
                }
            ],
            "_postman_variable_scope": "environment",
            "_postman_exported_at": "",
            "_postman_exported_using": "OpenAPI to Postman Converter"
        }
        
        print(f"✓ Generated environment: {api_title} Environment")
        return True
    
    def save_files(self) -> bool:
        """Save Postman collection and environment to JSON files."""
        if not self.postman_collection or not self.postman_environment:
            print("✗ No collection or environment to save")
            return False
        
        try:
            # Save collection
            collection_file = self.config["output_collection"]
            with open(collection_file, 'w', encoding='utf-8') as f:
                json.dump(self.postman_collection, f, indent=2, ensure_ascii=False)
            print(f"✓ Collection saved: {collection_file}")
            
            # Save environment
            environment_file = self.config["output_environment"]
            with open(environment_file, 'w', encoding='utf-8') as f:
                json.dump(self.postman_environment, f, indent=2, ensure_ascii=False)
            print(f"✓ Environment saved: {environment_file}")
            
            return True
        except IOError as e:
            print(f"✗ Error saving files: {e}")
            return False
    
    def run(self) -> bool:
        """Execute the full conversion process."""
        print("=" * 70)
        print("OpenAPI to Postman Converter")
        print("=" * 70)
        print()
        
        # Step 1: Fetch OpenAPI spec
        if not self.fetch_openapi_spec():
            return False
        print()
        
        # Step 2: Convert to Postman format
        if not self.convert_to_postman():
            return False
        print()
        
        # Step 3: Generate environment
        if not self.generate_environment():
            return False
        print()
        
        # Step 4: Save files
        if not self.save_files():
            return False
        print()
        
        # Success message
        print("=" * 70)
        print("✓ Conversion Complete!")
        print("=" * 70)
        print()
        print("Next Steps:")
        print("  1. Open Postman")
        print(f"  2. Import '{self.config['output_collection']}'")
        print(f"  3. Import '{self.config['output_environment']}'")
        print("  4. Select the imported environment from the dropdown")
        print("  5. Call /common/login → /common/verify to get your token")
        print("  6. Token will be automatically saved and used in all requests!")
        print()
        
        return True


def main():
    """Main entry point for the converter."""
    try:
        converter = OpenAPIToPostmanConverter("config.json")
        success = converter.run()
        exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n✗ Conversion cancelled by user")
        exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        exit(1)


if __name__ == "__main__":
    main()

