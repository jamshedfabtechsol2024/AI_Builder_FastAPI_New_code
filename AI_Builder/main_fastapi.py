"""
FastAPI Main module for AI Builder Version 2
Contains FastAPI endpoints for different AI agent tasks
"""

import os
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv, find_dotenv
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import base64
from typing import Optional
import urllib.request
import urllib.error

# Import from our modules
from .models import (
    TokenManager, ProjectContext,
    manager_agent, planner_agent, codegen_agent,
    error_files_finder_agent, error_resolver_agent,
    modifier_files_finder_agent, modifier_agent, code_conversation_agent, project_summary_agent, name_suggestion_agent, updating_and_error_summary_agent
)
from .functions import (
    handle_error_resolution,
    clean_ai_output, extract_text_from_result_object,
    run_agent_with_token_limit, code_update,
    stream_codegen_chunks, run_agent_with_token_limit_streaming, extract_json_from_text, handle_error_resolution_streaming, count_input_tokens_anthropic, 
)
from .simple_database import (
    get_user, get_or_create_conversation, add_conversation_version,
    get_current_json, undo_json, redo_json, get_messages_history,
    update_current_json, update_current_json_with_history, get_undo_redo_status,
    add_ai_message, validate_conversation_id, create_new_conversation,
    get_conversation_full, list_conversations_basic, create_new_project_with_conversation, is_first_message_in_conversation, update_project_name,get_project_publish_info,
    get_conversation_messages, verify_workspace_access, list_conversations_without_workspace, get_user_subscription, reserve_user_tokens
)
from .credit_calculator import credits_for_messages, count_tokens as count_tokens_anthropic_exact
from .prompts import codegen_prompt, error_resolving_prompt, code_modifier_prompt


_ = load_dotenv(find_dotenv())
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")


if not CLAUDE_API_KEY:
    raise ValueError("CLAUDE_API_KEY environment variable is required")

app = FastAPI(
    title="AI Builder Version 2",
    description="AI-powered code generation and project management API",
    version="2.0.0"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-frontend-domain.com", "http://localhost:3000", "https://ai-web-builder-fe.vercel.app", "https://staron.ai"],  # Add your frontend domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




class UserRequest(BaseModel):
    user_input: str
    project_context: dict = None
    user_id: int = None  

class ManagerRequest(BaseModel):
    user_input: str

class ManagerResponse(BaseModel):
    task: str
    confidence: float = 1.0

class AIResponse(BaseModel):
    ai_message: str
    current_json: dict
    conversation_id: str
    task_type: str

class CodeGenerationResponse(BaseModel):
    ai_message: str
    current_json: dict
    conversation_id: str

class UndoRedoResponse(BaseModel):
    current_json: dict
    version_index: int

# -------------------
# Global instances
# -------------------
# token_manager = TokenManager()
# project_context = ProjectContext()


async def get_manager_decision(user_input: str, conversation_id: str) -> str:
    """Get manager agent decision for task routing"""
    try:
        # name_suggest = await run_agent_with_token_limit(name_suggestion_agent, user_input)
        # print(f"📝 Suggested project name: {name_suggest.final_output.strip()}")
        # print()
    
        chat_history = get_conversation_messages(conversation_id)

        if chat_history is None:
            conversation_input = [{"role": "user", "content": user_input}]
        else:
            conversation_input = chat_history + [
                {"role": "user", "content": user_input}
            ]
    
        manager_result = await run_agent_with_token_limit(manager_agent, conversation_input)
        task_type = manager_result.final_output.strip()
        print(f"============📝 Manager decision: {task_type}==================")

        try:
            manager_decision = json.loads(task_type)
            task = manager_decision.get("task", "")
        except:
            if "code_generation" in task_type:
                task = "code_generation"
            elif "error_resolution" in task_type:
                task = "error_resolution"
            elif "code_change" in task_type:
                task = "code_change"
            elif "code_continuation" in task_type:
                task = "code_continuation"
            elif "code_conversation" in task_type:
                task = "code_conversation"
            else:
                task = "code_conversation"
        
        return task
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Manager decision failed: {str(e)}")


async def generate_project_name(user_input: str) -> str:
    name_suggest = await run_agent_with_token_limit(name_suggestion_agent, user_input)
    print(f"📝 Suggested project name: {name_suggest.final_output.strip()}")
    
    ai_response = name_suggest.final_output.strip()
    
    clean_name = ai_response.replace('```json', '').replace('```', '').replace('json','').strip()
    
    if clean_name.startswith('{') and '"project_name"' in clean_name:
        try:
            parsed = json.loads(clean_name)
            return parsed.get("project_name", "New Project")
        except:
            pass
    
    return clean_name if clean_name else "New Project"

def extract_user_id_from_token(token: str) -> int:
    try:
        if token and '.' in token:
            parts = token.split('.')
            if len(parts) >= 2:
                payload_b64 = parts[1]
                padding = '=' * (-len(payload_b64) % 4)
                payload_json = base64.urlsafe_b64decode(payload_b64 + padding).decode()
                payload = json.loads(payload_json)
                candidate = payload.get('user_id') or payload.get('sub') or payload.get('uid')
                if candidate is not None:
                    return int(candidate)
    except Exception:
        pass
    try:
        padding = '=' * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(token + padding).decode()
        data = json.loads(decoded)
        candidate = data.get('user_id') or data.get('uid') or data.get('sub')
        if candidate is not None:
            return int(candidate)
    except Exception:
        pass
    # Try plain integer token
    try:
        return int(token)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid token: cannot extract user_id")

def send_low_credit_notification(user_id: int, daily_remaining: int, total_remaining: int):
    try:
        url = os.getenv("NOTIFICATIONS_API_URL", "https://api.staron.ai/api/v1/notifications/credit-balance-update/")
        payload = {
            "user_id": user_id,
            "daily_tokens_available": daily_remaining,
            "total_tokens_remaining": total_remaining,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        token = os.getenv("NOTIFICATIONS_API_TOKEN")
        # if token:
        #     headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            _ = resp.read()
    except Exception:
        pass

async def notify_low_credit_async(user_id: int, daily_remaining: int, total_remaining: int):
    await asyncio.to_thread(send_low_credit_notification, user_id, daily_remaining, total_remaining)



@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "message": "AI Builder Version 2 API",
        "version": "2.0.0",
        "endpoints": {
            "manager": "/manager - Route user requests to appropriate agents",
            "code_generation": "/code_generation - Generate new projects with planning",
            "error_resolution": "/error_resolution - Fix bugs and errors",
            "code_change": "/code_change - Modify existing code",
            "code_continuation": "/code_continuation - Complete partial code"
        }
    }

class CreateProjectRequest(BaseModel):
    workspace_id: Optional[str] = None



@app.post("/api/v1/create_project")
async def create_project(
    request: CreateProjectRequest,
    Authorization: Optional[str] = Header(None, description="Bearer access token")
):
    token = None
    if Authorization:
        if Authorization.lower().startswith("bearer "):
            token = Authorization.split(" ", 1)[1].strip()
        else:
            token = Authorization.strip()
    
    if not token:
        raise HTTPException(status_code=401, detail="Missing Authorization token")
    
    user_id = extract_user_id_from_token(token)
    print("Extracted user_id:", user_id)
    user = get_user(user_id)
    if not user:
        raise HTTPException(
            status_code=404, 
            detail="First create an account then use AI model"
        )
    
    workspace = None
    if request.workspace_id:  # Access from request object
        print('request workspace id',request.workspace_id)
        workspace = verify_workspace_access(request.workspace_id, user_id)
        if not workspace:
            raise HTTPException(status_code=404, detail="Workspace not found or access denied")
    
    result = create_new_project_with_conversation(user_id, workspace_id=request.workspace_id)

    if not result:
        raise HTTPException(
            status_code=500, 
            detail="Failed to create project"
        )

    return {
        "message": "Project created successfully", 
        "conversation_id": result["conversation_id"],
        "project_id": result["project_id"]
    }


@app.post("/api/v1/ai_chat/{conversation_id}")
async def manager_endpoint(request: ManagerRequest, conversation_id: str):
    """Manager Agent - Routes user requests to appropriate tasks and returns streaming AI response"""

    is_valid = validate_conversation_id(conversation_id)

    if not is_valid:
        raise HTTPException(
            status_code=404, 
            detail="Conversation not found"
        )

    try:
        meta = get_conversation_full(conversation_id)
        if not meta or not isinstance(meta, dict) or not meta.get("user_id"):
            raise HTTPException(status_code=404, detail="Conversation not found")
        user_id_for_tokens = meta.get("user_id")
        sub = get_user_subscription(user_id_for_tokens)
        if not sub or sub.get("status") not in ("active", "trialing"):
            raise HTTPException(status_code=402, detail="No active subscription")
        try:
            daily_left = sub.get("daily_tokens_available", 0)
            total_left = sub.get("total_tokens_remaining", 0)
            if daily_left == 0:
                raise HTTPException(status_code=402, detail="You have consumed your daily tokens limit")
            if isinstance(daily_left, int) and daily_left <= 10:
                asyncio.create_task(notify_low_credit_async(user_id_for_tokens, int(daily_left), int(total_left)))
        except Exception as e:
            raise 

        existing_json = get_current_json(conversation_id) or {}
        has_initial_json = bool(existing_json) and isinstance(existing_json, dict) and len(existing_json.keys()) > 0

        if is_first_message_in_conversation(conversation_id):
            project_name = await generate_project_name(request.user_input)
            update_project_name(conversation_id, project_name)
        else:
            project_name = None

        try:
            task_type = await get_manager_decision(request.user_input, conversation_id)
            print(f"🧠 Manager decided task type: {task_type}")
        except Exception as e:
            print(f"⚠️ Manager decision failed, using default task type: {e}")
            task_type = "code_generation"

        if not has_initial_json:
            if task_type == "code_conversation":
                return await code_conversation_function(request, conversation_id, project_name)

            return await streaming_code_generation(request, conversation_id, project_name)


        if task_type == "error_resolution":
            return await error_resolution_function(request, conversation_id, existing_json)
        if task_type == "code_change":
            return await code_change_function(request, conversation_id, existing_json)
        if task_type == "code_conversation":
            return await code_conversation_function(request, conversation_id)
        if task_type == "code_generation":
            return await code_change_function(request, conversation_id, existing_json)


        return await streaming_code_generation(request, conversation_id)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def streaming_code_generation(request: ManagerRequest, conversation_id: str, project_name: str = None):
    """Streaming code generation with database storage"""
    try:
        # Fetch user id for post-hoc billing
        meta_for_billing = get_conversation_full(conversation_id)
        user_id_for_tokens = meta_for_billing.get("user_id") if isinstance(meta_for_billing, dict) else None
       
        try:
            
            chat_history = get_conversation_messages(conversation_id)

            if chat_history is None:
                conversation_input = [{"role": "user", "content": request.user_input}]
            else:
                conversation_input = chat_history + [
                    {"role": "user", "content": request.user_input}
                ]

            plan_output = await run_agent_with_token_limit(planner_agent, conversation_input)
            if hasattr(plan_output, 'final_output'):
                plan_data = clean_ai_output(plan_output.final_output)
            else:
                plan_data = f"Project plan for: {request.user_input}"
        except Exception as e:
            print(f"⚠️ Planner agent error: {e}")
            plan_data = f"Project plan for: {request.user_input}"

        data = f"Detect the language of the User Input: {request.user_input} \n\n Convert this Project Plan to detected language: \n\n{plan_data}"

        codegen_input = f"Based on this project plan, only generate the React project:\n\n{plan_data}\n\nUser Request: {request.user_input} \n\n Only generate the spefic features requested by the user. Not include any extra features."
        
        # Variables to collect streaming output
        ai_message = ""
        ai_json = {}
        full_json_output = ""
        
        async def generate():
            nonlocal ai_message, ai_json, full_json_output
            
            try:
                async for chunk in run_agent_with_token_limit_streaming(project_summary_agent, data, 1000):
                    if chunk.strip():
                        chunk = chunk.replace("```html","").replace("```", "").replace("html", "")
                        formatted_chunk = chunk #+ '\n' if not chunk.endswith('\n') else chunk
                        yield f"data: {json.dumps({'type': 'message', 'chunk': formatted_chunk})}\n\n"
                        await asyncio.sleep(0.05)
                        ai_message += chunk  

                import re
                async for piece in stream_codegen_chunks(codegen_agent, codegen_input):
                    clean_piece = piece.replace("```json", "").replace("```", "").replace("json", "")
                    if clean_piece.strip():
                        yield f"data: {json.dumps({'type': 'json_chunk', 'chunk': clean_piece})}\n\n"
                        await asyncio.sleep(0.005)
                        full_json_output += clean_piece  
                
                try:
                    ai_json = json.loads(full_json_output)
                except json.JSONDecodeError:
                    try:
                        # print("new code generation  new code generation  new code generation new code generation ")
                        ai_json = full_json_output
                        # print("ai_json", ai_json)
                    except Exception as e:
                        print(f"❌ Failed to extract JSON: {e}")
                        ai_json = {
                            "project_name": "Generated Project",
                            "framework": "React",
                            "files": {},
                            "generated_from": request.user_input
                        }
                        
            except Exception as e:
                print(f"❌ Error in generate(): {e}")
                raise HTTPException(status_code=500, detail=str(e))
            # ✅ Fix key name "package." → "package.json"
            if isinstance(ai_json, dict):
                if "files" in ai_json and isinstance(ai_json["files"], dict):
                    if "package." in ai_json["files"]:
                        ai_json["files"]["package.json"] = ai_json["files"].pop("package.")
            try:
                # Save JSON state to conversation table
                add_conversation_version(
                    conversation_id,
                    ai_json
                )
                # Save detailed message to AIMessage table
                add_ai_message(
                    conversation_id,
                    request.user_input,
                    ai_message,
                    ai_json,
                    'code_generation'
                )
                print(f"✅ Saved conversation: {request.user_input[:50]}...")
            except Exception as e:
                print(f"❌ Error saving to database: {e}")

            # Compute exact tokens and credits using Anthropic count_tokens and deduct from user's daily tokens
            try:
                if user_id_for_tokens:
                    assistant_text = json.dumps(ai_json, indent=2) if isinstance(ai_json, (dict, list)) else str(ai_json)
                    messages_json = [
                        {"role": "user", "content": [{"type": "text", "text": codegen_input}]},
                        {"role": "assistant", "content": [{"type": "text", "text": assistant_text}]},
                    ]

                    credit  = await asyncio.to_thread(
                        credits_for_messages,
                        model="claude-sonnet-4-20250514",
                        system=codegen_prompt,
                        messages=messages_json,
                    )
                    _ = reserve_user_tokens(int(user_id_for_tokens), int(credit))
            except Exception as bill_err:
                print(f"⚠️ Failed to deduct tokens for code generation: {bill_err}")

            yield f"data: {json.dumps({'done': True, 'type': 'complete','conversation_id': conversation_id, 'project_name':project_name})}\n\n"
        

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def error_resolution_function(request: ManagerRequest, conversation_id: str, current_json: dict):
    """Error Resolution - Fixes bugs and errors in existing code with streaming"""
    ai_message = ""
    ai_generated_content = ""  # ✅ NEW: Store AI-generated content for token counting
    
    async def generate():
        nonlocal ai_message, ai_generated_content

        try:
            if not current_json:
                yield f"data: {json.dumps({'type': 'error', 'chunk': 'project_context is required for error resolution'})}\n\n"
                return
            
            # Step 1: Get analysis from summary agent
            async for chunk in run_agent_with_token_limit_streaming(updating_and_error_summary_agent, request.user_input):
                if chunk.strip():
                    chunk = chunk.replace("```html","").replace("```", "").replace("html", "")
                    formatted_chunk = chunk
                    yield f"data: {json.dumps({'type': 'message', 'chunk': formatted_chunk})}\n\n"
                    await asyncio.sleep(0.05)
                    ai_message += chunk  
            
            final_project_json = None
            ai_resolver_content = "" 
            
            async for chunk, final_json, ai_content in handle_error_resolution_streaming(request.user_input, current_json, conversation_id):
                if chunk:
                    yield f"data: {json.dumps(chunk)}\n\n"
                    await asyncio.sleep(0.005)
                
                if final_json is not None:
                    final_project_json = final_json
                    print(f"✅ Final project received: {type(final_project_json)}")
                
                if ai_content:  # ✅ Store AI-generated content
                    ai_resolver_content = ai_content
                    print(f"✅ AI content received: {len(ai_content)} characters")
            
            # Step 3: Final processing after streaming completes
            if final_project_json:
                print(f"🔄 Updating conversation history...")
                success = update_current_json_with_history(
                    conversation_id, 
                    final_project_json,
                )
                
                # Save detailed message to AIMessage table
                result = add_ai_message(
                    conversation_id,
                    request.user_input,
                    ai_message,
                    final_project_json,
                    'error_resolution'
                )
                
                if not result:
                    print('Failed to save error resolution message in error_resolution_function')
                if not success:
                    print('Failed to update history_jsons and current_json in error_resolution_function')
                    yield f"data: {json.dumps({'type': 'error', 'chunk': 'Failed to update conversation history'})}\n\n"
                else:
                    completion_data = {
                        'type': 'complete',
                        'chunk': 'Error resolution completed successfully',
                        'conversation_id': conversation_id
                    }
                    yield f"data: {json.dumps(completion_data)}\n\n"
            else:
                print("❌ No final project generated")
                yield f"data: {json.dumps({'type': 'error', 'chunk': 'Error resolution failed - no result generated'})}\n\n"
                        
        except Exception as e:
            print(f"❌ Error in error_resolution_function: {e}")
            yield f"data: {json.dumps({'type': 'error', 'chunk': str(e)})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )

async def code_change_function(request: UserRequest, conversation_id: str, current_json: dict):
    """Code Change - Modifies existing code based on user requirements"""

    ai_message = ""
    ai_generated_content = ""
    
    async def generate():
        nonlocal ai_message, ai_generated_content

        try:
            if not current_json:
                yield f"data: {json.dumps({'type': 'error', 'chunk': 'project_context is required for code change'})}\n\n"
                return
            
            async for chunk in run_agent_with_token_limit_streaming(updating_and_error_summary_agent, request.user_input):
                if chunk.strip():
                    chunk = chunk.replace("```html","").replace("```", "").replace("html", "")
                    formatted_chunk = chunk
                    yield f"data: {json.dumps({'type': 'message', 'chunk': formatted_chunk})}\n\n"
                    await asyncio.sleep(0.05)
                    ai_message += chunk
            
            final_project_json = None
            ai_modifier_content = ""  # ✅ Store the AI modifier output
            
            async for chunk, final_json, ai_content in code_update(request.user_input, current_json):
                if chunk:
                    # Stream chunk to frontend
                    yield f"data: {json.dumps(chunk)}\n\n"
                    await asyncio.sleep(0.005)
                
                # Store the final results when available
                if final_json is not None:
                    final_project_json = final_json
                    print(f"✅ Final project received: {type(final_project_json)}")
                
                if ai_content:  # ✅ Store AI-generated content
                    ai_modifier_content = ai_content
                    print(f"✅ AI modifier content received: {len(ai_content)} characters")
            
            if final_project_json:
                success = update_current_json_with_history(
                    conversation_id, 
                    final_project_json,
                )
                
                result = add_ai_message(
                    conversation_id,
                    request.user_input,
                    ai_message,
                    final_project_json,
                    'code_change'
                )
                # Post-hoc billing for code change (Claude stage)
                try:
                    meta_for_billing = get_conversation_full(conversation_id)
                    uid = meta_for_billing.get("user_id") if isinstance(meta_for_billing, dict) else None
                    if uid and ai_modifier_content:
                        # Use AI modifier content instead of full_project for token counting
                        assistant_text = ai_modifier_content
                        
                        messages_json = [
                            {"role": "user", "content": [{"type": "text", "text": request.user_input}]},
                            {"role": "assistant", "content": [{"type": "text", "text": assistant_text}]},
                        ]
                        
                        credit = await asyncio.to_thread(
                            credits_for_messages,
                            model="claude-sonnet-4-20250514",
                            system=code_modifier_prompt,
                            messages=messages_json,
                        )
                        _ = reserve_user_tokens(int(uid), int(credit))
                        print(f"✅ Tokens deducted for AI modifier content: {credit}")
                    elif uid:
                        print("⚠️ No AI content available for billing")
                except Exception as bill_err:
                    print(f"⚠️ Failed to deduct tokens for code change: {bill_err}")
                
                if not result:
                    print('Failed to save code change message in code_change_function')
                if not success:
                    print('Failed to update history_jsons and current_json in code_change_function')
                    yield f"data: {json.dumps({'type': 'error', 'chunk': 'Failed to update conversation history'})}\n\n"
                else:
                    completion_data = {
                        'type': 'complete',
                        'chunk': 'Code update completed successfully',
                        'conversation_id': conversation_id
                    }
                    yield f"data: {json.dumps(completion_data)}\n\n"
            else:
                print("❌ No final project generated")
                yield f"data: {json.dumps({'type': 'error', 'chunk': 'Code change failed - no result generated'})}\n\n"
                        
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'chunk': str(e)})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )


async def code_conversation_function(request: ManagerRequest, conversation_id: str, project_name: str = None):
    """Code Conversation - Interactive coding session with streaming"""
    try:
        # Variables to collect streaming output
        ai_message = ""
        
        async def generate():
            nonlocal ai_message
            
            try:
                chat_history = get_conversation_messages(conversation_id)

                if chat_history is None:
                    conversation_input = [{"role": "user", "content": request.user_input}]
                else:
                    conversation_input = chat_history + [
                        {"role": "user", "content": request.user_input}
                    ]
                # Stream the conversation response
                async for chunk in run_agent_with_token_limit_streaming(code_conversation_agent, conversation_input, 1000):
                    if chunk.strip():
                        chunk = chunk.replace("```html","").replace("```", "").replace("html", "")
                        formatted_chunk = chunk
                        yield f"data: {json.dumps({'type': 'message', 'chunk': formatted_chunk})}\n\n"
                        await asyncio.sleep(0.5)
                        ai_message += chunk
                        
            except Exception as e:
                print(f"❌ Error in generate(): {e}")
                yield f"data: {json.dumps({'type': 'error', 'chunk': str(e)})}\n\n"
                return

            try:
                print(f"[DB] Saving conversation message. user_input: {request.user_input[:50]}, ai_message: {ai_message[:50]}")
                success = add_ai_message(
                    conversation_id,
                    request.user_input,
                    ai_message,
                    None,  # No generated JSON for conversation type
                    'conversation'
                )
                if success:
                    print(f"✅ Saved conversation message: {request.user_input[:50]}...")
                else:
                    print(f"❌ Failed to save conversation message")
            except Exception as e:
                print(f"❌ Error saving to database: {e}")

            yield f"data: {json.dumps({'done': True, 'type': 'complete', 'conversation_id': conversation_id, 'project_name': project_name})}\n\n"
        
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/v1/conversation/undo")
async def undo_conversation(conversation_id: str):
    """Undo the last JSON change in conversation"""
    try:
        current_json = undo_json(conversation_id)
        status = get_undo_redo_status(conversation_id)
        
        return {
            "current_json": current_json,
            "version_index": status["current_index"],
            "can_undo": status["can_undo"],
            "can_redo": status["can_redo"],
            "total_versions": status["total_versions"],
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/conversation/redo")
async def redo_conversation(conversation_id: str):
    """Redo the next JSON change in conversation"""
    try:
        current_json = redo_json(conversation_id)
        status = get_undo_redo_status(conversation_id)
        
        return {
            "current_json": current_json,
            "version_index": status["current_index"],
            "can_undo": status["can_undo"],
            "can_redo": status["can_redo"],
            "total_versions": status["total_versions"],
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/conversation/{conversation_id}/undo-redo-status")
async def get_undo_redo_status_endpoint(conversation_id: str):
    """Get undo/redo status for a conversation"""
    try:
        status = get_undo_redo_status(conversation_id)
        current_json = get_current_json(conversation_id)
        
        return {
            "current_json": current_json,
            "version_index": status["current_index"],
            "can_undo": status["can_undo"],
            "can_redo": status["can_redo"],
            "total_versions": status["total_versions"],
            "conversation_id": conversation_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/conversation/{conversation_id}")
async def get_conversation_endpoint(conversation_id: str):
    """Return current_json and all messages for a conversation.

    Response shape:
    {
      "conversation_id": str,
      "current_json": dict,
      "messages": [
        {"user_input": str, "ai_message": str, "created": str, "generated_json": dict|null}
      ]
    }
    """
    try:
        meta = get_conversation_full(conversation_id)
        if not meta:
            raise HTTPException(status_code=404, detail="Conversation not found")

        current_json = meta.get("current_json") if isinstance(meta, dict) else {}
        project_name = meta.get("session_name") if isinstance(meta, dict) else None

        raw_messages = get_messages_history(conversation_id)
        project_repo_info = get_project_publish_info(conversation_id)

        messages = []
        for m in raw_messages:
            messages.append({
                "id": m.get("id"),
                "user_input": m.get("user_message"),
                "ai_message": m.get("ai_message"),
                "created": m.get("created_at"),
                "generated_json": m.get("generated_json") if m.get("generated_json") else None
            })

        return {
            "conversation_id": conversation_id,
            "project_name": project_name,
            "current_json": current_json or {},
            "messages": messages,
            "project_repo_info": project_repo_info
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/conversation/{conversation_id}/current")
async def get_current_conversation_state(conversation_id: str):
    """Get current conversation state"""
    try:
        current_json = get_current_json(conversation_id)
        return {
            "current_json": current_json,
            "conversation_id": conversation_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}




@app.get("/api/v1/debug/user/{user_id}")
async def debug_user(user_id: int):
    """Debug user lookup"""
    try:
        user = get_user(user_id)

        print("user", user)
        if user:
            return {"user_exists": True, "user_id": user_id}
        else:
            return {"user_exists": False, "user_id": user_id}
    except Exception as e:
        return {"error": str(e)}


# @app.get("/api/v1/conversations")
# async def list_conversations(Authorization: Optional[str] = Header(None, description="Bearer access token")):
#     """List conversations (projects) for the authenticated user with basic info and dummy image url."""
#     try:
#         token = None
#         if Authorization:
#             if Authorization.lower().startswith("bearer "):
#                 token = Authorization.split(" ", 1)[1].strip()
#             else:
#                 token = Authorization.strip()

#         if not token:
#             raise HTTPException(status_code=401, detail="Missing Authorization token")

#         user_id = extract_user_id_from_token(token)
#         user = get_user(user_id)
#         if not user:
#             raise HTTPException(status_code=404, detail="Invalid user")

#         items = list_conversations_basic(user_id)
#         # Attach a dummy image URL to each
#         # for it in items:
#         #     it["image_url"] = "https://picsum.photos/seed/" + it["id"] + "/300/200"
#         return {"conversations": items}
#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/conversations")
async def list_conversations(
    workspace_filter: Optional[str] = Query(None, description="Filter by workspace: 'with-workspace', 'without-workspace', or None for all"),
    Authorization: Optional[str] = Header(None, description="Bearer access token")
):
    """List conversations (projects) for the authenticated user with optional workspace filter"""
    try:
        token = None
        if Authorization:
            if Authorization.lower().startswith("bearer "):
                token = Authorization.split(" ", 1)[1].strip()
            else:
                token = Authorization.strip()

        if not token:
            raise HTTPException(status_code=401, detail="Missing Authorization token")

        user_id = extract_user_id_from_token(token)
        user = get_user(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="Invalid user")

        if workspace_filter == "without_workspace":
            items = list_conversations_without_workspace(user_id)
        else:
            items = list_conversations_basic(user_id)  # All conversations

        return {"conversations": items}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




# -------------------
# Run the application
# -------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
