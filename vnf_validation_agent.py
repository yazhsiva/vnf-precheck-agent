import os
import json
from openai import OpenAI
from dotenv import load_dotenv

# --- 1. SETUP: Point the client to the local Ollama server ---
load_dotenv()
client = OpenAI(
    base_url='http://localhost:11434/v1', 
    api_key='ollama', # This can be any non-empty string for Ollama
)

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
def run_agent(user_goal: str):
    """
    Runs the agentic workflow: Plan -> Execute -> Summarize.
    """
    print(f"\n==================================================")
    print(f"AGENT: Received New Goal: '{user_goal}'")
    print(f"==================================================")

    available_tools = {
        "check_vnf_package_structure": check_vnf_package_structure,
        "check_security_compliance": check_security_compliance,
        "check_resource_requirements": check_resource_requirements,
    }
    
    tools_definitions = [{"type": "function", "function": {"name": n, "description": f.__doc__}} for n, f in available_tools.items()]
    prompt = f"Based on the user's goal: '{user_goal}', decide which validation tools to call. Respond with a list of tool calls in JSON."
    
    print("\n[1/3] AGENT: Thinking... Creating an execution plan...")
    # Use the llama3 model
    response = client.chat.completions.create(model="llama3", messages=[{"role": "user", "content": prompt}], tools=tools_definitions, tool_choice="auto")
    tool_calls = response.choices[0].message.tool_calls
    
    if not tool_calls:
        print("AGENT: The LLM decided no tools were needed for this goal.")
        return
    print(f"AGENT: Plan created. Will execute {len(tool_calls)} tool(s).")

    print("\n[2/3] AGENT: Executing the plan...")
    tool_outputs = []
    for call in tool_calls:
        call_id = getattr(call, 'id', f"call_{hash(str(call))}") 
        tool_name = call.function.name
        tool_to_call = available_tools[tool_name]
        tool_args = json.loads(call.function.arguments)
        output = tool_to_call(**tool_args)
        tool_outputs.append({"tool_call_id": call_id, "role": "tool", "name": tool_name, "content": output})
    print("AGENT: All tools executed successfully.")

    print("\n[3/3] AGENT: Thinking... Generating final summary...")
    summary_messages = [{"role": "user", "content": user_goal}, response.choices[0].message] + tool_outputs
    # Use the llama3 model for the summary step
    summary_response = client.chat.completions.create(model="llama3", messages=summary_messages)
    final_summary = summary_response.choices[0].message.content
    print(f"\n--- ✅ FINAL REPORT ---\n{final_summary}\n----------------------\n")

# --- 4. RUN THE AGENT WITH DIFFERENT GOALS ---
if __name__ == "__main__":
    run_agent("Please perform a pre-check on the VNF package named 'cisco_firewall_v2.1.zip'")
    run_agent("I need to validate a new package from a new vendor. The file is 'newvendor_router_highcpu.rar'")