import json
import tiktoken
import time
import os
from datetime import datetime
from dotenv import load_dotenv, find_dotenv
from agents import Agent, OpenAIChatCompletionsModel, AsyncOpenAI, ModelSettings

# Load environment variables
_ = load_dotenv(find_dotenv())
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Initialize external client and model
external_client: AsyncOpenAI = AsyncOpenAI(
    api_key=CLAUDE_API_KEY,
    base_url="https://api.anthropic.com/v1/",
)

llm_model: OpenAIChatCompletionsModel = OpenAIChatCompletionsModel(
    model= "claude-sonnet-4-20250514", #"claude-haiku-4-5-20251001",#
    openai_client=external_client
)

# Define web search tool configuration
web_search_tool = {
    "type": "web_search_20250305",
    "name": "web_search"
}

external_client_2: AsyncOpenAI = AsyncOpenAI(
    api_key=GEMINI_API_KEY,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
)

gemini_llm_model: OpenAIChatCompletionsModel = OpenAIChatCompletionsModel(
    model="gemini-2.5-flash",
    openai_client=external_client_2
)

gemini_llm_model_2: OpenAIChatCompletionsModel = OpenAIChatCompletionsModel(
    model="gemini-2.0-flash",
    openai_client=external_client_2
)
 #"gemini-2.0-flash",
class TokenManager:
    """Enhanced Token Management Class for handling API rate limits"""
    
    def __init__(self, max_tokens_per_minute=8000, token_limit_threshold=7500):
        self.max_tokens_per_minute = max_tokens_per_minute
        self.token_limit_threshold = token_limit_threshold
        self.tokens_used = 0
        self.start_time = datetime.now()
        self.continuation_needed = False
        self.last_generated_content = ""
        try:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self.tokenizer = tiktoken.get_encoding("gpt2")

    def count_tokens(self, text):
        """Count tokens in text"""
        try:
            if text is None:
                return 0
            if isinstance(text, dict):
                text = json.dumps(text)
            return len(self.tokenizer.encode(str(text)))
        except Exception:
            return max(1, len(str(text)) // 4)

    def check_and_wait(self, estimated_response_tokens=500):
        """Check token usage and wait if approaching limit"""
        current_time = datetime.now()
        elapsed_time = (current_time - self.start_time).total_seconds()
        if elapsed_time >= 60:
            self.tokens_used = 0
            self.start_time = current_time
            print("✅ Token counter reset - New minute started")
            return
        if self.tokens_used + estimated_response_tokens >= self.token_limit_threshold:
            wait_time = 60 - elapsed_time
            if wait_time > 0:
                print(f"⏰ Token limit approaching ({self.tokens_used}/{self.token_limit_threshold})")
                print(f"⏸️  Waiting {wait_time:.1f} seconds until next minute...")
                time.sleep(wait_time)
                self.tokens_used = 0
                self.start_time = datetime.now()
                print("✅ Wait complete - Continuing...")

    def add_tokens(self, tokens):
        """Add tokens to usage counter"""
        self.tokens_used += tokens
        if self.tokens_used > self.max_tokens_per_minute:
            self.tokens_used = self.max_tokens_per_minute
        print(f"📊 Tokens used this minute: {self.tokens_used}/{self.max_tokens_per_minute}")

    def set_continuation_needed(self, content):
        """Mark that continuation is needed and store the last content"""
        self.continuation_needed = True
        self.last_generated_content = content
        print("🔄 Continuation needed - partial content saved")

    def get_continuation_context(self):
        """Get context for continuation"""
        return {
            "partial_content": self.last_generated_content,
            "continuation_needed": self.continuation_needed
        }


class ProjectContext:
    """Enhanced Project Context Class for managing project state"""
    
    def __init__(self):
        self.project_structure = {}
        self.generated_files = {}
        self.project_name = ""
        self.framework = ""
        self.partial_generation = False
        self.current_file_being_generated = ""
        self.error_files = []
        self.original_files = {}

    def set_project_info(self, name, framework):
        """Set project name and framework"""
        self.project_name = name
        self.framework = framework

    def add_file(self, file_path, content):
        """Add a file to the project"""
        self.generated_files[file_path] = content

    def set_original_files(self, files_dict):
        """Store original files for error resolution"""
        self.original_files = files_dict.copy()

    def set_error_files(self, file_list):
        """Set files that have errors"""
        self.error_files = file_list

    def get_error_files_with_content(self):
        """Get error files with their content"""
        error_files_content = {}
        for file_path in self.error_files:
            if file_path in self.original_files:
                error_files_content[file_path] = self.original_files[file_path]
            elif file_path in self.generated_files:
                error_files_content[file_path] = self.generated_files[file_path]
        return error_files_content

    def set_partial_generation(self, file_path, partial_content):
        """Mark that a file is partially generated"""
        self.partial_generation = True
        self.current_file_being_generated = file_path
        self.generated_files[file_path] = partial_content

    def get_context_summary(self):
        """Get summary of current project context"""
        return {
            "project_name": self.project_name,
            "framework": self.framework,
            "existing_files": list(self.generated_files.keys()),
            "partial_generation": self.partial_generation,
            "current_file": self.current_file_being_generated,
            "error_files": self.error_files,
            "file_summaries": {
                path: content[:200] + "..." if len(content) > 200 else content
                for path, content in self.generated_files.items()
            }
        }




# -------------------
# All Agents Defined Here
# -------------------

# Import prompts from local module using relative import
from .prompts import (
    manager_prompt, planner_prompt, codegen_prompt,
    error_files_finder_prompt, error_resolving_prompt,
    modifier_files_finder_prompt, code_modifier_prompt, code_conversation_prompt, project_summary_prompt, name_suggest_prompt, updating_and_error_summary_prompt
)


# claude_model_settings = ModelSettings(
#     temperature=0.7,
#     max_tokens=4000,
#     top_p=1.0,
#     reasoning=True  # Enable extended thinking
# )

gemini_model_settings = ModelSettings(
    temperature=0.2,
)

manager_agent = Agent(
    name="Manager",
    instructions=manager_prompt,
    model=llm_model
)

planner_agent = Agent(
    name="ProjectPlanner",
    instructions=planner_prompt,
    model=gemini_llm_model_2
)

codegen_agent = Agent(
    name="CodeGenerator", 
    instructions=codegen_prompt,
    model=llm_model
    # tools=[web_search_tool]  # Add tools parameter
)

# Enhanced Error Resolution Agents
error_files_finder_agent = Agent(
    name="ErrorFilesFinder",
    instructions=error_files_finder_prompt,
    model=gemini_llm_model
)

error_resolver_agent = Agent(
    name="ErrorResolver",
    instructions=error_resolving_prompt,
    model=llm_model
)

modifier_files_finder_agent = Agent(
    name="ChangeCodeAgent", 
    instructions=modifier_files_finder_prompt,
    model=gemini_llm_model
)

modifier_agent = Agent(
    name="CodeModifier",
    instructions=code_modifier_prompt,
    model=llm_model
)


code_conversation_agent = Agent(
    name="CodeConversation",
    instructions=code_conversation_prompt,
    model=gemini_llm_model
)

project_summary_agent = Agent(
    name="ProjectSummary",
    instructions=project_summary_prompt,
    model=gemini_llm_model,
    model_settings = gemini_model_settings
)


name_suggestion_agent = Agent(
    name="NameSuggestion",
    instructions=name_suggest_prompt,
    model=gemini_llm_model
)


updating_and_error_summary_agent = Agent(
    name="UpdatingAndErrorSummary",
    instructions=updating_and_error_summary_prompt,
    model=gemini_llm_model
)
