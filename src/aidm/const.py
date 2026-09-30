"""Global constants for the AI Dungeon Master engine."""

# Directory layout (resolved relative to the working directory)
GAMES_DIR = "games"
SAVES_DIR = "saves"

# Filenames
CONFIG_FILENAME = "config.json"
SCENARIO_FILENAME = "scenario.json"
META_FILENAME = "meta.json"

# Event log
EVENTS_DIR = "events"
LOG_FILENAME = "log.jsonl"

# v2 memory substrate (per save)
STORY_FILENAME = "story.json"
TRANSCRIPT_FILENAME = "transcript.jsonl"

# v2 context assembler budgets (per-turn DM prompt)
TRANSCRIPT_TURNS = 3
MAX_PRESENT_IN_CONTEXT = 6

# Tool-managed character tag lists: durable traits + transient conditions.
MAX_TRAITS = 12
MAX_CONDITIONS = 8

# Authored-identity fields the write tools refuse to mutate (v2 protected keys).
# Every type protects its structural keys; characters also protect authored persona.
PROTECTED_FIELDS_BASE = frozenset(
    {"slug", "type", "regen", "last_simulated_turn", "last_seen_turn"}
)
PROTECTED_FIELDS_BY_TYPE = {
    "character": frozenset(
        {"name", "race", "personality", "background", "role", "secret", "vice", "virtue"},
    ),
}

# Dev LLM trace (written per save in dev mode): full prompts, tool calls, reasoning
LLM_LOG_FILENAME = "llm.jsonl"
# Human-readable Markdown rendering of the same exchanges (dev mode)
LLM_MD_FILENAME = "llm.md"
# Live per-step turn trace (dev mode): one timestamped line per step, tailable as it runs
TRACE_FILENAME = "trace.log"

# Simulation (Phase 6)
# An entity is "near"/relevant if the player holds a relationship with it at or above
# this value (scale is author-defined; the fixture uses 0..100).
RELEVANCE_THRESHOLD = 50
# Cap the number of deterministic catch-up updates at the start of a turn.
MAX_CATCHUP_ENTITIES = 3

# REPL display
WELCOME_PROMPT = "aidm> "
GAME_PROMPT = "> "
HISTORY_FILENAME = ".aidm_history"
