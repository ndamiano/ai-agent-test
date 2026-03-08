# Integration Tests

This directory contains integration tests that require external services or running instances of external applications.

## test_connectors.py

This test requires a live LMStudio connection to be running on the local machine. It tests the function calling capabilities of the LMStudio connector.

### Requirements

- LMStudio must be running locally
- The LMStudio server must be accessible on the configured endpoint
- A compatible model must be loaded in LMStudio

### Running the Tests

These tests should **NOT** be run in CI environments as they require a live LMStudio instance.

To run these tests locally:

```bash
# Ensure LMStudio is running first
python -m pytest tests/integration/test_connectors.py -v
```

### CI Considerations

These tests are intentionally excluded from the main test suite to avoid CI failures when LMStudio is not available. They should only be run in development environments where LMStudio is properly configured and running.