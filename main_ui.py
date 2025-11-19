"""Main UI application entry point for the AI Creative Agent System"""

import tkinter as tk
from tkinter import messagebox
import sys
import traceback
from pathlib import Path

# Add the project root to Python path for imports
sys.path.append(str(Path(__file__).parent))

from ui.chat_window import ChatWindow
from agents.ui_main_agent import UIMainAgent
from tools.project_manager import project_manager
from tools.project import Project


class AIAgentUIApp:
    """Main application class for the AI Creative Agent System UI"""
    
    def __init__(self):
        """Initialize the application"""
        
        # Create main window
        self.root = tk.Tk()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        # Initialize agent and project
        self._setup_agent_and_project()
        
        # Create chat window with agent callback
        self.chat_window = ChatWindow(
            root=self.root,
            agent_callback=self._agent_callback,
            clear_history_callback=self._clear_history_callback
        )
        
        # Connect agent to UI for tool execution updates
        self.agent.set_tool_callback(self._tool_execution_callback)
        
        # Set up menu bar
        self._setup_menu()
        
        # Handle exceptions
        self.root.report_callback_exception = self._handle_exception
    
    def _setup_agent_and_project(self):
        """Initialize the agent and default project"""
        try:
            # Set up default project
            default_project = Project("ui-session")
            project_manager.set_current_project(default_project)
            
            # Create UI-enabled agent
            self.agent = UIMainAgent(use_native_tools=True)
            
        except Exception as e:
            messagebox.showerror("Initialization Error", 
                               f"Failed to initialize agent or project:\n{str(e)}")
            sys.exit(1)
    
    def _setup_menu(self):
        """Setup the application menu bar"""
        
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Clear Chat", command=self._clear_chat)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_closing)
        
        # Tools menu
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Available Tools", command=self._show_tools_info)
        tools_menu.add_command(label="Conversation Info", command=self._show_conversation_info)
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self._show_about)
    
    def _agent_callback(self, message: str) -> str:
        """
        Callback function for getting agent responses
        
        Args:
            message: User's message
            
        Returns:
            Agent's response
        """
        try:
            return self.agent.chat(message)
        except Exception as e:
            error_msg = f"Agent error: {str(e)}"
            print(f"Agent callback error: {e}")
            traceback.print_exc()
            return error_msg
    
    def _clear_history_callback(self):
        """Callback function for clearing agent history"""
        try:
            self.agent.clear_history()
        except Exception as e:
            print(f"Clear history error: {e}")
    
    def _tool_execution_callback(self, tool_name: str, arguments: dict, status: str):
        """
        Callback function for tool execution updates
        
        Args:
            tool_name: Name of the tool being executed
            arguments: Arguments passed to the tool
            status: Execution status ('started', 'completed', 'error')
        """
        try:
            # Update the chat window with tool execution info
            self.chat_window.add_tool_execution(tool_name, arguments, status)
        except Exception as e:
            print(f"Tool callback error: {e}")
    
    def _clear_chat(self):
        """Clear chat history"""
        self.chat_window._clear_chat()
    
    def _show_tools_info(self):
        """Show information about available tools"""
        try:
            tools_info = self.agent.get_available_tools_info()
            
            # Create a new window to display tools info
            info_window = tk.Toplevel(self.root)
            info_window.title("Available Tools")
            info_window.geometry("600x400")
            
            # Add scrollable text widget
            from tkinter import scrolledtext
            text_widget = scrolledtext.ScrolledText(
                info_window,
                wrap=tk.WORD,
                font=('Consolas', 10),
                padx=10,
                pady=10
            )
            text_widget.pack(fill=tk.BOTH, expand=True)
            text_widget.insert(tk.END, tools_info)
            text_widget.config(state='disabled')
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to get tools info:\n{str(e)}")
    
    def _show_conversation_info(self):
        """Show conversation state information"""
        try:
            conv_info = self.agent.get_conversation_summary()
            
            info_text = f"""Conversation Summary:
            
• Message Count: {conv_info['message_count']}
• Native Tools Enabled: {conv_info['use_native_tools']}
• UI Callbacks Active: {conv_info['has_tool_callback']}

Recent Messages:
"""
            
            for i, msg in enumerate(conv_info['recent_messages'][-3:], 1):
                role = msg['role'].title()
                content = msg['content'][:100] + "..." if len(msg['content']) > 100 else msg['content']
                info_text += f"\n{i}. {role}: {content}"
            
            messagebox.showinfo("Conversation Info", info_text)
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to get conversation info:\n{str(e)}")
    
    def _show_about(self):
        """Show about dialog"""
        about_text = """AI Creative Agent System - UI Version

A conversational AI system for generating creative content
including Skyrim mods, visual novels, and fantasy worlds.

Features:
• Interactive chat interface with the AI agent
• Real-time tool execution monitoring
• World generation and character creation tools
• Integrated project management

Built with Python and Tkinter."""
        
        messagebox.showinfo("About", about_text)
    
    def _handle_exception(self, exc_type, exc_value, exc_traceback):
        """Handle uncaught exceptions"""
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        error_msg = f"An unexpected error occurred:\n\n{exc_type.__name__}: {str(exc_value)}"
        
        # Log to console
        print(f"Uncaught exception: {error_msg}")
        traceback.print_exception(exc_type, exc_value, exc_traceback)
        
        # Show error dialog
        messagebox.showerror("Unexpected Error", error_msg)
    
    def _on_closing(self):
        """Handle application closing"""
        if messagebox.askokcancel("Quit", "Do you want to quit?"):
            self.root.quit()
            self.root.destroy()
    
    def run(self):
        """Start the application main loop"""
        try:
            print("Starting AI Creative Agent System UI...")
            self.root.mainloop()
        except KeyboardInterrupt:
            print("\nApplication interrupted by user")
        except Exception as e:
            print(f"Application error: {e}")
            traceback.print_exc()
        finally:
            print("Application closing...")


def main():
    """Main entry point"""
    try:
        app = AIAgentUIApp()
        app.run()
    except Exception as e:
        print(f"Failed to start application: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()