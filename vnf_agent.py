import os
import json
import re
from typing import List, Dict, Any
from openai import OpenAI
from dotenv import load_dotenv

# --- 1. SETUP: Point the client to the local Ollama server ---
load_dotenv()

PROVIDER = os.getenv("PROVIDER", "ollama").lower()  # 'openai' | 'azure' | 'ollama'
MODEL_NAME = os.getenv("VNF_AGENT_MODEL", "phi3")

# OpenAI (public) defaults
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Azure OpenAI
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")

# Ollama
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama")  # dummy key

if PROVIDER == 'openai':
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set for provider=openai")
    client = OpenAI(api_key=OPENAI_API_KEY)
elif PROVIDER == 'azure':
    if not (AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY):
        raise RuntimeError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set for provider=azure")
    client = OpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        base_url=f"{AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments"
    )
elif PROVIDER == 'ollama':
    client = OpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY)
else:
    raise RuntimeError(f"Unsupported PROVIDER={PROVIDER}")

# --- 2. DEFINE THE AGENT'S TOOLS (No changes here) ---
def check_vnf_package_structure(file_name: str):
    """Tool 1: Checks VNF package name and extension (.zip)."""
    print(f"--- TOOL LOG: Running check_vnf_package_structure on '{file_name}'...")
    if not file_name.endswith(".zip"):
        return json.dumps({"is_valid": False, "reason": "Invalid file extension. Expected .zip."})
    if len(file_name.removesuffix(".zip").split('_')) < 3:
        return json.dumps({"is_valid": False, "reason": "Naming convention violation. Expected: vendor_name_version.zip."})
    return json.dumps({"is_valid": True, "reason": "Package structure and naming are valid."})

def check_security_compliance(file_name: str):
    """Tool 2: Simulates a security check for trusted vendors."""
    print(f"--- TOOL LOG: Running check_security_compliance for '{file_name}'...")
    vendor = file_name.split('_')[0]
    trusted_vendors = ["cisco", "juniper", "paloalto"]
    if vendor.lower() in trusted_vendors:
        return json.dumps({"is_compliant": True, "reason": f"Vendor '{vendor}' is trusted."})
    return json.dumps({"is_compliant": False, "reason": f"Vendor '{vendor}' is not trusted."})

def check_resource_requirements(file_name: str):
    """Tool 3: Simulates checking resource limits from the package."""
    print(f"--- TOOL LOG: Running check_resource_requirements for '{file_name}'...")
    if "highcpu" in file_name.lower():
        return json.dumps({"is_within_limits": False, "reason": "VNF requires high CPU (32 cores), exceeding standard limit."})
    return json.dumps({"is_within_limits": True, "reason": "Resource requirements are within standard limits."})

# --- 3. THE AGENT'S CORE LOGIC ---
def _build_tools_schema(available_tools: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return OpenAI-style tool (function) schema with parameters for each tool."""
    schema = []
    for name, fn in available_tools.items():
        schema.append({
            "type": "function",
            "function": {
                "name": name,
                "description": fn.__doc__ or name,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_name": {
                            "type": "string",
                            "description": "Name of the VNF package file to validate (e.g. vendor_product_version.zip)."
                        }
                    },
                    "required": ["file_name"]
                }
            }
        })
    return schema


def _extract_file_name(user_goal: str) -> str | None:
    """Heuristic extraction of a file name from free-form user goal if LLM declines tool usage."""
    match = re.search(r"([\w.-]+\.(zip|rar|tar\.gz))", user_goal, re.IGNORECASE)
    return match.group(1) if match else None


def _safe_json_loads(s: str) -> Dict[str, Any]:
    try:
        return json.loads(s) if s else {}
    except Exception as e:
        print(f"WARN: Failed to parse tool arguments JSON: {e}. Raw: {s}")
        return {}


def _call_llm(messages: List[Dict[str, str]], tools=None, tool_choice="auto"):
    return client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
    )


def run_agent(user_goal: str):
    print(f"\n==================================================")
    print(f"AGENT: Received New Goal: '{user_goal}'")
    print(f"MODEL: {MODEL_NAME}  |  PROVIDER: {PROVIDER}")
    print(f"==================================================")

    available_tools = {
        "check_vnf_package_structure": check_vnf_package_structure,
        "check_security_compliance": check_security_compliance,
        "check_resource_requirements": check_resource_requirements,
    }

    tools_definitions = _build_tools_schema(available_tools)
    system_prompt = (
        "You are a pre-validation agent for Virtual Network Function (VNF) packages. "
        "Decide which tools to invoke based ONLY on the user goal. Always provide tool calls when a file name is present. "
        "If the package is not a .zip still validate structure, security and resource requirements."
    )
    user_prompt = f"User goal: {user_goal}"

    print("\n[1/3] AGENT: Planning (requesting tool selection from LLM)...")
    response = None
    supports_tools = PROVIDER in {"openai", "azure"}  # naive capability flag
    if supports_tools:
        try:
            response = _call_llm([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ], tools=tools_definitions, tool_choice="auto")
        except Exception as e:
            print(f"ERROR: LLM planning call failed (tools). Falling back. Details: {e}")
            response = None
    else:
        print("INFO: Provider lacks tool calling; using heuristic fallback.")

    tool_calls = []
    planning_message = None
    if response:
        planning_message = response.choices[0].message
        # Support both tool_calls (new) and function_call (legacy)
        if hasattr(planning_message, 'tool_calls') and planning_message.tool_calls:
            tool_calls = planning_message.tool_calls
        elif getattr(planning_message, 'function_call', None):
            tool_calls = [planning_message.function_call]

    # If LLM declined tools, perform heuristic fallback
    if not tool_calls:
        print("AGENT: No tool calls returned by model. Using heuristic fallback.")
        file_name = _extract_file_name(user_goal)
        if file_name:
            planned = [
                {"id": "fallback_1", "function": {"name": n, "arguments": json.dumps({"file_name": file_name})}}
                for n in available_tools.keys()
            ]
            tool_calls = planned
        else:
            print("AGENT: No file detected; nothing to validate.")
            return

    print(f"AGENT: Plan created. Will execute {len(tool_calls)} tool(s).")

    print("\n[2/3] AGENT: Executing the plan...")
    tool_outputs = []
    for call in tool_calls:
        # Normalizing structure (Ollama/OpenAI compatibility)
        call_id = getattr(call, 'id', None) or getattr(call, 'name', None) or f"call_{hash(str(call))}"
        # Structured attributes may differ; handle dict fallback
        fn_meta = getattr(call, 'function', None) or getattr(call, 'name', None)
        if isinstance(call, dict):
            fn_meta = call.get('function', call)
        tool_name = getattr(fn_meta, 'name', None) if not isinstance(fn_meta, dict) else fn_meta.get('name')
        if tool_name not in available_tools:
            print(f"WARN: Unknown tool '{tool_name}' returned by model; skipping.")
            continue
        raw_args = getattr(fn_meta, 'arguments', '{}') if not isinstance(fn_meta, dict) else fn_meta.get('arguments', '{}')
        args = _safe_json_loads(raw_args)
        # Align parameter key
        if 'file_name' not in args:
            extracted = _extract_file_name(user_goal)
            if extracted:
                args['file_name'] = extracted
        try:
            output = available_tools[tool_name](**args)
        except TypeError as e:
            print(f"ERROR executing {tool_name}: {e}. Args: {args}")
            output = json.dumps({"error": str(e), "args": args})
        tool_outputs.append({
            "tool_call_id": call_id,
            "role": "tool",
            "name": tool_name,
            "content": output
        })
    print("AGENT: All tool invocations completed.")

    print("\n[3/3] AGENT: Summarizing results...")
    summary_messages = [
        {"role": "system", "content": "Summarize the validation results concisely with pass/fail per check and an overall decision."},
        {"role": "user", "content": user_goal},
    ]
    if planning_message:
        summary_messages.append(planning_message)
    summary_messages.extend(tool_outputs)

    try:
        summary_response = _call_llm(summary_messages)
        
        final_summary = summary_response.choices[0].message.content
    except Exception as e:
        final_summary = f"LLM summary failed: {e}\nRaw tool outputs: {json.dumps(tool_outputs, indent=2)}"

    print(f"\n--- ✅ FINAL REPORT ---\n{final_summary}\n----------------------\n")

if __name__ == "__main__":
    run_agent("Please perform a pre-check on the VNF package named 'cisco_firewall_v2.1.zip'")
    run_agent("I need to validate a new package from a new vendor. The file is 'newvendor_router_highcpu.rar'")