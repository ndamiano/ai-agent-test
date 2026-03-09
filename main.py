"""Minimal example of the refactored agentic loop"""

from agents.main_agent import MainAgent
from agents.agent_spawner import AgentSpawner
from agents.task_runner import task_runner
from tools.project_manager import project_manager
from tools.project import Project
from tools.tool_manager import tool_manager
from tools.write_to_file import register_write_tools
import datetime
import textwrap

def format_task_status(status_data: dict) -> str:
    """Format task status with a tree-like structure"""
    if "error" in status_data:
        return f"❌ Error: {status_data['error']}"
    
    task = status_data["task"]
    subtasks = status_data["subtasks"]
    events = status_data["events"]
    
    # Format task header
    goal_preview = task["goal"][:50] + "..." if len(task["goal"]) > 50 else task["goal"]
    status_icon = "✅" if task["status"] == "completed" else "🔄" if task["status"] == "in_progress" else "⏳"
    
    output = f"📋 Task: {goal_preview}\n"
    output += f"Status: {status_icon} {task['status']}\n\n"
    
    # Format subtasks
    if subtasks:
        output += "Subtasks:\n"
        for subtask in subtasks:
            icon = "✅" if subtask["status"] == "completed" else "🔄" if subtask["status"] == "in_progress" else "⏳"
            # Use agent_id and goal instead of agent_name and description
            agent_name = subtask.get("agent_id", "unknown")
            description = subtask.get("goal", "No description")
            output += f"  {icon} {agent_name:<15} — {description}\n"
        output += "\n"
    
    # Format recent events
    if events:
        output += "Recent events:\n"
        for event in events:
            timestamp = datetime.datetime.fromisoformat(event["created_at"]).strftime("%H:%M:%S")
            output += f"  [{timestamp}] {event['event_type']:<15} — {event['message']}\n"
    
    return output


def list_all_tasks() -> str:
    """List all tasks with id, truncated goal, status, and created_at"""
    try:
        from database.task_store import task_store
        tasks = task_store.list_tasks()
        
        if not tasks:
            return "No tasks found."
        
        output = "All Tasks:\n"
        output += f"{'ID':<8} {'Status':<12} {'Created At':<20} {'Goal'}\n"
        output += "-" * 80 + "\n"
        
        for task in tasks:
            goal_preview = task["goal"][:40] + "..." if len(task["goal"]) > 40 else task["goal"]
            created_at = datetime.datetime.fromisoformat(task["created_at"]).strftime("%Y-%m-%d %H:%M")
            status_icon = "✅" if task["status"] == "completed" else "🔄" if task["status"] == "in_progress" else "⏳"
            
            output += f"{task['id']:<8} {status_icon} {task['status']:<10} {created_at:<20} {goal_preview}\n"
        
        return output
    except Exception as e:
        return f"❌ Error listing tasks: {e}"


def handle_task_commands(user_input: str) -> bool:
    """Handle task runner commands. Returns True if command was handled, False otherwise."""
    parts = user_input.strip().split()
    if not parts:
        return False
    
    command = parts[0].lower()
    
    if command == "task" and len(parts) > 1:
        goal = " ".join(parts[1:])
        task_id = task_runner.create_and_run_background(goal)
        print(f"Task created with ID: {task_id}")
        print("Task is running in the background.")
        return True
    
    elif command == "status" and len(parts) > 1:
        task_id = parts[1]
        status_data = task_runner.get_status(task_id)
        formatted_status = format_task_status(status_data)
        print(formatted_status)
        return True
    
    elif command == "tasks":
        task_list = list_all_tasks()
        print(task_list)
        return True
    
    elif command == "ask" and len(parts) > 2:
        task_id = parts[1]
        question = " ".join(parts[2:])
        answer = task_runner.ask(task_id, question)
        print(f"Answer: {answer}")
        return True
    
    return False


def main():
    """Interactive chat loop with the main agent"""
    print("=== Generic Agentic Loop ===")
    print("Type 'quit' or 'exit' to end the conversation")
    print("Type 'clear' to clear conversation history")
    print("Type 'history' to view message history")
    print("Type 'tools' to list available tools\n")
    
    # Create a project
    project = Project("example-project")
    project_manager.set_current_project(project)
    
    # Register write tools
    register_write_tools(tool_manager)
    
    # Create main agent
    agent = MainAgent()
    
    print("Available tools:")
    print(tool_manager.list_tools())
    print()
    
    while True:
        try:
            user_input = input("You: ").strip()
            
            if user_input.lower() in ['quit', 'exit']:
                print("\nGoodbye!")
                break
            elif user_input.lower() == 'clear':
                agent.clear_history()
                print("Conversation history cleared.")
                continue
            elif user_input.lower() == 'history':
                history = agent.get_message_history()
                if not history:
                    print("No conversation history.")
                else:
                    print("\n=== Message History ===")
                    for i, msg in enumerate(history, 1):
                        role = msg['role'].title()
                        content = msg['content'][:100] + "..." if len(msg['content']) > 100 else msg['content']
                        print(f"{i}. {role}: {content}")
                    print("=====================\n")
                continue
            elif user_input.lower() == 'tools':
                print("\nAvailable tools:")
                print(tool_manager.list_tools())
                print()
                continue
            elif not user_input:
                continue
            
            # Handle task runner commands
            if handle_task_commands(user_input):
                continue
            
            # Get response from agent
            print("\nAgent:", end=" ")
            response = agent.chat(user_input)
            print(response)
            print()
            
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}")
            print("Please try again.\n")


if __name__ == "__main__":
    main()