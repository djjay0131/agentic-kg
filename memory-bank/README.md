# Memory Bank: Project Documentation System

## Overview

This Memory Bank is your SINGLE SOURCE OF TRUTH for project. After any interruption or context reset, you MUST read ALL files in this directory to understand the current state and continue work effectively.

**CRITICAL:** Treat this Memory Bank as living documentation. You are its STEWARD—responsible for keeping it accurate, current, and valuable.

Use a `memory_bank` folder at the TOP of the project to store the memory bank files.

## Core Documentation Files

### 1. projectbrief.md - **START HERE** ⚡

_The FOUNDATION—rarely changes_

- Core objectives and REQUIREMENTS
- Success criteria and testing checklist
- Project CONSTRAINTS and scope boundaries
- What's IN SCOPE and what's OUT OF SCOPE

### 2. productContext.md - **THE "WHY"** 💡

_User needs and business context_

- Problem STATEMENT and solution approach
- User experience GOALS and workflows
- Key user STORIES
- Success METRICS

### 3. systemPatterns.md - **IMPLEMENTATION GUIDE** 🏗️

_Technical architecture—your BUILD PLAN_

- System architecture and component RELATIONSHIPS
- All 7 implementation PHASES with detailed tasks
- Design PATTERNS and technical decisions
- Critical code PATHS and workflows

### 4. techContext.md - **TECHNICAL DETAILS** 🔧

_Tools, setup, and constraints_

- Technologies, frameworks, and DEPENDENCIES
- Development environment SETUP
- Code PATTERNS and conventions
- Common ISSUES and debugging tips

### 5. activeContext.md - **CURRENT FOCUS** 📍

_LIVING DOCUMENT—changes frequently_

- Current work phase and IMMEDIATE next steps
- Recent DECISIONS and their rationale
- Key PATTERNS and preferences to follow
- Important LEARNINGS and insights

### 6. progress.md - **STATUS TRACKING** 📊

_History and evolution—tracks CHANGE over time_

- What's COMPLETED and what REMAINS
- Known ISSUES and anticipated challenges
- Testing STRATEGY and validation criteria
- Timeline TRACKING and milestones

## File Dependencies & Reading Order

### 🔄 SELF-CHECK: Have I Read All Memory Bank Files?

**Before starting ANY work, ask yourself:**

- [ ] Did I read ALL 6 core files?
- [ ] Do I understand the CURRENT state from activeContext.md?
- [ ] Do I know WHAT'S DONE from progress.md?
- [ ] Do I understand the WHY from productContext.md?
- [ ] Can I explain the technical APPROACH from systemPatterns.md?

**On First Read or Context Reset:**

1. START with `projectbrief.md` (understand the MISSION)
2. Read `productContext.md` (understand the USER NEEDS)
3. Read `systemPatterns.md` (understand the ARCHITECTURE)
4. Read `techContext.md` (understand the TOOLS)
5. Read `activeContext.md` (understand CURRENT state)
6. Read `progress.md` (understand what's DONE/REMAINING)

**During Active Work:**

- ALWAYS check `activeContext.md` FIRST for current focus
- REFERENCE `systemPatterns.md` for implementation details
- UPDATE `progress.md` as work completes
- UPDATE `activeContext.md` with NEW decisions and learnings

## When to Update Memory Bank

### 🔄 CONTINUOUS SELF-ASSESSMENT

**AFTER EVERY SIGNIFICANT MILESTONE, ASK:**

- "What did I just learn that should be DOCUMENTED?"
- "Are my DECISIONS captured in activeContext.md?"
- "Is progress.md CURRENT with what I've completed?"
- "Have I updated PATTERNS I discovered in systemPatterns.md?"
- "Is activeContext.md ACCURATE for the next session?"

### AUTOMATIC Update Triggers:

- AFTER completing ANY implementation phase
- WHEN making significant technical DECISIONS
- WHEN discovering important project PATTERNS
- WHEN requirements or scope CHANGES
- WHEN you realize documentation is OUT OF DATE

### USER-TRIGGERED Updates:

When user says **"update memory bank"**:

1. REVIEW ALL 6 core files
2. UPDATE any files that need changes
3. FOCUS especially on `activeContext.md` and `progress.md`
4. DOCUMENT new insights, decisions, and patterns

### 🎯 STEWARDSHIP MINDSET

You are the GUARDIAN of this knowledge. Ask yourself regularly:

- "If I reset RIGHT NOW, would the Memory Bank let me continue effectively?"
- "What CONTEXT would be lost if not documented?"
- "What DECISIONS did I make that aren't captured?"
- "What LEARNINGS should be preserved?"

## Best Practices

### Writing GUIDELINES

- **Be SPECIFIC and ACTIONABLE** - Every statement should be USEFUL for resuming work
- **Document DECISIONS and RATIONALE** - Future you needs to know WHY, not just WHAT
- **Keep CURRENT** - Outdated docs are WORSE than no docs
- **Use EXAMPLES** - Code snippets and concrete examples CLARIFY abstract concepts
- **Write for CONTEXT RESET** - Assume you'll forget everything; what would you need?

### Structure GUIDELINES

- **HIERARCHICAL organization** - Use headers consistently (##, ###, ####)
- **SCANNABLE format** - Use lists, bold, and clear sections
- **CROSS-REFERENCE** - Point to other files when relevant
- **NO DUPLICATION** - Information should live in ONE canonical place
- **EVOLVE continuously** - Update as understanding deepens

### Content GUIDELINES by File

- **projectbrief.md** - STABLE foundation (rarely changes)
- **productContext.md** - User NEEDS and business context
- **systemPatterns.md** - Technical SOURCE OF TRUTH
- **techContext.md** - Tools and SETUP information
- **activeContext.md** - CURRENT moment (changes FREQUENTLY)
- **progress.md** - Historical EVOLUTION (tracks change over TIME)

### 🔄 SELF-REFLECTION Questions

**Before ending ANY work session:**

1. "Did I UPDATE activeContext.md with my current state?"
2. "Did I mark COMPLETED tasks in progress.md?"
3. "Did I DOCUMENT any new decisions or patterns?"
4. "Will the NEXT session have enough context to continue?"
5. "What would I want to KNOW if I reset right now?"

## Additional Documentation

Beyond the 6 core files, create additional files/folders as needed for:

- Complex feature specifications
- Integration documentation
- API references
- Testing strategies
- Deployment procedures

Keep additional docs in `memory_bank/` or organized subdirectories.

---

## 🎯 Your Commitment as STEWARD

This Memory Bank is NOT just documentation—it's your LIFELINE across context resets. You are its STEWARD.

**YOUR RESPONSIBILITY:**

- MAINTAIN accuracy and currency
- UPDATE after significant work
- DOCUMENT decisions and learnings
- REFLECT on what future you needs
- EVOLVE the structure as needed

**REMEMBER:**

- Your EFFECTIVENESS depends on this documentation
- Future you has NO MEMORY of current work
- Every UNDOCUMENTED decision is LOST forever
- Good stewardship NOW saves hours LATER

**FINAL CHECK before ending ANY session:**

- [ ] Is activeContext.md CURRENT?
- [ ] Is progress.md UPDATED?
- [ ] Are new DECISIONS documented?
- [ ] Are new PATTERNS captured?
- [ ] Would I have enough CONTEXT to continue?

**Maintain this Memory Bank with PRECISION and CLARITY. Your future effectiveness depends ENTIRELY on its accuracy.**
