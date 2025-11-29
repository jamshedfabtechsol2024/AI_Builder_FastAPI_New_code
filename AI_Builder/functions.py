"""
Functions module for AI Builder Version 2
Contains all utility functions and core functionality
"""

import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from agents import Runner
from .models import (
    TokenManager, ProjectContext,
    manager_agent, codegen_agent,
    error_files_finder_agent, error_resolver_agent,
    modifier_files_finder_agent, modifier_agent
)
import os
import anthropic

import traceback
# Global instances
token_manager = TokenManager()
project_context = ProjectContext()

# -------------------
# Utility Functions
# -------------------

def extract_text_from_event(event):
    """Extract clean text content from streaming events"""
    try:
        if hasattr(event, "data") and hasattr(event.data, "delta"):
            return event.data.delta
    except Exception:
        pass

    if isinstance(event, dict):
        if "delta" in event and isinstance(event["delta"], str):
            return event["delta"]
        if "text" in event and isinstance(event["text"], str):
            return event["text"]

    if isinstance(event, str):
        return event

    for attr in ("delta", "text", "content"):
        if hasattr(event, attr):
            val = getattr(event, attr)
            if isinstance(val, str):
                return val

    return ""


def extract_text_from_result_object(result_obj):
    """Extract text from result objects"""
    try:
        if hasattr(result_obj, "final_output"):
            return result_obj.final_output
        if hasattr(result_obj, "output"):
            return result_obj.output
        return str(result_obj)
    except Exception:
        return str(result_obj) if result_obj else ""


def is_json_complete(text):
    """Check if JSON is complete and valid"""
    try:
        json.loads(text)
        return True
    except json.JSONDecodeError:
        return False


def extract_partial_json(text):
    """Extract partial JSON and identify what's missing"""
    try:
        # Try to parse as-is
        return json.loads(text), True
    except json.JSONDecodeError:
        # Try to close incomplete JSON
        text = text.strip()
        if text.endswith(','):
            text = text[:-1]
        
        # Count braces and brackets
        open_braces = text.count('{') - text.count('}')
        open_brackets = text.count('[') - text.count(']')
        
        # Try to close them
        for _ in range(open_brackets):
            text += ']'
        for _ in range(open_braces):
            text += '}'
            
        try:
            return json.loads(text), False
        except json.JSONDecodeError:
            return None, False


def clean_ai_output(output):
    """Clean AI output by removing markdown formatting"""
    cleaned = output.strip()
    if cleaned.startswith("json\n"):
        cleaned = output.replace("json\n", "", 1).strip()
    elif cleaned.startswith("```json"):
        cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.replace("```", "").strip()
    return cleaned


def extract_json_from_text(text: str):
    """Best-effort extraction of a JSON object from noisy LLM output.
    Returns: Parsed JSON dict
    Raises: ValueError if JSON cannot be parsed after all attempts
    """
    if not isinstance(text, str):
        text = str(text) if text is not None else ""

    # Remove code fences and known noise tokens
    stripped = text.strip()
    if stripped.startswith("```json"):
        stripped = stripped[len("```json"):]
    elif stripped.startswith("json"):
        stripped = stripped[len("json"):]
    if stripped.startswith("```"):
        stripped = stripped[len("```"):]
    if stripped.endswith("```"):
        stripped = stripped[:-3]

    # Try to find a JSON object span
    start = stripped.find('{')
    end = stripped.rfind('}')
    
    if start != -1 and end != -1 and end > start:
        candidate = stripped[start:end+1]
    else:
        candidate = stripped

    # First attempt: direct parse
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse JSON (first attempt): {e}")
        pass

    # Second attempt: try to complete truncated JSON
    try:
        # If the JSON seems truncated, try to complete it
        if candidate.count('{') > candidate.count('}'):
            # More opening braces than closing - add missing closing braces
            missing_braces = candidate.count('{') - candidate.count('}')
            completed = candidate + '}' * missing_braces
            
            # Also check if strings need to be terminated
            lines = completed.split('\n')
            for i, line in enumerate(lines):
                if '"' in line and line.count('"') % 2 != 0:
                    # Unclosed string, add closing quote
                    lines[i] = line + '"'
            completed = '\n'.join(lines)
            
            return json.loads(completed)
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse JSON (completion attempt): {e}")
        pass

    # Third attempt: clean via clean_ai_output then parse
    try:
        cleaned_text = clean_ai_output(stripped)
        return json.loads(cleaned_text)
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse JSON (cleaned attempt): {e}")
        pass

    # Final attempt: use partial JSON extraction or return safe default
    try:
        partial, ok = extract_partial_json(candidate)
        if partial is not None:
            return partial
    except Exception as e:
        print(f"❌ Partial JSON extraction failed: {e}")

    # ✅ CHANGED: Raise exception instead of returning empty dict
    error_msg = f"All JSON parsing attempts failed. Original text preview: {text[:200]}..."
    print(f"❌ {error_msg}")
    raise ValueError(error_msg)


def count_input_tokens_anthropic(text: str) -> int:
    try:
        api_key = os.getenv("CLAUDE_API_KEY")
        if not api_key:
            return 0
        client = anthropic.Anthropic(api_key=api_key)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text or ""}
                ],
            }
        ]
        result = client.messages.count_tokens(
            model="claude-sonnet-4-20250514",
            system="You are a helpful assistant.",
            messages=messages,
        )
        return getattr(result, "input_tokens", 0) or 0
    except Exception:
        return 0


def get_file_type(file_path):
    """Get file type from extension"""
    extension = file_path.split('.')[-1].lower()
    type_map = {
        'jsx': 'react_component',
        'js': 'javascript',
        'css': 'stylesheet',
        'json': 'configuration',
        'html': 'markup',
        'md': 'markdown'
    }
    return type_map.get(extension, 'unknown')


def create_structure_only(project_data):
    """Create a simplified structure-only version (for cost optimization)."""
    if not isinstance(project_data, dict):
        return project_data

    structure = {
        "run": {
            "dev": "vite",
            "build": "vite build",
            "preview": "vite preview"
        },
        "files": {},
        "framework": project_data.get("framework", ""),
        "project_name": project_data.get("project_name", "")
    }

    if "files" in project_data and isinstance(project_data["files"], dict):
        for file_path in project_data["files"].keys():
            structure["files"][file_path] = ""

    return structure



# -------------------
# COST-OPTIMIZED Error Resolution Function
# -------------------

async def handle_error_resolution(user_input, code):
    """Cost-optimized error resolution - send only structure to AI agents"""
    print("\n🐛 STARTING ERROR RESOLUTION WORKFLOW (COST-OPTIMIZED)")
    print("=" * 50)
    
    # Step 1: Parse and separate structure from content
    try:
        if isinstance(code, str):
            full_project = json.loads(code)
            print("✅ Successfully parsed JSON string")
        elif isinstance(code, dict):
            full_project = code
            print("✅ Using provided dictionary")
        else:
            full_project = {}
            print("⚠️  Unknown code format, using empty structure")
        
        # Store full files content in project context (for later retrieval)
        if "files" in full_project and isinstance(full_project["files"], dict):
            project_context.set_original_files(full_project["files"])
            print("✅ Stored full file contents in project context")
        
        # Create structure-only version for AI agents (COST OPTIMIZATION)
        project_structure = create_structure_only(full_project)
        print(project_structure)
        error_description = user_input
            
    except (json.JSONDecodeError, TypeError) as e:
        print(f"❌ JSON parsing error: {e}")
        # Create fallback
        full_project = {
            "project_name": "error-project",
            "framework": "React",
            "files": {}
        }
        project_structure = full_project

    print(f"📋 Error Description: {error_description}")
    print(f"📁 Full Project Files: {len(full_project.get('files', {}))}")
    print(f"💰 Structure-only sent to AI (cost optimization)")

    # Step 2: Find affected files (send only structure to save tokens)
    print("\n🔍 STEP 1: Identifying affected files...")
    
    finder_input = {
        "error_description": error_description,
        "project_structure": project_structure  # Only structure, not full content!
    }
    
    finder_result = await run_agent_with_token_limit(
        error_files_finder_agent, 
        json.dumps(finder_input)
    )
    
    try:
        print(finder_result.final_output)
        # Parse finder result
        finder_output_text = clean_ai_output(finder_result.final_output)
        finder_output = json.loads(finder_output_text)
        affected_files = finder_output.get("affected_files", [])
        error_type = finder_output.get("error_type", "unknown")
        analysis = finder_output.get("analysis", "No analysis provided")
        
        print(f"📄 Affected Files: {affected_files}")
        print(f"🏷️  Error Type: {error_type}")
        print(f"📝 Analysis: {analysis}")
        
        # Store error files in project context
        # project_context.set_error_files(affected_files)
        
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing finder result: {e}")
        print(f"Raw finder output: {finder_result.final_output}")
        affected_files = ["src/components/MessageBubble.jsx"]
        error_type = "runtime_error"
    
    # Step 3: Get ONLY affected file contents from project context
    print("\n📄 STEP 2: Extracting affected file contents...")

    affected_files_content = {}
    for file_name in affected_files:
        if file_name in full_project["files"]:
            affected_files_content[file_name] = full_project["files"][file_name]
            print(f"📄 Extracted: {file_name}")
        else:
            print(f"⚠️ File not found: {file_name}")
    

    # Step 4: Send only affected files to resolver (COST OPTIMIZATION)
    print("\n🔧 STEP 3: Resolving errors...")
    
    resolver_input = {
        "error_description": error_description,
        "file_name": affected_files,
        "affected_files": affected_files_content,  # Only affected files, not all files!
        "error_type": error_type,
    }
    
    resolver_result = await run_agent_with_token_limit(
        error_resolver_agent,
        json.dumps(resolver_input)
    )
    
    try:
        resolver_output_text = clean_ai_output(resolver_result.final_output)
        # Parse the cleaned JSON string to dictionary
        resolver_output = json.loads(resolver_output_text)
        fixed_files = resolver_output  # Now it's a dict
        
        print(f"✅ Fixed Files: {list(fixed_files.keys())}")
        
        for file_path, fixed_content in fixed_files.items():
            full_project["files"][file_path] = fixed_content
            print(f"📝 Updated: {file_path}")

        
        return full_project
        
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing resolver result: {e}")
        print(f"Raw resolver output: {resolver_result.final_output}")
        return resolver_result.final_output


async def stream_codegen_async(agent, user_input):
    print("🚀 Generating code (streaming async)...")
    print("-" * 60)
    try:
        stream_result = Runner.run_streamed(agent, input=user_input)
        full_output = ""

        if hasattr(stream_result, "stream_events"):
            async for event in stream_result.stream_events():
                text_piece = extract_text_from_event(event)
                if text_piece:
                    full_output += text_piece

        print("\n" + "-" * 60)
        print("✅ Code generation completed!")
        return full_output

    except Exception as e:
        print(f"\n❌ Streaming error: {type(e).__name__}: {str(e)}")
        # Re-raise the exception so it can be caught by the calling function
        raise e

# dummy json for testing purposes
# async def stream_codegen_chunks(agent, user_input):
#     """Async generator yielding incremental codegen chunks (raw text from the model)."""
#     print("🚀 Generating code (chunk streaming)...")
#     print("-" * 60)

#     # Always use dummy during tests
#     sample = dummy_json()
#     project_json = sample.get("ai_json", sample)  # prefer full project JSON if present
#     json_text = json.dumps(project_json)

#     chunk_size = 2048  # or 1024
#     for i in range(0, len(json_text), chunk_size):
#         part = json_text[i:i+chunk_size]
#         print(part, end="", flush=True)
#         yield part
#     return


async def stream_codegen_chunks(agent, user_input):
    """Async generator yielding incremental codegen chunks (raw text from the model).

    The caller is responsible for assembling the chunks and parsing JSON at the end.
    """
    print("🚀 Generating code (chunk streaming)...")
    print("-" * 60)
    stream_result = Runner.run_streamed(agent, input=user_input)
    # Case 1: async stream events (agent live streaming)
    if hasattr(stream_result, "stream_events"):
        async for event in stream_result.stream_events():
            text_piece = extract_text_from_event(event)
            print(text_piece)
            if text_piece:
                yield text_piece
        return
    
async def  run_agent_with_token_limit(agent, input_data):
    print(f"🚀 Running {agent.name} agent...")
    print("-" * 60)
    print("-----------------------324234234324------------------")
    try:
        stream_result = Runner.run_streamed(agent, input=input_data)
        full_output = ""
        if hasattr(stream_result, "stream_events"):
            async for event in stream_result.stream_events():
                text_piece = extract_text_from_event(event)
                if text_piece:
                    # Only remove markdown code fences, not the word "json" from actual content
                    if text_piece.strip() in ["```json", "```", "json"]:
                        continue  # Skip these markers entirely
                    print(text_piece)
                    full_output += text_piece

        print("\n" + "-" * 60)

        class ResultWrapper:
            def __init__(self, raw, text):
                self.raw = raw
                self.final_output = text
        
        result = ResultWrapper(stream_result, full_output)
        
        return result
    except Exception as e:
        print(f"\n❌ Streaming error: {type(e).__name__}: {str(e)}")
        # Re-raise the exception so it can be caught by the calling function
        raise e

# async def run_agent_with_token_limit(agent, input_data):
#     """Run agent with token management"""
#     print(f"🚀 Running {agent.name} agent...")
#     print("-" * 60)
    
#     try:
#         stream_result = Runner.run_streamed(agent, input=input_data)
#         full_output = ""
#         usage_data = None

#         if hasattr(stream_result, "stream_events"):
#             async for event in stream_result.stream_events():
#                 text_piece = extract_text_from_event(event)
#                 if text_piece:
#                     print(text_piece, end="", flush=True)
#                     full_output += text_piece
                
#                 # Capture usage data from different event types
#                 if hasattr(event, 'usage'):
#                     usage_data = event.usage
#                 elif hasattr(event, 'data') and hasattr(event.data, 'usage'):
#                     usage_data = event.data.usage
#                 elif hasattr(event, 'data') and hasattr(event.data, 'response') and hasattr(event.data.response, 'usage'):
#                     usage_data = event.data.response.usage
#                 elif hasattr(event, 'event') and hasattr(event.event, 'usage'):
#                     usage_data = event.event.usage
                
#                 # Try to get usage from the event's content or data
#                 if not usage_data:
#                     if hasattr(event, 'content') and hasattr(event.content, 'usage'):
#                         usage_data = event.content.usage
#                     elif hasattr(event, 'delta') and hasattr(event.delta, 'usage'):
#                         usage_data = event.delta.usage
                
#                 # Debug: print ALL event information
#                 print(f"\n🔍 Event: {event}")
#                 print(f"🔍 Event type: {type(event)}")
#                 if hasattr(event, '__dict__'):
#                     print(f"🔍 Event attributes: {list(event.__dict__.keys())}")
#                     for attr in event.__dict__:
#                         print(f"🔍 {attr}: {getattr(event, attr)}")

#         print("\n" + "-" * 60)
        
#         # Check for usage data in the final result if not found in events
#         if not usage_data and hasattr(stream_result, 'usage'):
#             usage_data = stream_result.usage
#         elif not usage_data and hasattr(stream_result, 'data') and hasattr(stream_result.data, 'usage'):
#             usage_data = stream_result.data.usage
        
#         # Use actual tokens from model
#         if usage_data and usage_data.total_tokens is not None:
#             total_tokens = usage_data.total_tokens
#             print(f"📊 Actual tokens used: {total_tokens}")
#             token_manager.add_tokens(total_tokens)
#         else:
#             print("⚠️ No usage data available from model - estimating tokens")
#             # Fallback: estimate tokens using token manager
#             estimated_tokens = token_manager.count_tokens(input_data) + token_manager.count_tokens(full_output)
#             print(f"📊 Estimated tokens: {estimated_tokens}")
#             token_manager.add_tokens(estimated_tokens)

#         class ResultWrapper:
#             def __init__(self, raw, text):
#                 self.raw = raw
#                 self.final_output = text
        
#         result = ResultWrapper(stream_result, full_output)
        
#         return result
#     except Exception as e:
#         print(f"\n❌ Streaming error: {type(e).__name__}: {str(e)}")
#         # Re-raise the exception so it can be caught by the calling function
#         raise e

async def code_update(user_input, full_project):
    """Code update with AI content tracking for token counting"""
    
    print(f"🔍 Starting code update for: {user_input[:100]}...")
    
    try:
        change_input = json.dumps({"project": full_project, "query": user_input})
        change_result = await run_agent_with_token_limit(modifier_files_finder_agent, change_input)
        
        raw_text = change_result.final_output if hasattr(change_result, 'final_output') else str(change_result)
        try:
            files_info = extract_json_from_text(raw_text)
        except json.JSONDecodeError as e:
            print(f"❌ Failed to parse JSON from modifier output: {e}")
            yield {'type': 'error', 'chunk': f"❌ Failed to parse files info: {e}\n"}, None, ""
            return

        files_to_modify = files_info.get("files_to_modify", [])
        new_files_to_create = files_info.get("new_files_to_create", [])
        related_files_to_update = files_info.get("related_files_to_update", [])
        target_files = {}
        
        for fname in files_to_modify:
            if fname in full_project["files"]:
                target_files[fname] = full_project["files"][fname]
            else:
                yield {'type': 'warning', 'chunk': f"⚠️ File not found in project: {fname}\n"}, None, ""
        
        for fname in new_files_to_create:
            target_files[fname] = ""  
        
        for fname in related_files_to_update:
            if fname in full_project["files"]:
                target_files[fname] = full_project["files"][fname]
            else:
                yield {'type': 'warning', 'chunk': f"⚠️ Related file not found in project: {fname}\n"}, None, ""

        modifier_input = json.dumps({
            "files": target_files,
            "query": user_input,
            "summary": files_info.get("summary", "")
        })

        print("🔄 Calling modifier agent...")
        modifier_result = await run_agent_with_token_limit(modifier_agent, modifier_input)
        updated_files_output_json = modifier_result.final_output  # ✅ AI-generated content for token counting

        print('updated_files_output_json    =====   ',type(updated_files_output_json))

        print("3333333333333333333333333333333333333333333333333333333333333333333333333333333333333333333")
        try:
            print('**** Modifier output:', updated_files_output_json[:200] + "..." if len(updated_files_output_json) > 200 else updated_files_output_json)
            print("===============================================================================================================")
            
            try:
                updated_files = extract_json_from_text(updated_files_output_json)
                print("akash",updated_files)
            except:
                updated_files = updated_files_output_json

                print('*************************************************************')
                print(type(updated_files))
                print('*************************************************************')
        
        except Exception as e:
            print(f"❌ Failed to parse JSON from modifier output: {e}")
            yield {'type': 'error', 'chunk': f"❌ Failed to parse modifier output: {e}\n"}, None, ""
            return
            
        print("444444444444444444444444444444444444444444444444444444444444444444444444444444444444444444444444444")
        
        for file_path, fixed_content in updated_files.items():
            full_project["files"][file_path] = fixed_content
        
        print(f"🔍 code_update returning full_project type: {type(full_project)}")

        # ✅ SINGLE FINAL YIELD with all three values
        yield {'type': 'json_chunk', 'chunk': full_project}, full_project, updated_files_output_json
        
    except Exception as e:
        print(f"❌ Error in code_update: {type(e)} - {e}")
        traceback.print_exc()
        yield {'type': 'error', 'chunk': f"❌ Error in code update: {str(e)}\n"}, None, ""
    



async def run_agent_with_token_limit_streaming(agent, input_data, estimated_response_tokens=1000):
    """Run agent with proper streaming token management"""
    # input_tokens = token_manager.count_tokens(input_data)
    # token_manager.check_and_wait(estimated_response_tokens)

    print(f"🚀 Running {agent.name} agent...")
    print("-" * 60)
    print("🚀 Generating code (streaming with token management)...")
    
    try:
        
        stream_result = Runner.run_streamed(agent, input=input_data) 
        # accumulated_tokens = 0
        # token_check_interval = 50  # Check tokens every ~50 tokens
        # total_tokens = stream_result.raw_responses[0].usage.total_tokens
        # print("-------------------✅ Agent Total Tokens Used: ", total_tokens, "-------------------")
        if hasattr(stream_result, "stream_events"):
            async for event in stream_result.stream_events():
                text_piece = extract_text_from_event(event)
                if text_piece:
                    print(text_piece, end="", flush=True)
                    
                    # Yield the actual chunk for real streaming
                    yield text_piece
                    
                    # # Track tokens for management
                    # accumulated_tokens += token_manager.count_tokens(text_piece)
                    
                    # # Periodically check token usage
                    # if accumulated_tokens >= token_check_interval:
                    #     token_manager.add_tokens(accumulated_tokens)
                    #     accumulated_tokens = 0
                    #     # Re-check if we need to wait
                    #     token_manager.check_and_wait(estimated_response_tokens - accumulated_tokens)

        print("\n" + "-" * 60)
        
        # # Add any remaining tokens
        # if accumulated_tokens > 0:
        #     token_manager.add_tokens(accumulated_tokens)

    except Exception as e:
        print(f"\n❌ Streaming error: {type(e).__name__}: {str(e)}")
        raise e


async def handle_error_resolution_streaming(user_input, code, conversation_id):
    """Streaming error resolution workflow - returns COMPLETE project JSON"""
    
    # Step 1: Parse project structure
    try:
        if isinstance(code, str):
            full_project = json.loads(code)
        elif isinstance(code, dict):
            full_project = code
        else:
            full_project = {}
        
        project_structure = create_structure_only(full_project)
        error_description = user_input

    except (json.JSONDecodeError, TypeError) as e:
        yield {'type': 'error', 'chunk': f"❌ Error parsing project: {e}\n"}, None, ""
        return
    
    finder_input = {
        "error_description": error_description,
        "project_structure": project_structure
    }
    
    try:
        finder_result = await run_agent_with_token_limit(
            error_files_finder_agent,
            json.dumps(finder_input)
        )
    except Exception as e:
        yield {'type': 'error', 'chunk': f"❌ Error in finder agent: {e}\n"}, None, ""
        return
    
    try:
        finder_output_text = clean_ai_output(finder_result.final_output)
        finder_output = json.loads(finder_output_text)
        # affected_files = finder_output.get("affected_files", [])
        # error_type = finder_output.get("error_type", "unknown")
        # analysis = finder_output.get("analysis", "No analysis provided")
        # Support both old and new format
        primary_error_file = finder_output.get("primary_error_file", "")
        affected_files = finder_output.get("affected_files", [])
        fix_priority = finder_output.get("fix_priority", affected_files)
        dependency_chain = finder_output.get("dependency_chain", {})
        error_type = finder_output.get("error_type", "unknown")
        root_cause_analysis = finder_output.get("root_cause_analysis", finder_output.get("analysis", "No analysis"))

        # Ensure primary file is in affected files
        if primary_error_file and primary_error_file not in affected_files:
            affected_files.insert(0, primary_error_file)

        # Add config files from dependency chain
        config_files = dependency_chain.get("config_files", [])
        for config_file in config_files:
            if config_file not in affected_files:
                affected_files.append(config_file)

        # Add upstream dependencies (imported_by)
        imported_by = dependency_chain.get("imported_by", [])
        for upstream_file in imported_by:
            if upstream_file not in affected_files:
                affected_files.append(upstream_file)

    except json.JSONDecodeError as e:
        yield {'type': 'error', 'chunk': f"❌ Error parsing finder result: {e}\n"}, None, ""
        print("exectips = ",e )
        print(f"Raw finder output: {finder_result.final_output}")
        affected_files = ["src/main.jsx"]  # fallback
    
    affected_files_content = {}
    for file_name in affected_files:
        if file_name in full_project["files"]:
            affected_files_content[file_name] = full_project["files"][file_name]
        else:
            yield {'type': 'warning', 'chunk': f"⚠️ File not found: {file_name}\n"}, None, ""
    
    # resolver_input = {
    #     "error_description": error_description,
    #     "file_names": affected_files,
    #     "affected_files": affected_files_content,
    #     "error_type": error_type,
    # }
    resolver_input = {
        "error_description": error_description,
        "error_type": error_type,
        "root_cause_analysis": root_cause_analysis,
        "primary_error_file": primary_error_file,
        "fix_priority": fix_priority,
        "affected_files": affected_files_content,
    }
        
    try:
        resolver_result = await run_agent_with_token_limit(
            error_resolver_agent,
            json.dumps(resolver_input)
        )
    except Exception as e:
        yield {'type': 'error', 'chunk': f"❌ Resolver agent failed: {e}\n"}, None, ""
        return
    
    try:
        try:
            resolver_output_text_cleaned = clean_ai_output(resolver_result.final_output)
        except Exception as e:
            resolver_output_text_cleaned = resolver_result.final_output.replace("```json", "").replace("```", "").strip()
            yield {'type': 'error', 'chunk': f"❌ Error cleaning resolver output: {e}\n"}, None, ""   
        
        
        fixed_files = json.loads(resolver_output_text_cleaned)
        with open("AI_Builder/output.json", "w", encoding="utf-8") as f:
            json.dump(fixed_files, f, indent=4, ensure_ascii=False)

        print("JSON file saved successfully!")
        fixed_count = 0
        for file_path, fixed_content in fixed_files.items():
            if file_path in full_project["files"]:
                full_project["files"][file_path] = fixed_content
                fixed_count += 1
            else:
                yield {'type': 'warning', 'chunk': f"⚠️ File path not in project: {file_path}\n"}, full_project, resolver_output_text_cleaned

        # ✅ SINGLE FINAL YIELD - This is the key fix
        yield {'type': 'json_chunk', 'chunk': full_project}, full_project, resolver_output_text_cleaned
        
    except (json.JSONDecodeError, ValueError) as e:
        yield {'type': 'error', 'chunk': f"❌ Error parsing resolver result: {e}\n"}, None, ""
        
        yield {'type': 'message', 'chunk': "🛠️ Applying manual fix for the error...\n"}, None, ""
        
        if "src/main.jsx" in full_project["files"]:
            original_content = full_project["files"]["src/main.jsx"]
            fixed_content = original_content.replace(
                "<BrowserRouter>\n      <App \n    </BrowserRouter>",
                "<BrowserRouter>\n      <App />\n    </BrowserRouter>"
            )
            full_project["files"]["src/main.jsx"] = fixed_content
            yield {'type': 'message', 'chunk': "📝 Manually fixed: src/main.jsx\n"}, None, ""
        

        yield {'type': 'message', 'chunk': "Error resolution completed with manual fixes! 🚀\n"}, full_project, ""