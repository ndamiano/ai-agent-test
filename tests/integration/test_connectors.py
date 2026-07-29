import unittest

from llm_clients.connector import get_connector


class TestConnectors(unittest.TestCase):
    
    def setUp(self):
        self.connector = get_connector()
    
    def test_function_calling(self):
        """Test that function calling works against the live LLM server"""
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
        
        self.assertIn("choices", response)
        self.assertIsNotNone(response["choices"][0]["message"])
        
        message = response["choices"][0]["message"]
        if message.get("tool_calls"):
            tool_call = message["tool_calls"][0]
            print(f"✓ AI wants to call: {tool_call['function']['name']}")
            print(f"✓ With arguments: {tool_call['function']['arguments']}")
            
            self.assertEqual(tool_call['function']['name'], 'multiply')
            self.assertIn('a', tool_call['function']['arguments'])
            self.assertIn('b', tool_call['function']['arguments'])
        else:
            self.fail("No tool call detected in response")

if __name__ == '__main__':
    unittest.main()
