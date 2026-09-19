"""Render engine sections into prompt text and chat-completion messages."""


def format_state_block(state: dict, current_chat_id: int | None = None) -> str:
  """Render the structured chat state as a compact prompt section."""
  lines: list[str] = []
  if current_chat_id is not None:
    lines.append(f"CURRENT CHAT ID: {current_chat_id}")
  lines.append("CHAT STATE")

  fields = []
  if state.get("objective"):
    fields.append(f"Objective: {state['objective']}")
  if state.get("current_topic"):
    fields.append(f"Current topic: {state['current_topic']}")
  for key, label in (
    ("decisions", "Decisions"),
    ("constraints", "Constraints"),
    ("open_questions", "Open questions"),
    ("important_entities", "Key entities"),
  ):
    values = state.get(key) or []
    if values:
      fields.append(f"{label}: {'; '.join(values)}")
  if fields:
    lines.append("\n".join(fields))

  if state.get("last_summary"):
    lines.append(state["last_summary"])

  return "\n".join(lines)


def format_memories_block(memories: list[dict]) -> str:
  """Render retrieved global memories; empty input renders an empty string."""
  if not memories:
    return ""
  lines = ["RELEVANT GLOBAL MEMORIES"]
  lines.extend(f"- {memory['content']}" for memory in memories)
  return "\n".join(lines)


def build_system_section(
  base_prompt: str,
  memories_block: str,
  state_block: str,
  instructions: str,
) -> str:
  """Concatenate the fixed prompt pieces into the final system message."""
  sections = [base_prompt]
  if memories_block:
    sections.append(memories_block)
  if state_block:
    sections.append(state_block)
  sections.append(instructions)
  return "\n\n".join(sections)