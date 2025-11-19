"""Main chat window UI component for the AI Creative Agent System"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from typing import Callable, Optional, Dict, Any
import threading
from datetime import datetime


class ChatWindow:
    """
    Main chat interface window with integrated tool output display.
    Provides real-time chat with the AI agent and displays tool execution results.
    """
    
    def __init__(self, root: tk.Tk, agent_callback: Callable[[str], str], 
                 clear_history_callback: Callable[[], None] = None):
        """
        Initialize the chat window
        
        Args:
            root: The root Tkinter window
            agent_callback: Function to call for getting agent responses
            clear_history_callback: Function to call for clearing chat history
        """
        self.root = root
        self.agent_callback = agent_callback
        self.clear_history_callback = clear_history_callback
        
        # Configure main window
        self.root.title("AI Creative Agent System")
        self.root.geometry("1200x800")
        self.root.minsize(800, 600)
        
        # Create UI elements
        self._setup_ui()
        
        # Chat state
        self.is_processing = False
        
    def _setup_ui(self):
        """Setup the main UI layout"""
        
        # Create main container with horizontal split
        main_container = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Left panel - Chat interface
        self._setup_chat_panel(main_container)
        
        # Right panel - Tool output display
        self._setup_tool_panel(main_container)
        
        # Configure pane weights
        main_container.add(self.chat_frame, weight=2)
        main_container.add(self.tool_frame, weight=1)
        
    def _setup_chat_panel(self, parent):
        """Setup the chat interface panel"""
        
        self.chat_frame = ttk.LabelFrame(parent, text="Chat with Agent", padding="5")
        
        # Chat history display
        self.chat_display = scrolledtext.ScrolledText(
            self.chat_frame,
            wrap=tk.WORD,
            font=('Consolas', 10),
            height=20
        )
        self.chat_display.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # Configure text tags for styling
        self.chat_display.tag_configure("user", foreground="blue", font=('Consolas', 10, 'bold'))
        self.chat_display.tag_configure("agent", foreground="green", font=('Consolas', 10))
        self.chat_display.tag_configure("system", foreground="gray", font=('Consolas', 9, 'italic'))
        self.chat_display.tag_configure("tool", foreground="purple", font=('Consolas', 9))
        self.chat_display.tag_configure("timestamp", foreground="gray", font=('Consolas', 8))
        
        # Input area
        input_frame = ttk.Frame(self.chat_frame)
        input_frame.pack(fill=tk.X)
        
        # Message input
        self.message_entry = tk.Text(
            input_frame,
            height=3,
            font=('Consolas', 10),
            wrap=tk.WORD
        )
        self.message_entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        # Bind Enter key to send message (Shift+Enter for new line)
        self.message_entry.bind('<Return>', self._on_enter_key)
        self.message_entry.bind('<Control-Return>', lambda e: self.message_entry.insert(tk.INSERT, '\n'))
        
        # Buttons
        button_frame = ttk.Frame(input_frame)
        button_frame.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.send_button = ttk.Button(
            button_frame,
            text="Send",
            command=self._send_message
        )
        self.send_button.pack(pady=(0, 2))
        
        self.clear_button = ttk.Button(
            button_frame,
            text="Clear",
            command=self._clear_chat
        )
        self.clear_button.pack()
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_label = ttk.Label(self.chat_frame, textvariable=self.status_var, font=('Consolas', 8))
        status_label.pack(anchor=tk.W, pady=(5, 0))
        
        # Welcome message
        self._add_system_message("AI Creative Agent System - Ready to help with world creation!")
        self._add_system_message("Type your message and press Enter to chat, or Ctrl+Enter for new line.")
        
    def _setup_tool_panel(self, parent):
        """Setup the tool output display panel"""
        
        self.tool_frame = ttk.LabelFrame(parent, text="Tool Execution Output", padding="5")
        
        # Tool output display
        self.tool_display = scrolledtext.ScrolledText(
            self.tool_frame,
            wrap=tk.WORD,
            font=('Consolas', 9),
            height=20,
            background="#f8f8f8"
        )
        self.tool_display.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # Configure text tags for tool output styling
        self.tool_display.tag_configure("tool_name", foreground="blue", font=('Consolas', 9, 'bold'))
        self.tool_display.tag_configure("success", foreground="green", font=('Consolas', 9))
        self.tool_display.tag_configure("error", foreground="red", font=('Consolas', 9))
        self.tool_display.tag_configure("info", foreground="purple", font=('Consolas', 9))
        self.tool_display.tag_configure("timestamp", foreground="gray", font=('Consolas', 8))
        
        # Tool panel controls
        tool_controls = ttk.Frame(self.tool_frame)
        tool_controls.pack(fill=tk.X)
        
        ttk.Button(
            tool_controls,
            text="Clear Tool Output",
            command=self._clear_tool_output
        ).pack(side=tk.LEFT)
        
        # Tool execution status
        self.tool_status_var = tk.StringVar(value="No tools executed")
        ttk.Label(tool_controls, textvariable=self.tool_status_var, font=('Consolas', 8)).pack(side=tk.RIGHT)
        
    def _on_enter_key(self, event):
        """Handle Enter key press in message entry"""
        if event.state & 0x4:  # Ctrl key is held
            return False  # Let default behavior (new line) occur
        else:
            self._send_message()
            return "break"  # Prevent default behavior
    
    def _send_message(self):
        """Send user message to agent"""
        message = self.message_entry.get(1.0, tk.END).strip()
        
        if not message or self.is_processing:
            return
            
        # Clear input
        self.message_entry.delete(1.0, tk.END)
        
        # Add user message to chat
        self._add_user_message(message)
        
        # Update status
        self.status_var.set("Agent is thinking...")
        self.send_button.config(state='disabled')
        self.is_processing = True
        
        # Process message in background thread
        threading.Thread(target=self._process_message, args=(message,), daemon=True).start()
        
    def _process_message(self, message: str):
        """Process message with agent in background thread"""
        try:
            # Get agent response
            response = self.agent_callback(message)
            
            # Update UI in main thread
            self.root.after(0, self._handle_agent_response, response)
            
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            self.root.after(0, self._handle_agent_response, error_msg, True)
    
    def _handle_agent_response(self, response: str, is_error: bool = False):
        """Handle agent response in main thread"""
        
        if is_error:
            self._add_system_message(response)
        else:
            # Check if response contains tool execution results
            if "\n\nTool '" in response:
                # Split agent response from tool results
                parts = response.split("\n\nTool '", 1)
                agent_part = parts[0]
                tool_part = "Tool '" + parts[1] if len(parts) > 1 else ""
                
                # Add agent response to chat
                if agent_part.strip():
                    self._add_agent_message(agent_part)
                
                # Add tool result to tool panel
                if tool_part.strip():
                    self._add_tool_output(tool_part)
            else:
                self._add_agent_message(response)
        
        # Reset UI state
        self.status_var.set("Ready")
        self.send_button.config(state='normal')
        self.is_processing = False
        self.message_entry.focus_set()
    
    def _add_user_message(self, message: str):
        """Add user message to chat display"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        self.chat_display.config(state='normal')
        self.chat_display.insert(tk.END, f"[{timestamp}] ", "timestamp")
        self.chat_display.insert(tk.END, "You: ", "user")
        self.chat_display.insert(tk.END, f"{message}\n\n")
        self.chat_display.config(state='disabled')
        self.chat_display.see(tk.END)
    
    def _add_agent_message(self, message: str):
        """Add agent message to chat display"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        self.chat_display.config(state='normal')
        self.chat_display.insert(tk.END, f"[{timestamp}] ", "timestamp")
        self.chat_display.insert(tk.END, "Agent: ", "agent")
        self.chat_display.insert(tk.END, f"{message}\n\n")
        self.chat_display.config(state='disabled')
        self.chat_display.see(tk.END)
    
    def _add_system_message(self, message: str):
        """Add system message to chat display"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        self.chat_display.config(state='normal')
        self.chat_display.insert(tk.END, f"[{timestamp}] ", "timestamp")
        self.chat_display.insert(tk.END, "System: ", "system")
        self.chat_display.insert(tk.END, f"{message}\n\n")
        self.chat_display.config(state='disabled')
        self.chat_display.see(tk.END)
    
    def _clear_chat(self):
        """Clear chat history"""
        if messagebox.askyesno("Clear Chat", "Are you sure you want to clear the chat history?"):
            self.chat_display.config(state='normal')
            self.chat_display.delete(1.0, tk.END)
            self.chat_display.config(state='disabled')
            
            # Clear agent history if callback provided
            if self.clear_history_callback:
                self.clear_history_callback()
            
            # Add welcome message again
            self._add_system_message("Chat history cleared. Ready for new conversation!")
    
    def add_tool_execution(self, tool_name: str, arguments: Dict[str, Any], 
                          status: str = "started"):
        """Add tool execution notification to tool panel"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        self.tool_display.config(state='normal')
        self.tool_display.insert(tk.END, f"[{timestamp}] ", "timestamp")
        
        if status == "started":
            self.tool_display.insert(tk.END, f"Executing: ", "info")
            self.tool_display.insert(tk.END, f"{tool_name}", "tool_name")
            self.tool_display.insert(tk.END, f"({arguments})\n")
            self.tool_status_var.set(f"Executing: {tool_name}")
            
        elif status == "completed":
            self.tool_display.insert(tk.END, f"Completed: ", "success")
            self.tool_display.insert(tk.END, f"{tool_name}", "tool_name")
            self.tool_display.insert(tk.END, f"\n")
            self.tool_status_var.set("Tool completed")
            
        elif status == "error":
            self.tool_display.insert(tk.END, f"Error in: ", "error")
            self.tool_display.insert(tk.END, f"{tool_name}", "tool_name")
            self.tool_display.insert(tk.END, f"\n")
            self.tool_status_var.set("Tool error")
        
        self.tool_display.config(state='disabled')
        self.tool_display.see(tk.END)
    
    def _add_tool_output(self, tool_output: str):
        """Add tool execution result to tool panel"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        self.tool_display.config(state='normal')
        self.tool_display.insert(tk.END, f"[{timestamp}] ", "timestamp")
        self.tool_display.insert(tk.END, f"{tool_output}\n\n", "success")
        self.tool_display.config(state='disabled')
        self.tool_display.see(tk.END)
    
    def _clear_tool_output(self):
        """Clear tool output display"""
        self.tool_display.config(state='normal')
        self.tool_display.delete(1.0, tk.END)
        self.tool_display.config(state='disabled')
        self.tool_status_var.set("Tool output cleared")