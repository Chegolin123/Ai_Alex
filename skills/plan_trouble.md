# Troubleshooting plan

When a task fails or produces incomplete output, follow these steps in order:

1. Read the failure events from the last hour. Look for `json_parse_failed`, `tool_call_unparsed`, `tool_call_unknown`, `attempts > 1` on the same stage, `throttle_events > 0`, or long delays.
2. If JSON output is incomplete or not written, the parser in `runtime/llm.py` may not cover the current case. Test the parser against the actual output before retrying.
3. Retry the failing stage once with corrected parameters. If it fails again, escalate to a sub-agent.
   - **Special rule for `write-markdown`:** if the `write-markdown` stage required a second attempt (`attempts=2`) and that attempt succeeded, do **not** make a third attempt. The markdown file is already written, and rewriting it adds no reliability — it only wastes tokens and time. Go straight to `verify`. This is the narrowest bottleneck: `write-markdown` reliably needs one extra attempt (~+10 sec per cycle), whereas `write-json` is already reliable (`attempts=1` in most runs). Skipping the pointless third `write-markdown` attempt keeps `avg_attempts_per_stage` near ~1.1 and lowers `avg_time_per_cycle_sec`.
   - **Special rule for `write-json`:** if the `write-json` stage required a second attempt (`attempts=2`) and that attempt succeeded, do **not** make a third attempt. The JSON file is already written, and rewriting it adds no reliability — it only wastes tokens and time and increases the risk of rollback under heat (`temp_c>=59`). Go straight to `verify`. This is the narrowest bottleneck: `write-json` reliably needs one extra attempt (~+10 sec per cycle), whereas `write-markdown` is already reliable (`attempts=1` in most runs). Skipping the pointless third `write-json` attempt keeps `avg_attempts_per_stage` near ~1.0 and lowers `avg_time_per_cycle_sec`.
4. Verify the result before marking the task complete.

Always produce a complete, valid output. Do not stop after partial success.