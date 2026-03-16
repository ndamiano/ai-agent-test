# tests/test_connectors.py
import unittest
import sys
import os

from llm_clients.lmstudio_client import LMStudioConnector

class TestConnectors(unittest.TestCase):
    
    def setUp(self):
        self.connector = LMStudioConnector()
    
    def test_function_calling(self):
        """Test that function calling works with LMStudio"""
        tools = [{
            "type": "function",
            "function": {
                "name": "multiply",
                "description": "Multiply two numbers",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "number", "description": "First number"},
                        "b": {"type": "number", "description": "Second number"}
                    },
                    "required": ["a", "b"]
                }
            }
        }]
        
        messages = [{"role": "user", "content": "What's 7 times 8?"}]
        
        response = self.connector.generate_with_tools(messages, tools)
        
        # Assertions
        self.assertIn("choices", response)
        self.assertIsNotNone(response["choices"][0]["message"])
        
        # Check if we got a tool call
        message = response["choices"][0]["message"]
        if message.get("tool_calls"):
            tool_call = message["tool_calls"][0]
            print(f"✓ AI wants to call: {tool_call['function']['name']}")
            print(f"✓ With arguments: {tool_call['function']['arguments']}")
            
            # Verify the tool call
            self.assertEqual(tool_call['function']['name'], 'multiply')
            self.assertIn('a', tool_call['function']['arguments'])
            self.assertIn('b', tool_call['function']['arguments'])
        else:
            self.fail("No tool call detected in response")

if __name__ == '__main__':
    unittest.main()