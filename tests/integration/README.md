# Integration Tests

This directory contains integration tests that require external services or running instances of external applications.

## test_connectors.py

This test requires a live LLM server (LM Studio, `llama-server`, …) on the local machine. It tests the function calling capabilities of the connector.

### Requirements

- The LLM server must be running locally
- It must be reachable at `llm.base_url`
- A compatible model must be loaded

### Running the Tests

These tests should **NOT** be run in CI environments as they require a live LLM server.

To run these tests locally:

```bash
# Ensure the LLM server is running first
python -m pytest tests/integration/test_connectors.py -v
```

### CI Considerations

These tests are intentionally excluded from the main test suite to avoid CI failures when no LLM server is available. They should only be run in development environments where one is configured and running.
