#!/usr/bin/env python3
"""
OpenAPI to Postman Converter
Converts OpenAPI/Swagger specifications to Postman Collection v2.1 format
with automatic bearer token management and environment variables.
"""

import json
import os
import requests
from typing import Dict, List, Any, Optional


class OpenAPIToPostmanConverter:
    """Converts OpenAPI specs to Postman collections with auto-token management."""
    
    def __init__(self, config_file: str = "config.json"):
        """Initialize converter with configuration file."""
        self.config = self._load_config(config_file)
        self.openapi_spec = None
        self.postman_collection = None
        self.postman_environments = None
        self.project_folder = None
    
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
    
    def _is_public_endpoint(self, path: str) -> bool:
        """Check if endpoint is public (doesn't require authentication)."""
        public_paths = [
            "/common/signup",
            "/common/login",
            "/common/verify",
            "/",
            "/docs",
            "/openapi.json",
            "/redoc"
        ]
        return any(path.startswith(public_path) for public_path in public_paths)
    
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
        
        # Set authentication: ALL endpoints get bearer auth EXCEPT public endpoints
        # This ensures bearer token is always forwarded unless explicitly excluded
        is_public = self._is_public_endpoint(path)
        
        if not is_public:
            # Explicitly set bearer auth for all non-public endpoints
            # This ensures token is always forwarded, even if OpenAPI doesn't specify security
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
        else:
            # Explicitly set "no auth" for public endpoints to override collection-level auth
            request_item["request"]["auth"] = {
                "type": "noauth"
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
                    
                    # Special handling for /common/verify endpoint
                    if path == "/common/verify":
                        # Use {{mobile}} variable for mobile number
                        if "mobile" in example_body:
                            example_body["mobile"] = "{{mobile}}"
                        # Set default OTP to 1234 for easy testing
                        if "otp" in example_body:
                            example_body["otp"] = "1234"
                    
                    request_item["request"]["body"] = {
                        "mode": "raw",
                        "raw": json.dumps(example_body, indent=2),
                        "options": {
                            "raw": {
                                "language": "json"
                            }
                        }
                    }
        
        # Add pre-request script for verify endpoint to auto-fill mobile
        if path == "/common/verify":
            if "event" not in request_item:
                request_item["event"] = []
            
            request_item["event"].append({
                "listen": "prerequest",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "// Auto-fill mobile number from environment",
                        "// This ensures the mobile from login is automatically used in verify request",
                        "",
                        "const savedMobile = pm.environment.get('mobile');",
                        "",
                        "if (savedMobile && savedMobile.trim() !== '') {",
                        "    try {",
                        "        // Get current body - handle both string and object formats",
                        "        let bodyText = pm.request.body ? pm.request.body.raw : '{}';",
                        "        if (!bodyText || bodyText.trim() === '') {",
                        "            bodyText = '{\"mobile\": \"\", \"otp\": \"1234\"}';",
                        "        }",
                        "        ",
                        "        // Replace {{mobile}} variable first",
                        "        bodyText = bodyText.replace(/\\{\\{mobile\\}\\}/g, savedMobile);",
                        "        ",
                        "        // Parse JSON",
                        "        let body = {};",
                        "        try {",
                        "            body = JSON.parse(bodyText);",
                        "        } catch (e) {",
                        "            // If parsing fails, create new body",
                        "            body = { mobile: savedMobile, otp: '1234' };",
                        "        }",
                        "        ",
                        "        // Force update mobile field",
                        "        body.mobile = savedMobile;",
                        "        ",
                        "        // Ensure OTP is set",
                        "        if (!body.otp) {",
                        "            body.otp = '1234';",
                        "        }",
                        "        ",
                        "        // Update request body - use the correct method",
                        "        const updatedBody = JSON.stringify(body, null, 2);",
                        "        pm.request.body.update({",
                        "            mode: 'raw',",
                        "            raw: updatedBody,",
                        "            options: { raw: { language: 'json' } }",
                        "        });",
                        "        ",
                        "        console.log(`✅ Auto-filled mobile number: ${savedMobile}`);",
                        "        console.log(`✅ Request body updated successfully`);",
                        "        console.log(`✅ Verify request ready - OTP is pre-filled as 1234`);",
                        "    } catch (e) {",
                        "        console.log('❌ Error auto-filling mobile:', e.message);",
                        "        console.log('Stack:', e.stack);",
                        "        console.log('ℹ️  Please enter mobile number manually');",
                        "    }",
                        "} else {",
                        "    console.log('⚠️  WARNING: No mobile number found in environment!');",
                        "    console.log('⚠️  Please call /common/login first to save mobile number');",
                        "    console.log('ℹ️  Current environment:', pm.environment.name);",
                        "    console.log('ℹ️  Available variables:', Object.keys(pm.environment.toObject()));",
                        "}"
                    ]
                }
            })
        
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
                        "// Pre-request Script: Token Validation and Bearer Auth Setup",
                        "// This script runs before every request in the collection",
                        "",
                        "const token = pm.environment.get('access_token');",
                        "const tokenExpiry = pm.environment.get('token_expiry');",
                        "const requestUrl = pm.request.url.toString();",
                        "const isPublicEndpoint = requestUrl.includes('/common/login') || ",
                        "                          requestUrl.includes('/common/verify') || ",
                        "                          requestUrl.includes('/common/signup') || ",
                        "                          requestUrl === pm.environment.get('base_url') + '/' || ",
                        "                          requestUrl.includes('/docs') || ",
                        "                          requestUrl.includes('/openapi.json');",
                        "",
                        "// Only check token for non-public endpoints",
                        "if (!isPublicEndpoint) {",
                        "    if (!token || token.trim() === '') {",
                        "        console.log('⚠️  WARNING: No access token found!');",
                        "        console.log('⚠️  This request may fail with 401 Unauthorized');",
                        "        console.log('⚠️  Please login first using /common/login → /common/verify');",
                        "    } else if (tokenExpiry && Date.now() > parseInt(tokenExpiry)) {",
                        "        console.log('⚠️  WARNING: Access token has expired!');",
                        "        console.log('⚠️  Please login again using /common/login → /common/verify');",
                        "        pm.environment.set('access_token', '');",
                        "    } else {",
                        "        // Verify bearer auth is configured",
                        "        const auth = pm.request.auth;",
                        "        if (auth && auth.type === 'bearer') {",
                        "            console.log('✅ Bearer token will be sent in Authorization header');",
                        "            console.log(`✅ Token length: ${token.length} characters`);",
                        "        } else {",
                        "            console.log('⚠️  WARNING: Bearer auth not configured for this request!');",
                        "            console.log('⚠️  Token exists but may not be forwarded to API');",
                        "        }",
                        "    }",
                        "} else {",
                        "    console.log('ℹ️  Public endpoint - no authentication required');",
                        "}"
                    ]
                }
            },
            {
                "listen": "test",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "// Test Script: Auto-save Authentication Token and Role",
                        "// This script runs after every response in the collection",
                        "",
                        "if (pm.response.code === 200) {",
                        "    try {",
                        "        const response = pm.response.json();",
                        "        const requestUrl = pm.request.url.toString();",
                        "        let tokenSaved = false;",
                        "        let roleSaved = false;",
                        "        ",
                        "        // Helper function to extract token from various locations",
                        "        function extractToken(obj) {",
                        "            if (!obj) return null;",
                        "            return obj.access_token || obj.token || obj.accessToken || null;",
                        "        }",
                        "        ",
                        "        // Helper function to extract role from various locations",
                        "        function extractRole(obj) {",
                        "            if (!obj) return null;",
                        "            return obj.role || null;",
                        "        }",
                        "        ",
                        "        // Try to extract access_token from multiple possible locations",
                        "        let accessToken = extractToken(response) || extractToken(response.data) || extractToken(response.result);",
                        "        ",
                        "        if (accessToken) {",
                        "            pm.environment.set('access_token', accessToken);",
                        "            tokenSaved = true;",
                        "            ",
                        "            // Set token expiry (default: 24 hours)",
                        "            const expiryTime = Date.now() + (24 * 60 * 60 * 1000);",
                        "            pm.environment.set('token_expiry', expiryTime.toString());",
                        "            ",
                        "            console.log('✅ Access token saved to environment');",
                        "            console.log('✅ Token expiry set to 24 hours from now');",
                        "        }",
                        "        ",
                        "        // Extract and save role (especially from /common/verify endpoint)",
                        "        let role = extractRole(response) || extractRole(response.data) || extractRole(response.result);",
                        "        ",
                        "        if (role) {",
                        "            // Normalize role to lowercase",
                        "            role = role.toLowerCase();",
                        "            pm.environment.set('role', role);",
                        "            roleSaved = true;",
                        "            console.log(`✅ Role saved to environment: ${role}`);",
                        "            ",
                        "            // Log which environment should be used",
                        "            const currentEnv = pm.environment.name || 'current environment';",
                        "            if (currentEnv.toLowerCase().includes(role)) {",
                        "                console.log(`✅ Token saved to correct environment: ${currentEnv}`);",
                        "            } else {",
                        "                console.log(`⚠️  Warning: Role is '${role}' but current environment is '${currentEnv}'`);",
                        "                console.log(`   Consider switching to ${role} environment`);",
                        "            }",
                        "        }",
                        "        ",
                        "        // Auto-save user_id if present",
                        "        if (response.user_id) {",
                        "            pm.environment.set('user_id', response.user_id.toString());",
                        "            console.log('✅ User ID saved to environment');",
                        "        } else if (response.data && response.data.user_id) {",
                        "            pm.environment.set('user_id', response.data.user_id.toString());",
                        "            console.log('✅ User ID saved from data field');",
                        "        }",
                        "        ",
                        "        // Save mobile number from /common/login request",
                        "        if (requestUrl.includes('/common/login')) {",
                        "            try {",
                        "                const requestBody = pm.request.body.raw;",
                        "                if (requestBody) {",
                        "                    const loginData = JSON.parse(requestBody);",
                        "                    if (loginData.mobile) {",
                        "                        const mobileValue = loginData.mobile.toString().trim();",
                        "                        pm.environment.set('mobile', mobileValue);",
                        "                        console.log(`✅ Mobile number saved to environment: ${mobileValue}`);",
                        "                        console.log(`✅ This mobile will be auto-filled in verify request`);",
                        "                        console.log(`✅ You can now call /common/verify without entering mobile`);",
                        "                    } else {",
                        "                        console.log('⚠️  No mobile field found in login request body');",
                        "                    }",
                        "                } else {",
                        "                    console.log('⚠️  Login request body is empty');",
                        "                }",
                        "            } catch (e) {",
                        "                console.log('⚠️  Could not parse login request body:', e.message);",
                        "            }",
                        "        }",
                        "        ",
                        "        // Special handling for /common/verify endpoint",
                        "        if (requestUrl.includes('/common/verify')) {",
                        "            if (tokenSaved) {",
                        "                console.log('🎉 Login successful! Bearer token is ready to use.');",
                        "                if (roleSaved) {",
                        "                    console.log(`🎉 Role detected: ${role}. Make sure you're using the ${role} environment.`);",
                        "                }",
                        "            } else {",
                        "                console.log('⚠️  Warning: /common/verify response received but no access_token found');",
                        "                console.log('Response structure:', JSON.stringify(response, null, 2));",
                        "            }",
                        "        }",
                        "        ",
                        "        // Log if no token found but response is successful",
                        "        if (!tokenSaved && requestUrl.includes('/common/verify')) {",
                        "            console.log('⚠️  No access_token found in verify response');",
                        "            console.log('Response keys:', Object.keys(response));",
                        "        }",
                        "    } catch (e) {",
                        "        console.log('❌ Error parsing response:', e.message);",
                        "        console.log('Response text:', pm.response.text());",
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
    
    def generate_environments(self) -> bool:
        """Generate Postman environment files for Admin, Teacher, and Student roles."""
        if not self.openapi_spec:
            print("✗ No OpenAPI specification loaded")
            return False
        
        api_title = self.openapi_spec.get("info", {}).get("title", "API")
        
        # Define roles
        roles = ["Admin", "Teacher", "Student"]
        self.postman_environments = []
        
        for role in roles:
            environment = {
                "id": f"auto-generated-env-{role.lower()}",
                "name": f"{api_title} Environment ({role})",
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
                    },
                    {
                        "key": "role",
                        "value": role.lower(),
                        "type": "default",
                        "enabled": True
                    },
                    {
                        "key": "mobile",
                        "value": "",
                        "type": "default",
                        "enabled": True
                    }
                ],
                "_postman_variable_scope": "environment",
                "_postman_exported_at": "",
                "_postman_exported_using": "OpenAPI to Postman Converter"
            }
            self.postman_environments.append(environment)
        
        print(f"✓ Generated {len(roles)} environments: {', '.join([f'{r}' for r in roles])}")
        return True
    
    def _get_project_name(self) -> str:
        """Get the project name from OpenAPI spec title."""
        if not self.openapi_spec:
            return "API_Project"
        
        api_title = self.openapi_spec.get("info", {}).get("title", "API_Project")
        # Sanitize folder name: remove special characters, replace spaces with underscores
        folder_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in api_title)
        folder_name = folder_name.replace(' ', '_').strip('_')
        return folder_name if folder_name else "API_Project"
    
    def save_files(self) -> bool:
        """Save Postman collection and environment files in JSON/PROJECT_NAME folder."""
        if not self.postman_collection or not self.postman_environments:
            print("✗ No collection or environments to save")
            return False
        
        try:
            # Get project name from OpenAPI spec
            project_name = self._get_project_name()
            
            # Create nested folder structure: JSON/PROJECT_NAME/
            output_folder = os.path.join("JSON", project_name)
            
            # Create folder if it doesn't exist
            if not os.path.exists(output_folder):
                os.makedirs(output_folder)
                print(f"✓ Created output folder: {output_folder}/")
            
            # Save collection
            collection_file = os.path.join(output_folder, self.config["output_collection"])
            with open(collection_file, 'w', encoding='utf-8') as f:
                json.dump(self.postman_collection, f, indent=2, ensure_ascii=False)
            print(f"✓ Collection saved: {collection_file}")
            
            # Save environments (one for each role)
            roles = ["admin", "teacher", "student"]
            for i, role in enumerate(roles):
                environment_file = os.path.join(output_folder, f"postman_environment_{role}.json")
                with open(environment_file, 'w', encoding='utf-8') as f:
                    json.dump(self.postman_environments[i], f, indent=2, ensure_ascii=False)
                print(f"✓ Environment saved: {environment_file}")
            
            # Store folder name for success message
            self.project_folder = output_folder
            
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
        
        # Step 3: Generate environments
        if not self.generate_environments():
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
        print(f"📁 All files saved in: {self.project_folder}/")
        print()
        print("Generated Files:")
        print(f"  📄 {self.project_folder}/{self.config['output_collection']}")
        print(f"  📄 {self.project_folder}/postman_environment_admin.json")
        print(f"  📄 {self.project_folder}/postman_environment_teacher.json")
        print(f"  📄 {self.project_folder}/postman_environment_student.json")
        print()
        print("=" * 70)
        print("Next Steps:")
        print("=" * 70)
        print("  1. Open Postman")
        print(f"  2. Import all 4 files from '{self.project_folder}/' folder")
        print("     (collection + 3 environments)")
        print()
        print("  3. Login for each role:")
        print("     • Select 'Admin' environment → Login with admin credentials")
        print("     • Select 'Teacher' environment → Login with teacher credentials")
        print("     • Select 'Student' environment → Login with student credentials")
        print()
        print("  4. Switch between roles instantly using environment dropdown!")
        print("     Each role maintains its own token - no need to re-login!")
        print()
        print("Authentication Flow:")
        print("  POST /common/login   → Get OTP")
        print("  POST /common/verify  → Token auto-saved ✨")
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

