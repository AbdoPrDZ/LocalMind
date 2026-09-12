# Implement Global Memory for the Generic Local Agent

We are extending the existing **generic local LLM agent framework**.

The framework is NOT a project/task application. Any existing `Project`, `Task`, or similar models/tools are only demo/test implementations and must remain decoupled from the core framework.

The goal of this change is to add a **persistent global memory system** that works across conversations.

---

## Core Concept

The framework currently has:

* `Chat`
* per-chat `context`
* `Message`
* `Agent`
* `Tool`
* `ToolRegistry`
* local LLM
* SQLAlchemy persistence

Each chat already maintains its own summarized context.

Now add a second memory layer:

```text
Chat History
    │
    ▼
Chat Context
    │
    ▼
Global Memory Entries
    │
    ▼
Global Context
    │
    ▼
Current Agent
```

### Important distinction

Do NOT create one giant global conversation summary.

Instead, create individual **memory entries**.

A memory entry represents one useful piece of information that may remain relevant across multiple chats.

Examples:

```text
type: preference
content: User prefers React Native CLI instead of Expo.

type: decision
content: The agent framework uses llama.cpp instead of Ollama.

type: fact
content: EMoveX uses Odoo as the backend business logic layer.

type: topic
content: EMoveX is a React Native application for preparing and controlling Odoo stock transfers.
```

The framework itself must not know what EMoveX, Project, Task, Odoo, etc. mean.

---

# 1. Create a Memory model

Add a persistent SQLAlchemy model for global memory.

Suggested structure:

```python
Memory
├── id
├── type
├── content
├── importance
├── source_chat_id
├── created_at
└── updated_at
```

Use appropriate SQLAlchemy types.

### `type`

Initially support:

```text
fact
preference
decision
topic
```

Use an enum or another clean extensible representation.

Do not over-engineer this yet.

### `importance`

Use a simple numeric value or enum that allows memories to be ranked.

For example:

```text
1 = low
2 = normal
3 = important
4 = critical
```

Choose the implementation that fits the existing project conventions.

### `source_chat_id`

A memory should be traceable to the chat where it originated.

If the relationship makes sense with the existing schema, create a foreign key to `Chat`.

Do not make the framework dependent on a chat existing if there is a legitimate reason for system-level memories later.

---

# 2. Create a generic Memory service

Do not put memory logic directly inside `Chat`.

Create a dedicated abstraction such as:

```text
MemoryService
```

It should provide operations similar to:

```python
create(...)
get(...)
update(...)
delete(...)
search(...)
list(...)
```

The service should be responsible for persistence and retrieval of memory entries.

Keep it independent from Project/Task/etc.

---

# 3. Add memory tools

Add tools to the existing generic Tool system.

At minimum:

## `search_global_memory`

Purpose:

Allow the LLM to search persistent global memory.

Example:

```json
{
  "query": "React Native notifications"
}
```

Return relevant memory entries including their IDs, types, contents, importance, and source chat where useful.

---

## `get_memory`

Purpose:

Load one specific memory entry when the LLM already knows its ID.

Example:

```json
{
  "memory_id": 123
}
```

---

## `get_chat_context`

Purpose:

Allow the LLM to inspect the summarized context of another conversation.

Example:

```json
{
  "chat_id": 42
}
```

This is important because global memory may tell the model:

```text
EMoveX is a stock transfer application.
Related previous chat: 42
```

but the model may need more context from chat 42.

The tool should return the stored chat context without loading the entire message history.

---

## `search_chat_history`

Do not necessarily implement full semantic/vector search yet.

Create the abstraction only if it fits naturally into the current architecture.

For the first implementation, a simple database search over messages is acceptable.

Example:

```json
{
  "query": "FCM background notifications"
}
```

Optionally support:

```json
{
  "query": "...",
  "chat_id": 42
}
```

Return relevant messages or excerpts with their chat/message IDs.

Do not load huge amounts of history.

---

## `save_memory`

Allow the LLM to explicitly create a global memory entry.

Example:

```json
{
  "type": "decision",
  "content": "Use FCM for background notifications in React Native.",
  "importance": 3,
  "source_chat_id": 42
}
```

However, the system must discourage saving trivial information.

The tool description should clearly tell the model that global memory is for information likely to remain useful across future conversations.

Do NOT save:

* greetings
* temporary questions
* every user message
* ordinary conversational details
* information relevant only to the current chat
* redundant memories

---

# 4. Global Context generation

The existing Chat has a per-chat context.

Add a mechanism to generate a **small global context snapshot** from persistent memories.

Do NOT inject the entire memory table into every LLM request.

The global context should contain only a bounded number of important/relevant memory entries.

For example:

```text
GLOBAL MEMORY

Important preferences:
- User prefers React Native CLI instead of Expo.

Important decisions:
- Local LLM runtime uses llama.cpp.
- The framework should remain generic.

Known topics:
- EMoveX is an Odoo stock-transfer mobile application.
- OTA is a self-hosted OTA platform.
```

Keep this bounded by character/token limits.

The exact formatting should be implemented in a dedicated component/service rather than hardcoded throughout `Chat`.

---

# 5. Chat → LLM context

When sending a message, the model should receive:

```text
SYSTEM PROMPT

GLOBAL CONTEXT
...

CURRENT CHAT CONTEXT
...

USER MESSAGE
...
```

The global context should be intentionally small.

Do not send:

* every previous chat
* every memory entry
* complete message history

The model can retrieve additional information using tools.

---

# 6. Memory retrieval workflow

The intended behavior is:

```text
User asks something
       │
       ▼
LLM sees current chat context
       │
       ▼
LLM sees small global context
       │
       ├── enough information?
       │       │
       │       └── YES → answer
       │
       └── needs more information
               │
               ▼
       search_global_memory
               │
               ▼
       finds relevant memory
               │
               ▼
       memory references source chat
               │
               ▼
       get_chat_context
               │
               ▼
       LLM understands previous discussion
               │
               ▼
             answer
```

The LLM should be encouraged to retrieve information when the global context is insufficient rather than guessing.

---

# 7. Memory creation after conversations

Integrate memory creation into the existing chat lifecycle, but keep it lightweight.

After a meaningful interaction, the system should be able to identify potentially useful memories.

There are two possible mechanisms:

### Preferred initial implementation

Let the LLM explicitly call:

```text
save_memory
```

when it determines something is worth remembering.

This keeps memory creation inside the existing tool architecture.

Do not build a second complicated memory-extraction pipeline yet.

Later we can add automatic memory extraction.

---

# 8. Avoid duplicate memories

Before saving a new memory, try to avoid obvious duplicates.

For example, do not create:

```text
User prefers React Native CLI.
User prefers React Native CLI instead of Expo.
User uses React Native CLI rather than Expo.
```

as three independent memories.

For the first implementation, simple duplicate detection is sufficient.

Exact or normalized-content matching is acceptable.

Do NOT introduce embeddings/vector databases just for this.

---

# 9. Memory references

A memory should preserve provenance.

Example:

```text
Memory #123

type: decision

content:
Use FCM for background notifications in React Native.

source_chat_id:
42
```

This allows the system to trace:

```text
Memory
  ↓
Source Chat
  ↓
Chat Context
  ↓
Message History
```

Do not lose this relationship.

---

# 10. Tool security

Follow the existing tool architecture.

The LLM must NEVER receive direct access to:

* SQLAlchemy sessions
* raw SQL
* arbitrary database queries

The LLM interacts through controlled application tools.

For example:

```text
LLM
 ↓
search_global_memory
 ↓
MemoryService
 ↓
SQLAlchemy
 ↓
Database
```

not:

```text
LLM
 ↓
SQL
 ↓
Database
```

---

# 11. Keep the framework generic

This is extremely important.

Do not add code such as:

```python
if project:
    ...
if task:
    ...
if emovex:
    ...
```

inside the memory framework.

The memory system should work equally well for:

```text
software projects
personal preferences
technical decisions
people
companies
documents
research topics
configuration decisions
etc.
```

The existing Project/Task implementation should continue to function only as a demo/test domain.

---

# 12. Database migrations

Use the project's existing migration strategy.

If Alembic is already configured:

* create the migration
* add the Memory table
* add required indexes/foreign keys

If migrations are not yet configured, do not introduce an unnecessarily large migration system just for this feature. Follow the existing project architecture.

Add indexes where useful, especially for:

* memory type
* importance
* source chat
* timestamps

---

# 13. Agent integration

Update the existing Agent so that memory tools are available through the normal ToolRegistry.

Do NOT hardcode memory logic into the Agent if the existing architecture already supports registered tools.

The Agent should simply receive:

```text
ToolRegistry
    ├── memory tools
    ├── filesystem tools
    ├── database tools
    ├── project demo tools
    └── ...
```

Memory is another capability of the generic agent.

---

# 14. Chat integration

Modify the existing `Chat` implementation so that it:

1. Loads the current chat context.
2. Builds a bounded global context snapshot.
3. Sends both to the Agent.
4. Continues maintaining the existing per-chat context behavior.
5. Does not replace the existing chat context system.
6. Does not inject full global memory into the prompt.

The public API should remain simple:

```python
chat.send(...)
chat.send_stream(...)
chat.context
chat.history
```

Do not make callers manually manage global memory.

---

# 15. Streaming

Preserve the existing streaming behavior.

Memory/tool operations should not break:

```python
chat.send_stream(...)
```

Make sure tool calls and any context/memory metadata are not accidentally exposed as part of the final user-visible response.

Follow the existing Qwen3 `<think>` handling already implemented in the project.

---

# 16. Context vs Memory

Maintain this distinction throughout the implementation:

### Chat Context

Short-term working memory for one conversation.

```text
"What are we currently discussing?"
```

### Global Memory

Persistent information useful across conversations.

```text
"What should I remember about this user/topic/decision?"
```

### Chat History

Original source material.

```text
"What exactly was said?"
```

### Global Context

A small prompt-friendly snapshot generated from global memory.

```text
"What global information should the model see immediately?"
```

Do not merge these concepts into one table or one giant string unless the existing architecture requires it.

---

# 17. Do not add vector databases yet

Do NOT introduce:

* Chroma
* FAISS
* Qdrant
* Pinecone
* embeddings
* RAG frameworks

for this implementation.

The first version should use:

```text
SQLAlchemy
+
structured Memory entries
+
simple search
+
tool-based retrieval
```

We can add semantic retrieval later without changing the high-level memory architecture.

---

# 18. Tests

Add tests for at least:

### Memory persistence

```text
create memory
retrieve memory
update memory
delete memory
```

### Search

```text
save several memories
search for a topic
verify relevant results
```

### Provenance

```text
memory → source_chat_id
```

### Chat context retrieval

```text
get_chat_context(chat_id)
```

### Global context

Verify that:

* important memories appear
* global context is bounded
* unrelated memories are not blindly dumped into the prompt

### Tool calls

Verify the LLM-facing memory tools validate their arguments and return controlled results.

---

# 19. Before modifying code

First inspect the existing project.

Understand:

* current directory structure
* Agent implementation
* Chat implementation
* Tool base class
* Tool registry
* SQLAlchemy models
* database/session handling
* migrations
* streaming implementation
* Qwen3 output handling
* existing Project/Task demo implementation

Do not rewrite the architecture unnecessarily.

Reuse existing abstractions where they already fit.

If an abstraction needs improvement, make the smallest clean change required.

---

# 20. Implementation order

Implement in this order:

```text
1. Inspect existing architecture
2. Add Memory model
3. Add migration if appropriate
4. Add MemoryService
5. Add memory tools
6. Register memory tools
7. Add bounded global-context builder
8. Integrate global context into Chat → Agent
9. Integrate get_chat_context retrieval
10. Add save-memory capability
11. Add duplicate protection
12. Add tests
13. Verify existing Project/Task demo still works
14. Verify normal chats still work
15. Verify streaming still works
```

---

# Expected final architecture

The resulting architecture should conceptually look like:

```text
                         Agent
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
        Chat Context   Global Memory   Tools
              │            │            │
              │            │       ┌────┴─────────┐
              │            │       │              │
              │            │    Memory         Other
              │            │     Tools          Tools
              │            │
              └──────┬─────┘
                     ▼
                    LLM
                     │
                     ▼
               Tool Retrieval
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
   Global Memory            Chat Context
   / MemoryService          / ChatService
          │                     │
          └──────────┬──────────┘
                     ▼
                  Database
```

The important architectural principle is:

> **Global memory is persistent structured knowledge, global context is only the small portion exposed to the LLM automatically, and historical conversations are retrieved on demand through tools.**

Implement this as an incremental change to the existing codebase. Do not create a separate application and do not redesign unrelated parts of the framework.
