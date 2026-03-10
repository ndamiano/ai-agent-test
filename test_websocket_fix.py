#!/usr/bin/env python3
"""
Test script to verify the WebSocket 403 fix.
Tests both valid and invalid task IDs to ensure proper error handling.
"""

import asyncio
import websockets
import json

async def test_websocket_connection(task_id, expected_behavior):
    """Test WebSocket connection for a given task ID."""
    uri = f"ws://localhost:8000/tasks/{task_id}/ws"
    
    print(f"\nTesting WebSocket connection for task: {task_id}")
    print(f"Expected behavior: {expected_behavior}")
    
    try:
        async with websockets.connect(uri) as websocket:
            print("✓ WebSocket connection established")
            
            # Wait for initial message
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                data = json.loads(message)
                print(f"✓ Received message: {data}")
                
                # Check if it's an error message for invalid task
                if data.get("type") == "error":
                    print("✓ Proper error handling for invalid task ID")
                    return True
                elif data.get("type") == "task_status":
                    print("✓ Valid task status received")
                    return True
                else:
                    print(f"⚠ Unexpected message type: {data.get('type')}")
                    return False
                    
            except asyncio.TimeoutError:
                print("✗ Timeout waiting for initial message")
                return False
                
    except Exception as e:
        print(f"✗ WebSocket connection failed: {e}")
        return False

async def main():
