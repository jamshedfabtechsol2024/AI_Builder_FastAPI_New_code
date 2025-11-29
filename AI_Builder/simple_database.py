"""
Simple database connection using psycopg2 directly
Avoids Django ORM issues in FastAPI container
"""
import os
import psycopg2
import psycopg2.extras
import json
import uuid
from typing import Optional, Dict, Any
import json, uuid
from fastapi import HTTPException
from typing import Optional
import re



# Database connection parameters
# DB_CONFIG = {
#     'host': os.environ.get('POSTGRES_HOST', 'db'),
#     'port': os.environ.get('POSTGRES_PORT', '5432'),
#     'database': os.environ.get('POSTGRES_DB', 'ai_web_builder'),
#     'user': os.environ.get('POSTGRES_USER', 'postgres'),
#     'password': os.environ.get('POSTGRES_PASSWORD', 'password'),
# }

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'aiwb-db'),
        port=os.getenv('POSTGRES_PORT', '5432'),
        database=os.getenv('POSTGRES_DB', 'ai_web_builder'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD', 'password')
    )



def _to_list(value):
    """Normalize DB JSON field to Python list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return json.loads(value.decode("utf-8"))
        except Exception:
            return []
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return []
    return []


def _to_dict(value):
    """Normalize DB JSON field to Python dict."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return json.loads(value.decode("utf-8"))
        except Exception:
            return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return {}

def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Get user by ID"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, email, first_name, last_name FROM accounts_user WHERE id = %s", (user_id,))
        result = cursor.fetchone()
        
        if result:
            user = {
                'id': result[0],
                'email': result[1],
                'first_name': result[2],
                'last_name': result[3]
            }
            return user
        return None
        
    except Exception as e:
        print(f"Error getting user: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()

def get_user_subscription(user_id: int) -> Optional[Dict[str, Any]]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, stripe_subscription_id, status, total_tokens_remaining,
                   daily_tokens_available, last_allocation_date,
                   current_period_start, current_period_end, created_at, updated_at, plan_id, user_id
            FROM accounts_usersubscription
            WHERE user_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,)
        )
        r = cursor.fetchone()
        if not r:
            return None
        return {
            'id': r[0],
            'stripe_subscription_id': r[1],
            'status': r[2],
            'total_tokens_remaining': r[3],
            'daily_tokens_available': r[4],
            'last_allocation_date': r[5],
            'current_period_start': r[6],
            'current_period_end': r[7],
            'created_at': r[8],
            'updated_at': r[9],
            'plan_id': r[10],
            'user_id': r[11],
        }
    except Exception as e:
        print(f"Error fetching user subscription: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()

def reserve_user_tokens(user_id: int, amount: int) -> Optional[Dict[str, Any]]:
    print("[DB] Reserving tokens for user:", amount)
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, status, total_tokens_remaining, daily_tokens_available
            FROM accounts_usersubscription
            WHERE user_id = %s
            ORDER BY id DESC
            LIMIT 1
            FOR UPDATE
            """,
            (user_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        sub_id, status, total_remaining, daily_available = row
        if status not in ('active', 'trialing'):
            return None
        if daily_available is None or total_remaining is None:
            return None
        print("[DB] Daily available:", daily_available)
        if daily_available < amount:
            new_daily = 0
            minus_from_total = amount - daily_available
            if total_remaining > minus_from_total:
                total_remaining= total_remaining - minus_from_total
            if total_remaining < minus_from_total:
                total_remaining=0
        if daily_available > amount:
            new_daily = daily_available - amount
        print("NEW DAIly ", new_daily)
        new_total = total_remaining
        cursor.execute(
            """
            UPDATE accounts_usersubscription
            SET daily_tokens_available = %s,
                total_tokens_remaining = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (new_daily, new_total, sub_id)
        )
        conn.commit()
        return {
            'id': sub_id,
            'daily_tokens_available': new_daily,
            'total_tokens_remaining': new_total,
        }
    except Exception as e:
        if 'conn' in locals():
            conn.rollback()
        print(f"Error reserving tokens: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()

def get_or_create_conversation(user_id: int) -> Optional[str]:
    """Get or create conversation for user"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if user exists
        cursor.execute("SELECT id FROM accounts_user WHERE id = %s", (user_id,))
        if not cursor.fetchone():
            return None
        
        # Get or create conversation
        cursor.execute("""
            SELECT id FROM ai_conversations_aiconversation 
            WHERE user_id = %s AND is_active = true
        """, (user_id,))
        
        result = cursor.fetchone()
        if result:
            return str(result[0])
        
        # Create new conversation
        conversation_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO ai_conversations_aiconversation 
            (id, user_id, messages, history_jsons, current_json, version_index, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            RETURNING id
        """, (conversation_id, user_id, '[]', '[]', None, -1, True))
        
        conversation_id = cursor.fetchone()[0]
        conn.commit()
        return str(conversation_id)
        
    except Exception as e:
        print(f"Error getting/creating conversation: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()

def add_conversation_version(conversation_id: str, ai_json: dict) -> bool:
    """Add a new version to the conversation (only JSON history, no messages)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get current conversation
        cursor.execute("""
            SELECT history_jsons, current_json, version_index 
            FROM ai_conversations_aiconversation WHERE id = %s
        """, (conversation_id,))
        
        result = cursor.fetchone()
        if not result:
            return False
        
        history_jsons, current_json, version_index = result
        
        # Parse existing data (tolerate text, bytes, or json)
        history_jsons_list = _to_list(history_jsons)
        
        # Add new JSON to history (max 20, FIFO)
        history_jsons_list.append(ai_json if isinstance(ai_json, dict) else _to_dict(ai_json))
        if len(history_jsons_list) > 20:
            # Keep last 20 (FIFO)
            history_jsons_list = history_jsons_list[-20:]
        
        # Update version index (points to the last item)
        new_version_index = (version_index if isinstance(version_index, int) else -1) + 1
        
        # Update conversation (only JSON state, no messages)
        print("[DB] Updating conversation JSON state:")
        print("[DB]  history_jsons len:", len(history_jsons_list))
        print("[DB]  version_index:", new_version_index)

        cursor.execute("""
            UPDATE ai_conversations_aiconversation 
            SET history_jsons = %s, current_json = %s, 
                version_index = %s, updated_at = NOW()
            WHERE id = %s
        """, (
            psycopg2.extras.Json(history_jsons_list),
            psycopg2.extras.Json(ai_json),
            new_version_index,
            conversation_id
        ))
        
        conn.commit()
        return True
        
    except Exception as e:
        print(f"Error adding conversation version: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()

def get_current_json(conversation_id: str) -> Dict[str, Any]:
    """Get current JSON for conversation"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT current_json FROM ai_conversations_aiconversation WHERE id = %s
        """, (conversation_id,))
        
        result = cursor.fetchone()
        if result and result[0] is not None:
            return _to_dict(result[0])
        return {}
        
    except Exception as e:
        print(f"Error getting current JSON: {e}")
        return {}
    finally:
        if 'conn' in locals():
            conn.close()


def update_current_json(conversation_id: str, new_json: Dict[str, Any]) -> bool:
    """Update current JSON for conversation"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE ai_conversations_aiconversation 
            SET current_json = %s 
            WHERE id = %s
        """, (json.dumps(new_json), conversation_id))
        
        conn.commit()
        return cursor.rowcount > 0
        
    except Exception as e:
        print(f"Error updating current JSON: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()

def update_current_json_with_history(conversation_id: str, new_json: Dict[str, Any]) -> bool:
    """Update current JSON and add to history (only JSON state, no messages)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get current conversation
        cursor.execute("""
            SELECT history_jsons, current_json, version_index 
            FROM ai_conversations_aiconversation WHERE id = %s
        """, (conversation_id,))
        
        result = cursor.fetchone()
        if not result:
            return False
        
        history_jsons, current_json, version_index = result
        
        # Parse existing data (tolerate text, bytes, or json)
        history_jsons_list = _to_list(history_jsons)
        
        # Add new JSON to history (max 20, FIFO)
        history_jsons_list.append(new_json if isinstance(new_json, dict) else _to_dict(new_json))
        if len(history_jsons_list) > 20:
            # Keep last 20 (FIFO)
            history_jsons_list = history_jsons_list[-20:]
        
        # Update version index (points to the last item)
        new_version_index = (version_index if isinstance(version_index, int) else -1) + 1
        
        # Update conversation (only JSON state, no messages)
        print("[DB] Updating conversation JSON state with history:")
        print("[DB]  history_jsons len:", len(history_jsons_list))
        print("[DB]  version_index:", new_version_index)

        cursor.execute("""
            UPDATE ai_conversations_aiconversation 
            SET history_jsons = %s, current_json = %s, 
                version_index = %s, updated_at = NOW()
            WHERE id = %s
        """, (
            json.dumps(history_jsons_list),
            json.dumps(new_json),
            new_version_index,
            conversation_id
        ))
        
        conn.commit()
        return cursor.rowcount > 0
        
    except Exception as e:
        print(f"Error updating current JSON with history: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()

def undo_json(conversation_id: str) -> Dict[str, Any]:
    """Move version pointer back by one (undo)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get current conversation
        cursor.execute("""
            SELECT history_jsons, version_index FROM ai_conversations_aiconversation WHERE id = %s
        """, (conversation_id,))
        
        result = cursor.fetchone()
        if not result:
            return {}
        
        history_jsons, version_index = result
        history_jsons_list = _to_list(history_jsons)
        
        if not history_jsons_list:
            return {}
        
        # Ensure version_index is valid
        if version_index is None or version_index < 0:
            version_index = len(history_jsons_list) - 1
        
        # Move back (can't go below 0)
        new_version_index = max(0, version_index - 1)
        
        # Get the JSON at the new index
        current_json = history_jsons_list[new_version_index] if new_version_index < len(history_jsons_list) else {}
        
        print(f"[UNDO] Moving from index {version_index} to {new_version_index}")
        print(f"[UNDO] History length: {len(history_jsons_list)}")
        
        # Update conversation
        cursor.execute("""
            UPDATE ai_conversations_aiconversation 
            SET version_index = %s, current_json = %s, updated_at = NOW()
            WHERE id = %s
        """, (new_version_index, json.dumps(current_json), conversation_id))
        
        conn.commit()
        return current_json
        
    except Exception as e:
        print(f"Error undoing JSON: {e}")
        return {}
    finally:
        if 'conn' in locals():
            conn.close()

def redo_json(conversation_id: str) -> Dict[str, Any]:
    """Move version pointer forward by one (redo)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get current conversation
        cursor.execute("""
            SELECT history_jsons, version_index FROM ai_conversations_aiconversation WHERE id = %s
        """, (conversation_id,))
        
        result = cursor.fetchone()
        if not result:
            return {}
        
        history_jsons, version_index = result
        history_jsons_list = _to_list(history_jsons)
        
        if not history_jsons_list:
            return {}
        
        # Ensure version_index is valid
        if version_index is None or version_index < 0:
            version_index = len(history_jsons_list) - 1
        
        # Move forward (can't go beyond last item)
        new_version_index = min(len(history_jsons_list) - 1, version_index + 1)
        
        # Get the JSON at the new index
        current_json = history_jsons_list[new_version_index] if new_version_index < len(history_jsons_list) else {}
        
        print(f"[REDO] Moving from index {version_index} to {new_version_index}")
        print(f"[REDO] History length: {len(history_jsons_list)}")
        
        # Update conversation
        cursor.execute("""
            UPDATE ai_conversations_aiconversation 
            SET version_index = %s, current_json = %s, updated_at = NOW()
            WHERE id = %s
        """, (new_version_index, json.dumps(current_json), conversation_id))
        
        conn.commit()
        return current_json
        
    except Exception as e:
        print(f"Error redoing JSON: {e}")
        return {}
    finally:
        if 'conn' in locals():
            conn.close()

def get_undo_redo_status(conversation_id: str) -> Dict[str, Any]:
    """Get undo/redo status for a conversation"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get current conversation
        cursor.execute("""
            SELECT history_jsons, version_index FROM ai_conversations_aiconversation WHERE id = %s
        """, (conversation_id,))
        
        result = cursor.fetchone()
        if not result:
            return {"can_undo": False, "can_redo": False, "current_index": -1, "total_versions": 0}
        
        history_jsons, version_index = result
        history_jsons_list = _to_list(history_jsons)
        
        if not history_jsons_list:
            return {"can_undo": False, "can_redo": False, "current_index": -1, "total_versions": 0}
        
        # Ensure version_index is valid
        if version_index is None or version_index < 0:
            version_index = len(history_jsons_list) - 1
        
        can_undo = version_index > 0
        can_redo = version_index < len(history_jsons_list) - 1
        
        return {
            "can_undo": can_undo,
            "can_redo": can_redo,
            "current_index": version_index,
            "total_versions": len(history_jsons_list)
        }
        
    except Exception as e:
        print(f"Error getting undo/redo status: {e}")
        return {"can_undo": False, "can_redo": False, "current_index": -1, "total_versions": 0}
    finally:
        if 'conn' in locals():
            conn.close()

def add_ai_message(conversation_id: str, user_message: str, ai_message: str, generated_json: dict = None, message_type: str = 'conversation') -> bool:
    """Add a new AI message to the ai_conversations_aimessage table"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        message_id = str(uuid.uuid4())
        
        cursor.execute(
            """
            INSERT INTO ai_conversations_aimessage 
            (id, conversation_id, user_message, ai_message, generated_json, message_type, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """,
            (
                message_id,
                conversation_id,
                user_message,
                ai_message,
                json.dumps(generated_json) if generated_json else None,
                message_type
            )
        )
        conn.commit()
        return True
    except Exception as e:
        print(f"Error adding AI message: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()

def get_messages_history(conversation_id: str):
    """Get messages history from ai_conversations_aimessage table for a conversation id."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, user_message, ai_message, generated_json, message_type, created_at
            FROM ai_conversations_aimessage 
            WHERE conversation_id = %s
            ORDER BY created_at ASC
            """,
            (conversation_id,)
        )
        rows = cursor.fetchall()
        messages = []
        for row in rows:
            messages.append({
                'id': row[0],
                'user_message': row[1],
                'ai_message': row[2],
                'generated_json': _to_dict(row[3]) if row[3] else None,
                'message_type': row[4],
                'created_at': row[5].isoformat() if row[5] else None
            })
        return messages
    except Exception as e:
        print(f"Error getting messages history: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_conversation_full(conversation_id: str):
    """Return the full conversation row details by id.

    Includes: id, user_id, session_name, is_active, current_json, history_jsons,
    version_index, created_at, updated_at. Messages are fetched separately from ai_conversations_aimessage.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, user_id, session_name, is_active, current_json, history_jsons, version_index, created_at, updated_at
            FROM ai_conversations_aiconversation
            WHERE id = %s
            """,
            (conversation_id,)
        )
        r = cursor.fetchone()
        if not r:
            return None
        return {
            'id': str(r[0]),
            'user_id': r[1],
            'session_name': r[2],
            'is_active': bool(r[3]),
            'current_json': _to_dict(r[4]),
            'history_jsons': _to_list(r[5]),
            'version_index': r[6],
            'created_at': r[7].isoformat() if r[7] else None,
            'updated_at': r[8].isoformat() if r[8] else None,
        }
    except Exception as e:
        print(f"Error getting full conversation: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def validate_conversation_id(conversation_id: str) -> bool:
    """Validate conversation ID"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM ai_conversations_aiconversation WHERE id = %s", (conversation_id,))
        return cursor.fetchone() is not None
    except Exception as e:
        print(f"Error validating conversation ID: {e}")
        return False


def list_conversations_basic(user_id: int):
    """Return basic conversation info for a user: id, session_name, created_at, updated_at."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT c.id, c.image_url, c.session_name, c.created_at, c.updated_at
            FROM ai_conversations_aiconversation c
            INNER JOIN projects_project p ON c.id = p.conversation_id
            WHERE c.user_id = %s AND p.is_deleted = false
            ORDER BY c.updated_at DESC
            """,
            (user_id,)
        )
        rows = cursor.fetchall()
        results = []
        for r in rows:
            results.append({
                'id': str(r[0]),
                'image_url': r[1],
                'title': r[2],
                'created_at': r[3].isoformat() if r[3] else None,
                'updated_at': r[4].isoformat() if r[4] else None,
            })
        return results
    except Exception as e:
        print(f"Error listing basic conversations: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def list_conversations_without_workspace(user_id: int):
    """Return basic conversation info for projects without workspace: id, session_name, created_at, updated_at."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT c.id, c.image_url, c.session_name, c.created_at, c.updated_at
            FROM ai_conversations_aiconversation c
            INNER JOIN projects_project p ON c.id = p.conversation_id
            WHERE c.user_id = %s AND p.is_deleted = false AND p.workspace_id IS NULL
            ORDER BY c.updated_at DESC
            """,
            (user_id,)
        )
        rows = cursor.fetchall()
        results = []
        for r in rows:
            results.append({
                'id': str(r[0]),
                'image_url': r[1],
                'title': r[2],
                'created_at': r[3].isoformat() if r[3] else None,
                'updated_at': r[4].isoformat() if r[4] else None,
            })
        return results
    except Exception as e:
        print(f"Error listing conversations without workspace: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def create_new_conversation(user_id: int, session_name: Optional[str] = None) -> Optional[str]:
    """Always create a new conversation (project) row and return its id.

    Creates record in ai_conversations_aiconversation with session_name as project title.
    Messages will be stored separately in ai_conversations_aimessage table.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        conversation_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO ai_conversations_aiconversation 
            (id, user_id, session_name, history_jsons, current_json, version_index, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            RETURNING id
            """,
            (
                conversation_id,
                user_id,
                session_name,
                '[]',
                None,
                -1,
                True,
            )
        )
        new_id = cursor.fetchone()[0]
        conn.commit()
        return str(new_id)
    except Exception as e:
        print(f"Error creating new conversation: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()

def generate_next_project_id(cursor):
    cursor.execute("""
        SELECT project_id 
        FROM projects_project
        WHERE project_id ~ '^#PRJ-[0-9]+$'
        ORDER BY created_at DESC
        LIMIT 1
    """)
    
    row = cursor.fetchone()

    if not row:
        return "#PRJ-100"

    match = re.search(r"#PRJ-(\d+)$", row[0])
    if match:
        next_num = int(match.group(1)) + 1
    else:
        next_num = 100

    return f"#PRJ-{next_num}"



def create_new_project_with_conversation(user_id: int, workspace_id: Optional[str] = None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        conversation_id = str(uuid.uuid4())
        project_uuid = str(uuid.uuid4())        # PRIMARY KEY UUID
        project_seq_id = generate_next_project_id(cursor)  # #PRJ-101  

        # Insert Conversation
        cursor.execute("""
            INSERT INTO ai_conversations_aiconversation 
            (id, image_url, user_id, workspace_id, session_name, history_jsons, current_json, version_index, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            RETURNING id
        """, (
            conversation_id,
            f"https://picsum.photos/seed/{conversation_id}/300/200",
            user_id,
            workspace_id,
            None,
            json.dumps([]),
            None,
            -1,
            True,
        ))
        new_conversation_id = cursor.fetchone()[0]

        # Insert Project
        cursor.execute("""
            INSERT INTO projects_project 
            (id, project_id, user_id, workspace_id, conversation_id, name, description,
             project_type, status, project_structure, metadata, current_version,
             total_versions, created_at, updated_at, last_modified,
             is_public, is_template, repo_name, git_repo_url, is_published,
             is_deleted, deleted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    NOW(), NOW(), NOW(), %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            project_uuid,        # primary key UUID
            project_seq_id,      # sequential project_id = #PRJ-xxx
            user_id,
            workspace_id,
            conversation_id,
            None,
            f"Project created from conversation {conversation_id}",
            "website",
            "draft",
            json.dumps({}),
            json.dumps({"created_via": "fastapi", "conversation_id": conversation_id}),
            0,
            0,
            False,
            False,
            None,
            None,
            False,
            False,
            None,
        ))
        new_project_id = cursor.fetchone()[0]

        conn.commit()

        return {
            "conversation_id": new_conversation_id,
            "project_id": project_uuid              # Just in case you want the UUID too
        }

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        conn.close()


def verify_workspace_access(workspace_id: str, user_id: int) -> Optional[dict]:
    """Verify workspace exists and belongs to user. Returns workspace dict or None."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, name, description, user_id, is_archived, created_at, updated_at
            FROM projects_workspace
            WHERE id = %s AND user_id = %s AND is_archived = FALSE
        """, (workspace_id, user_id))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return None
        
        return {
            "id": str(result[0]),
            "name": result[1],
            "description": result[2],
            "user_id": result[3],
            "is_archived": result[4],
            "created_at": result[5].isoformat() if result[5] else None,
            "updated_at": result[6].isoformat() if result[6] else None,
        }
    except Exception as e:
        print(f"Error verifying workspace access: {e}")
        if 'conn' in locals():
            conn.close()
        return None


def is_first_message_in_conversation(conversation_id: str) -> bool:
    """
    Check if this is the first user message in the conversation
    Count only user_message to determine if it's the first message
    Returns True if NO messages exist (first message), False if messages already exist
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) 
            FROM ai_conversations_aimessage 
            WHERE conversation_id = %s 
            AND user_message IS NOT NULL 
            AND user_message != ''
            """,
            (conversation_id,)
        )
        user_message_count = cursor.fetchone()[0]  # Get the count value
        return user_message_count == 0  # True if count is 0 (no messages exist)
        
    except Exception as e:
        print(f"Error checking first message: {e}")
        return False  # Default to False to avoid repeated naming
    finally:
        if 'conn' in locals():
            conn.close()

def update_project_name(conversation_id: str, project_name: str):
    """
    Update the project name in both ai_conversations_aiconversation and projects_project tables
    in a single transaction
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            """
            UPDATE ai_conversations_aiconversation 
            SET session_name = %s 
            WHERE id = %s
            """,
            (project_name, conversation_id)
        )
        
        cursor.execute(
            """
            UPDATE projects_project 
            SET name = %s 
            WHERE conversation_id = %s
            """,
            (project_name, conversation_id)
        )
        
        conn.commit()
        print(f"Project name updated to: {project_name} in both tables")
        
    except Exception as e:
        print(f"Error updating project name: {e}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()

def get_project_publish_info(conversation_id: str):
    """
    Get project's publish information based on conversation_id.
    Returns a dictionary:
        {
            "is_published": bool,
            "repo_name": str or None,
            "git_repo_url": str or None
        }
    If project not found → returns all values as None / False.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT is_published, repo_name, git_repo_url
            FROM projects_project
            WHERE conversation_id = %s
            """,
            (conversation_id,)
        )
        
        result = cursor.fetchone()

        if not result:
            return {
                "is_published": False,
                "repo_name": None,
                "git_repo_url": None
            }

        is_published, repo_name, git_repo_url = result

        return {
            "is_published": bool(is_published),
            "repo_name": repo_name,
            "git_repo_url": git_repo_url
        }

    except Exception as e:
        print(f"Error fetching project publish info: {e}")
        return {
            "is_published": False,
            "repo_name": None,
            "git_repo_url": None
        }

    finally:
        if 'conn' in locals():
            conn.close()

def get_conversation_messages(conversation_id: str):
    """Fetch all messages (user + AI) for a given conversation_id and return them in OpenAI-style format."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT user_message, ai_message
            FROM ai_conversations_aimessage
            WHERE conversation_id = %s
            ORDER BY created_at ASC
            """,
            (conversation_id,)
        )

        rows = cursor.fetchall()

        chat_history = []
        for row in rows:
            user_msg, ai_msg = row

            # Append user message
            if user_msg:
                chat_history.append({
                    "role": "user",
                    "content": user_msg
                })

            # Append AI message
            if ai_msg:
                chat_history.append({
                    "role": "assistant",
                    "content": ai_msg
                })

        return chat_history

    except Exception as e:
        print(f"Error fetching messages: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()
