# Specification Builder

You build specifications using TodoWrite-driven workflow.

Request: $*

## Initial Todo Structure

Create these todos immediately:

1. [pending] Analyze request and gather repository context
2. [pending] Research existing solutions and best practices
3. [pending] Define problem and how to verify solution
4. [pending] Ask clarifying questions
5. [pending] Create sample implementation
6. [pending] Draft specification
7. [pending] Generate questions: Skeptical Technical Lead
8. [pending] Generate questions: Quality/Operations Engineer
9. [pending] Create final specification

## Execution Rules

1. Mark todos as in_progress when starting
2. Complete work for that phase
3. Mark complete before moving to next
4. Question generation (7-8) can run in parallel using Task tool

## Phase Details

### Phase 1: Repository Context

- Search for similar features/patterns in codebase
- Identify conventions and frameworks used
- Note relevant configuration or dependencies

### Phase 2: Research

- Look for existing solutions to similar problems
- Check for available libraries or tools
- Research best practices for this domain

### Phase 3: Problem Definition

- State problem in one sentence
- Describe how we'll know it works
- List what could go wrong

### Phase 4: Clarifying Questions

- Focus on ambiguities and constraints
- Get details needed for implementation
- Identify any constraints or dependencies
- Ask 2-5 specific questions about requirements
- Ask what the definition of done looks like _required_
- Ask how this will be verified _required_

**Rules:**

1. Only ask 1 question at a time
2. After the user answers, ask the next question
3. Repeat this until you have asked between 2 and 5 clarifying questions and every _required_ question
4. Do not proceed to the next phase until all clarifying questions have been answered

### Phase 5: Sample Implementation

Show the core approach in 50-100 lines of code.
Include comments explaining verification points.

### Phase 6: Draft Specification

Include:

- Problem statement
- Solution approach
- How to verify it works
- Implementation phases

### Phase 7-8: Generate Questions

**Skeptical Technical Lead Questions:**

- Business value: "What's the 20% that gives 80% value?"
- Technical risk: "What are the security implications and complexity costs?"
- Alternatives: "Why not use existing solutions instead?"
- Maintenance: "Who maintains this long-term?"

**Quality/Operations Engineer Questions:**

- Testing: "How do I verify this actually works? Where will it break?"
- Monitoring: "How do I know when this fails in production?"
- Debugging: "What happens when this breaks at 3am?"
- User experience: "Will users know if it's working correctly?"

### Phase 9: Final Specification

Present the spec along with the questions for user consideration.

### Guidelines

- Do not create any artifacts

Start executing Phase 1 now.
