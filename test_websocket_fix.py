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
    """Main test function to verify WebSocket functionality."""
    print("Testing WebSocket 403 fix...")
    
    # Test with an invalid task ID
    print("\n=== Testing with invalid task ID ===")
    result1 = await test_websocket_connection("invalid-task-id", "Should receive error message")
    
    # Test with a valid task ID (if server is running)
    print("\n=== Testing with valid task ID ===")
    result2 = await test_websocket_connection("test-task-123", "Should receive task status")
    
    # Summary
    print("\n=== Test Results ===")
    print(f"Invalid task ID test: {'✓ PASS' if result1 else '✗ FAIL'}")
    print(f"Valid task ID test: {'✓ PASS' if result2 else '✗ FAIL'}")
    
    if result1 and result2:
        print("\n🎉 All tests passed! WebSocket 403 fix is working correctly.")
    else:
        print("\n❌ Some tests failed. Please check the WebSocket implementation.")

if __name__ == "__main__":
    asyncio.run(main())
