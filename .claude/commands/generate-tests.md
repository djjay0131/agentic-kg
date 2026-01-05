---
description: Generate pytest tests for specified files
---

Run the test-generator agent to create comprehensive pytest test suites.

If a specific file is mentioned in the conversation, generate tests for that file.
Otherwise, check for files flagged in the most recent code review (MAJ-006 type issues).

For each file:
1. Analyze all classes, functions, and methods
2. Identify testable units and their requirements
3. Generate tests covering:
   - Happy path scenarios
   - Edge cases and boundary conditions
   - Error handling and validation
   - Serialization (for Pydantic models)
4. Create the test file in the appropriate tests/ directory
5. Include fixtures for common setup

Output the complete test file content and save it to the appropriate location.
