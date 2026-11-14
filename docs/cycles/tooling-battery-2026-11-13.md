# Tooling battery report — 2026-11-13

Gateway: 0.8.1 · entries targeted: 73 · measured: 51 · served-but-unmeasured: 0 · never served: 22

## Rubric (mechanical — no hand-entered scores)

Components per entry: T1 exact-args, T2 large-payload integrity,
T3 parallel calls, T4 tool-result follow-through, T5 error recovery
(identical re-issue after a tool error = 0), V envelope validity
(fraction of the entry's calls with strict-JSON arguments naming a
declared tool; a gateway escalation away from the pinned entry on a
tool task counts one invalid call and scores that task 0 — the
pinned response failed the gateway's own validation).
Composite = mean of available components; an entry needs >= 3
available components to be measured. Tooling score =
1 + round(4 * composite), clamped 1..5 — with one gate: if the
envelope component is 0 (every call the entry produced was
malformed), the score floors at 1 regardless of composite.
Tasks the pinned entry never served are no_evidence and
contribute nothing.


| entry | tooling | composite | T1 | T2 | T3 | T4 | T5 | env | esc |
|---|---|---|---|---|---|---|---|---|---|
| groq/gpt-oss-20b | 5 | 0.92 | 1.00 | 1.00 | 0.50 | 1.00 | 1.00 | 1.00 | 0 |
| groq/gpt-oss-120b | 5 | 0.92 | 1.00 | 1.00 | 0.50 | 1.00 | 1.00 | 1.00 | 0 |
| groq/qwen3.8-27b | 4 | 0.83 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| openrouter/north-mini-code-free | — | — | — | — | — | — | — | — | 0 |
| openrouter/dots-3-note-preview-free | — | — | — | — | — | — | — | — | 0 |
| deepinfra/deepseek-v4-flash | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| ollama/gpt-oss-120b | — | — | — | — | — | — | — | — | 0 |
| siliconflow/glm-5.3 | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| premium/gpt-5.5 | — | — | — | — | — | — | — | — | 0 |
| deepseek/deepseek-v4-pro | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| zai/glm-5.3 | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| meta/muse-spark-1.1 | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| huggingface/deepseek-v4-flash | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| cerebras/gpt-oss-120b | 5 | 0.92 | 1.00 | 1.00 | 0.50 | 1.00 | 1.00 | 1.00 | 0 |
| novita/gpt-oss-120b | 5 | 0.92 | 1.00 | 1.00 | 0.50 | 1.00 | 1.00 | 1.00 | 0 |
| zai/glm-4.7-flash | 5 | 1.00 | 1.00 | 1.00 | — | — | — | 1.00 | 0 |
| mistral/mistral-large | — | — | — | — | — | — | — | — | 0 |
| mistral/codestral | 4 | 0.83 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| google/gemini-3.5-flash-lite | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| cloudflare/gpt-oss-120b | — | — | — | — | — | — | — | — | 0 |
| cloudflare/glm-4.7-flash | — | — | — | — | — | — | — | — | 0 |
| zai/glm-4.5-flash | 5 | 1.00 | 1.00 | — | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| zai/glm-4.6v-flash | — | 1.00 | — | — | — | — | — | 1.00 | 0 |
| cloudflare/llama-4-scout | 5 | 1.00 | 1.00 | — | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| cloudflare/qwen3.8-27b | 5 | 1.00 | 1.00 | — | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| cloudflare/gpt-oss-20b | 5 | 1.00 | 1.00 | 1.00 | — | — | — | 1.00 | 0 |
| cloudflare/mistral-small-3.1 | — | — | — | — | — | — | — | — | 0 |
| openrouter/nemotron-3.5-lightning-free | — | — | — | — | — | — | — | — | 0 |
| openrouter/nemotron-3-nano-omni-free | — | — | — | — | — | — | — | — | 0 |
| openrouter/ernie-5.5-flash-free | — | — | — | — | — | — | — | — | 0 |
| openrouter/flint-free | — | — | — | — | — | — | — | — | 0 |
| openrouter/lfm-3-1.7b-free | — | — | — | — | — | — | — | — | 0 |
| openrouter/lfm-2.5-2.6b-free | — | — | — | — | — | — | — | — | 0 |
| openrouter/nemotron-3-ultra-free | — | — | — | — | — | — | — | — | 0 |
| openrouter/nemotron-3.5-super-vl-free | — | — | — | — | — | — | — | — | 0 |
| openrouter/free-router | — | — | — | — | — | — | — | — | 0 |
| openrouter/deepseek-v4-flash | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| openrouter/glm-5.3-flash | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| openrouter/qwen3.8-flash | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| google/gemini-3.1-flash-lite | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| openrouter/gemini-3.1-flash-lite | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| openrouter/llama-4-maverick | 4 | 0.83 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| openrouter/claude-haiku-5.5 | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| siliconflow/qwen3-32b | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| siliconflow/deepseek-v3.2 | 5 | 1.00 | 1.00 | — | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| siliconflow/deepseek-v4-flash | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| deepinfra/gpt-oss-120b | 5 | 0.92 | 1.00 | 1.00 | 0.50 | 1.00 | 1.00 | 1.00 | 0 |
| deepinfra/deepseek-v4-flash-0731 | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| deepinfra/llama-3.1-8b-turbo | 5 | 1.00 | 1.00 | — | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| deepinfra/mistral-small-24b | — | — | — | — | — | — | — | — | 0 |
| deepinfra/qwen3.8-27b | 5 | 1.00 | 1.00 | — | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| novita/llama-3.1-8b | — | — | — | — | — | — | — | — | 0 |
| novita/glm-4.7-flash | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| novita/qwen3.8-flash | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| novita/deepseek-v4-flash-0731 | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| novita/qwen3.8-27b | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| novita/kimi-k2.5 | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| novita/glm-4.6 | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| huggingface/glm-5.3-flash | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| huggingface/qwen3.8-27b | 5 | 1.00 | 1.00 | — | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| mistral/mistral-small | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| mistral/ministral-8b | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| mistral/mistral-medium | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| groq/qwen3.6-27b | 5 | 0.90 | — | 1.00 | 0.50 | 1.00 | 1.00 | 1.00 | 0 |
| zai/glm-4.6 | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| zai/glm-5.3-flash | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| meta/muse-spark-1.2 | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| meta/muse-spark-1.3 | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| cerebras/qwen-3.8-27b | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |
| aion/aion-3.5-mini | — | — | — | — | — | — | — | — | 0 |
| aion/aion-2.0 | — | — | — | — | — | — | — | — | 0 |
| nvidia/nemotron-3.5-lightning | 5 | 0.92 | 1.00 | 1.00 | 0.50 | 1.00 | 1.00 | 1.00 | 0 |
| nvidia/nemotron-3-nano-omni | 5 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0 |

## Per-entry detail

### groq/gpt-oss-20b
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "one_call_only", "calls": ["get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge\u202f7 is **4815\u202fkPa**, located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### groq/gpt-oss-120b
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "one_call_only", "calls": ["get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge\u202f7 was **4815\u202fkPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### groq/qwen3.8-27b
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 0.625, "lines_intact": 100, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 is **4815 kPa**, and it is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### openrouter/north-mini-code-free
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "deepseek/deepseek-v4-pro"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t4_follow_through: {"outcome": "no_evidence", "served": "deepseek/deepseek-v4-pro"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### openrouter/dots-3-note-preview-free
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### deepinfra/deepseek-v4-flash
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "Here are the details for **Gauge 7**:\n\n- **Reading:** 4815\n- **Unit:** kPa (kilopascals)\n- **Location:** north vault"}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### ollama/gpt-oss-120b
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### siliconflow/glm-5.3
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 was **4815 kPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### premium/gpt-5.5
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "deepseek/deepseek-v4-pro"}
- t2_large_payload: {"outcome": "no_evidence", "served": "deepseek/deepseek-v4-pro"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepseek/deepseek-v4-pro"}
- t4_follow_through: {"outcome": "no_evidence", "served": "deepseek/deepseek-v4-pro"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepseek/deepseek-v4-pro"}

### deepseek/deepseek-v4-pro
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 was **4815 kPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### zai/glm-5.3
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 was **4815 kPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### meta/muse-spark-1.1
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for **gauge 7** is:\n\n*   **Reading:** 4815\n*   **Unit:** kPa\n*   **Location:** north vault"}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### huggingface/deepseek-v4-flash
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for **gauge 7** is **4815 kPa**, and it is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### cerebras/gpt-oss-120b
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "one_call_only", "calls": ["get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The gauge reading is **4815\u202fkPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### novita/gpt-oss-120b
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "one_call_only", "calls": ["get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge\u202f7 was **4815\u202fkPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### zai/glm-4.7-flash
- measured: True · served_self: True · envelope: 2 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### mistral/mistral-large
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### mistral/codestral
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "no_tool_call"}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 was 4815 kPa, located in the north vault."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### google/gemini-3.5-flash-lite
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 is **4815 kPa**, and it is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### cloudflare/gpt-oss-120b
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### cloudflare/glm-4.7-flash
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "deepseek/deepseek-v4-pro"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### zai/glm-4.5-flash
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "no_evidence", "served": null}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "\nBased on the latest reading for gauge 7:\n\n- **Reading**: 4815\n- **Unit**: kPa (kilopascals)\n- **Location**: north vault"}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### zai/glm-4.6v-flash
- measured: False · served_self: False · envelope: 2 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "cloudflare/gpt-oss-120b"}
- t2_large_payload: {"outcome": "no_evidence", "served": "cloudflare/gpt-oss-120b"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "cloudflare/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "aion/aion-2.0"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### cloudflare/llama-4-scout
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "no_evidence", "served": null}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The reading was 4815 kPa, and the gauge is located in the north vault."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### cloudflare/qwen3.8-27b
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "no_evidence", "served": null}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "\n\nGauge 7's latest reading is **4815 kPa**, and it's located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### cloudflare/gpt-oss-20b
- measured: True · served_self: True · envelope: 2 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### cloudflare/mistral-small-3.1
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### openrouter/nemotron-3.5-lightning-free
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### openrouter/nemotron-3-nano-omni-free
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### openrouter/ernie-5.5-flash-free
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### openrouter/flint-free
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### openrouter/lfm-3-1.7b-free
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}

### openrouter/lfm-2.5-2.6b-free
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### openrouter/nemotron-3-ultra-free
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### openrouter/nemotron-3.5-super-vl-free
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### openrouter/free-router
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### openrouter/deepseek-v4-flash
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for **gauge 7** is **4815 kPa**, and the gauge is located at the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### openrouter/glm-5.3-flash
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 was **4815 kPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### openrouter/qwen3.8-flash
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 is **4815 kPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### google/gemini-3.1-flash-lite
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The reading for gauge 7 is 4815 kPa, and it is located in the north vault."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### openrouter/gemini-3.1-flash-lite
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The reading for gauge 7 is 4815 kPa, and it is located in the north vault."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### openrouter/llama-4-maverick
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 0.0, "lines_intact": 0, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 is 4815 kPa, and it's located in the north vault."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### openrouter/claude-haiku-5.5
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The reading for gauge 7 is **4815 kPa**, and the gauge is in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### siliconflow/qwen3-32b
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "\n\nThe latest reading for gauge 7 is **4815 kPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### siliconflow/deepseek-v3.2
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "Based on the lookup results:\n\n- **Reading:** 4815\n- **Unit:** kPa (kilopascals)\n- **Location:** north vault\n\nSo gauge 7 in the north vault has a current reading"}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### siliconflow/deepseek-v4-flash
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for **gauge 7** is:\n\n- **Reading:** 4815\n- **Unit:** kPa\n- **Location:** north vault"}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### deepinfra/gpt-oss-120b
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 0.994, "lines_intact": 159, "lines_total": 160}
- t3_parallel_calls: {"outcome": "one_call_only", "calls": ["get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge\u202f7 is **4815\u202fkPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### deepinfra/deepseek-v4-flash-0731
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 is:\n\n- **Reading:** 4815\n- **Unit:** kPa\n- **Location:** north vault"}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### deepinfra/llama-3.1-8b-turbo
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The reading was 4815 kPa, and the gauge is located in the north vault."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### deepinfra/mistral-small-24b
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### deepinfra/qwen3.8-27b
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "\n\nThe latest reading for gauge 7 is **4815 kPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### novita/llama-3.1-8b
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### novita/glm-4.7-flash
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 is **4815** in the unit of **kPa**, located at the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### novita/qwen3.8-flash
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "Gauge 7's latest reading was **4815 kPa**, and it is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### novita/deepseek-v4-flash-0731
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "Gauge 7's latest reading is **4815 kPa**, located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### novita/qwen3.8-27b
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 is **4815 kPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### novita/kimi-k2.5
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "Based on the lookup, here is the information for gauge 7:\n\n- **Reading:** 4815\n- **Unit:** kPa (kilopascals)\n- **Location:** north vault"}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### novita/glm-4.6
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 is:\n\n- **Reading:** 4815\n- **Unit:** kPa\n- **Location:** North vault"}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### huggingface/glm-5.3-flash
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 was **4815 kPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### huggingface/qwen3.8-27b
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "\n\nGauge 7's latest reading is **4815 kPa**, and it's located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### mistral/mistral-small
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 is **4815 kPa**, and it is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### mistral/ministral-8b
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for **gauge 7** is **4815 kPa**. It is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### mistral/mistral-medium
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 is **4815 kPa**, and it is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### groq/qwen3.6-27b
- measured: True · served_self: True · envelope: 5 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "one_call_only", "calls": ["get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 is **4815 kPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### zai/glm-4.6
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 is:\n\n- **Reading:** 4815\n- **Unit:** kPa\n- **Location:** North vault"}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### zai/glm-5.3-flash
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 was **4815 kPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### meta/muse-spark-1.2
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for **gauge 7** is:\n\n*   **Reading:** 4815\n*   **Unit:** kPa\n*   **Location:** north vault"}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### meta/muse-spark-1.3
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 was **4815 kPa**, and the gauge is located in the **north vault**.\n"}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### cerebras/qwen-3.8-27b
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "\n\nThe latest reading for gauge 7 is **4815 kPa**, located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### aion/aion-3.5-mini
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "deepseek/deepseek-v4-pro"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "deepseek/deepseek-v4-pro"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### aion/aion-2.0
- measured: False · served_self: False · envelope: 0 valid / 0 invalid
- t1_exact_args: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t2_large_payload: {"outcome": "no_evidence", "served": "google/gemini-3.1-flash-lite"}
- t3_parallel_calls: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}
- t4_follow_through: {"outcome": "no_evidence", "served": "nvidia/nemotron-3-nano-omni"}
- t5_error_recovery: {"outcome": "no_evidence", "served": "deepinfra/gpt-oss-120b"}

### nvidia/nemotron-3.5-lightning
- measured: True · served_self: True · envelope: 6 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "one_call_only", "calls": ["get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge\u202f7 is **4815\u202fkPa**, and the gauge is located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}

### nvidia/nemotron-3-nano-omni
- measured: True · served_self: True · envelope: 7 valid / 0 invalid
- t1_exact_args: {"outcome": "exact"}
- t2_large_payload: {"outcome": "integrity_scored", "integrity": 1.0, "lines_intact": 160, "lines_total": 160}
- t3_parallel_calls: {"outcome": "both_calls", "calls": ["get_time", "get_weather"]}
- t4_follow_through: {"outcome": "followed_through", "excerpt": "The latest reading for gauge 7 is **4815 kPa**, located in the **north vault**."}
- t5_error_recovery: {"outcome": "adapted", "next_call": "list_elements"}


---

# Addendum — corrected-shape re-run (2026-11-13b) and merged results

The first run's request shape carried `temperature: 0.0`, which is itself a request fault on real upstreams (OpenAI gpt-5.5 rejects any non-default temperature verbatim; Mistral rejects greedy sampling unless top_p == 1, error 3054 — both proven by direct differential). The battery was fixed (no temperature sent) and the 22 unmeasured entries were re-probed, plus three measured entries as shape-stability controls. **All three controls reproduced their run-1 scores exactly (5, 5, 5)**, so the two runs merge: run 1 stands for its 51 measured entries; the re-run adds 3 newly measured.

## Newly measured in the re-run

| entry | tooling | note |
|---|---|---|
| premium/gpt-5.5 | 5 | run-1 failure was the battery's temperature fault, now proven by OpenAI's verbatim rejection |
| mistral/mistral-large | 5 | same — Mistral greedy-sampling rule |
| zai/glm-4.6v-flash | 2 | THIN evidence: exactly 3 components — T3 one-call-only (0.5), T5 failed gateway validation (0.0), envelope 0.5; its own 429s starved the remaining tasks. Provisional; priority re-probe |

## Final merged score table (54 of 73 measured)

| entry | tooling | run |
|---|---|---|
| cerebras/gpt-oss-120b | 5 | 1 |
| cerebras/qwen-3.8-27b | 5 | 1 |
| cloudflare/gpt-oss-20b | 5 | 1 |
| cloudflare/llama-4-scout | 5 | 1 |
| cloudflare/qwen3.8-27b | 5 | 1 |
| deepinfra/deepseek-v4-flash | 5 | 1 |
| deepinfra/deepseek-v4-flash-0731 | 5 | 1 |
| deepinfra/gpt-oss-120b | 5 | 1 |
| deepinfra/llama-3.1-8b-turbo | 5 | 1 |
| deepinfra/qwen3.8-27b | 5 | 1 |
| deepseek/deepseek-v4-pro | 5 | 1 |
| google/gemini-3.1-flash-lite | 5 | 1 |
| google/gemini-3.5-flash-lite | 5 | 1 |
| groq/gpt-oss-120b | 5 | 1 |
| groq/gpt-oss-20b | 5 | 1 |
| groq/qwen3.6-27b | 5 | 1 |
| huggingface/deepseek-v4-flash | 5 | 1 |
| huggingface/glm-5.3-flash | 5 | 1 |
| huggingface/qwen3.8-27b | 5 | 1 |
| meta/muse-spark-1.1 | 5 | 1 |
| meta/muse-spark-1.2 | 5 | 1 |
| meta/muse-spark-1.3 | 5 | 1 |
| mistral/ministral-8b | 5 | 1 |
| mistral/mistral-large | 5 | 2 |
| mistral/mistral-medium | 5 | 1 |
| mistral/mistral-small | 5 | 1 |
| novita/deepseek-v4-flash-0731 | 5 | 1 |
| novita/glm-4.6 | 5 | 1 |
| novita/glm-4.7-flash | 5 | 1 |
| novita/gpt-oss-120b | 5 | 1 |
| novita/kimi-k2.5 | 5 | 1 |
| novita/qwen3.8-27b | 5 | 1 |
| novita/qwen3.8-flash | 5 | 1 |
| nvidia/nemotron-3-nano-omni | 5 | 1 |
| nvidia/nemotron-3.5-lightning | 5 | 1 |
| openrouter/claude-haiku-5.5 | 5 | 1 |
| openrouter/deepseek-v4-flash | 5 | 1 |
| openrouter/gemini-3.1-flash-lite | 5 | 1 |
| openrouter/glm-5.3-flash | 5 | 1 |
| openrouter/qwen3.8-flash | 5 | 1 |
| premium/gpt-5.5 | 5 | 2 |
| siliconflow/deepseek-v3.2 | 5 | 1 |
| siliconflow/deepseek-v4-flash | 5 | 1 |
| siliconflow/glm-5.3 | 5 | 1 |
| siliconflow/qwen3-32b | 5 | 1 |
| zai/glm-4.5-flash | 5 | 1 |
| zai/glm-4.6 | 5 | 1 |
| zai/glm-4.7-flash | 5 | 1 |
| zai/glm-5.3 | 5 | 1 |
| zai/glm-5.3-flash | 5 | 1 |
| groq/qwen3.8-27b | 4 | 1 |
| mistral/codestral | 4 | 1 |
| openrouter/llama-4-maverick | 4 | 1 |
| zai/glm-4.6v-flash | 2 | 2 |

Distribution: 5 × 50, 4 × 3 (groq/qwen3.8-27b, mistral/codestral, openrouter/llama-4-maverick — all lost the T2 large-payload component), 2 × 1 (zai/glm-4.6v-flash, thin evidence).

## Unmeasured after both runs (19) — with reasons

| entry | reason |
|---|---|
| aion/aion-2.0 | Aion upstream 429 congested (both runs; served other traffic in-window) |
| aion/aion-3.5-mini | Aion upstream 429 congested (both runs) |
| cloudflare/glm-4.7-flash | Cloudflare account pool exhausted (429 congested) |
| cloudflare/gpt-oss-120b | Cloudflare account pool exhausted (error 4006 class; partial same-day recovery seen in attempt ledger but no clean probe window) |
| cloudflare/mistral-small-3.1 | Cloudflare account pool exhausted (429 congested) |
| deepinfra/mistral-small-24b | Upstream does not support tool calling (direct evidence) — supports_tools corrected to false in this release; not measurable by design |
| novita/llama-3.1-8b | Upstream does not support function calling (direct evidence) — supports_tools corrected to false in this release; not measurable by design |
| ollama/gpt-oss-120b | Upstream 402 account_dead — the standing lane_dead fault; needs Jordan’s account look |
| openrouter/dots-3-note-preview-free | OpenRouter shared free pool: 429 congested (both runs) |
| openrouter/ernie-5.5-flash-free | OpenRouter shared free pool: 429 congested (both runs) |
| openrouter/flint-free | OpenRouter shared free pool: 429 congested (both runs) |
| openrouter/free-router | OpenRouter shared free pool: 429 congested (both runs) |
| openrouter/lfm-2.5-2.6b-free | OpenRouter shared free pool: 429 congested (both runs) |
| openrouter/lfm-3-1.7b-free | OpenRouter shared free pool: 429 congested (both runs) |
| openrouter/nemotron-3-nano-omni-free | OpenRouter shared free pool: 429 congested (both runs) |
| openrouter/nemotron-3-ultra-free | OpenRouter shared free pool: 429 congested (both runs) |
| openrouter/nemotron-3.5-lightning-free | OpenRouter shared free pool: 429 congested (both runs) |
| openrouter/nemotron-3.5-super-vl-free | OpenRouter shared free pool: 429 congested (both runs) |
| openrouter/north-mini-code-free | OpenRouter shared free pool: 429 congested (both runs) |

Unmeasured entries receive NO tooling score and rank for tooling exactly as they did before the dimension existed (the stage-1 fallback rule). Their absence is evidence about reachability at probe time, not about tool quality. The weekly re-probe (docs/cycles/weekly-reprobe.md) is the remedy; the OpenRouter and dots-3/nemotron-ultra entries are its priority targets.
