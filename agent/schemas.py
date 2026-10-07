"""JSON schemas for every structured turn.

Measured problem these solve: without a constraint the model narrates instead of
answering ("Теперь создам файл..."), and json.loads fails. llama-server enforces
these as a grammar, so a conforming reply is structural, not a matter of luck.
runtime.llm.repair_json stays as the fallback for servers without the feature.
"""

STR = {"type": "string"}
BOOL = {"type": "boolean"}
INT = {"type": "integer"}

PLAN = {
    "type": "object",
    "properties": {
        "goal": STR,
        "stages": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z0-9-]{2,24}$"},
                    "goal": {"type": "string", "maxLength": 120},
                    "acceptance_criteria": {"type": "string", "maxLength": 200},
                    "budget_steps": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["id", "goal", "acceptance_criteria", "budget_steps"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["goal", "stages"],
    "additionalProperties": False,
}

SUB_AGENT = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["done", "blocked", "partial"]},
        "summary": STR,
        "artifacts": {"type": "array", "items": STR},
        "files_changed": {"type": "array", "items": STR},
        "notes": STR,
    },
    "required": ["status", "summary", "artifacts", "files_changed", "notes"],
    "additionalProperties": False,
}

VERIFY = {
    "type": "object",
    "properties": {
        "passed": BOOL,
        "method": {"type": "string", "enum": ["deterministic", "llm"]},
        "evidence": STR,
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "detail": STR,
    },
    "required": ["passed", "method", "evidence", "severity", "detail"],
    "additionalProperties": False,
}

PLAN_TROUBLE = {
    "type": "object",
    "properties": {
        "escalate": BOOL,
        "root_cause_hypothesis": STR,
        "subtasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": STR, "action": STR, "verify": STR},
                "required": ["id", "action", "verify"],
                "additionalProperties": False,
            },
        },
        "reason": STR,
    },
    "required": ["escalate", "root_cause_hypothesis", "subtasks", "reason"],
    "additionalProperties": False,
}

ORCHESTRATOR = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["run_sub_agent", "run_verify", "decompose", "escalate", "done"],
        },
        "stage_id": STR,
        "task": STR,
        "criteria": STR,
        "reason": STR,
    },
    "required": ["action", "stage_id", "task", "criteria", "reason"],
    "additionalProperties": False,
}

SELF_IMPROVE = {
    "type": "object",
    "properties": {
        "weakness": STR,
        "component": {
            "type": "string",
            "enum": [
                "runtime/llm.py",
                "runtime/safety.py",
                "runtime/thermal.py",
                "agent/loop.py",
                "agent/tools.py",
                "rag/index.py",
                "skills/plan.md",
                "skills/orchestrator.md",
                "skills/sub_agent.md",
                "skills/verify.md",
                "skills/plan_trouble.md",
                "skills/self_improve.md",
            ],
        },
        "change_instruction": STR,
        "reason": STR,
        "expected_effect": STR,
        "test_plan": STR,
        "rollback_plan": STR,
    },
    "required": [
        "weakness",
        "component",
        "change_instruction",
        "reason",
        "expected_effect",
        "test_plan",
        "rollback_plan",
    ],
    "additionalProperties": False,
}

BY_SKILL = {
    "plan": PLAN,
    "sub_agent": SUB_AGENT,
    "verify": VERIFY,
    "plan_trouble": PLAN_TROUBLE,
    "orchestrator": ORCHESTRATOR,
    "self_improve": SELF_IMPROVE,
}


def for_skill(name):
    return BY_SKILL.get(name)
