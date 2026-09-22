from pathlib import Path

p = Path('run_v15_forward_test.py')
s = p.read_text(encoding='utf-8')
replacements = [
    (
        """    @staticmethod\n    def _trade_key_from_record(r):\n        return f\"{r.get('coin', '')}|{r.get('side', '')}|{r.get('opened_at', '')}|{r.get('core', '')}\"\n""",
        """    @staticmethod\n    def _trade_key_from_record(r):\n        opened = r.get('opened_at') or r.get('signal_time') or ''\n        return f\"{r.get('coin', '')}|{r.get('side', '')}|{opened}|{r.get('core', '')}\"\n""",
    ),
    (
        """    def _append_closed_record(self, r, trade_no):\n        result = str(r.get(\"result\", \"\")).upper().strip()\n        if result not in {\"TP\", \"SL\"} or not self._valid_closed_record({**r, \"signal_time\": r.get(\"opened_at\", \"\")}):\n            return\n        self.master_rows.append({\n""",
        """    def _append_closed_record(self, r, trade_no):\n        result = str(r.get(\"result\", \"\")).upper().strip()\n        if result not in {\"TP\", \"SL\", \"TP HIT\", \"SL HIT\"}:\n            return\n        normalized = {**r, \"signal_time\": r.get(\"signal_time\") or r.get(\"opened_at\", \"\")}\n        if not self._valid_closed_record(normalized):\n            return\n        signal_time = r.get(\"opened_at\") or r.get(\"signal_time\") or \"\"\n        result_short = \"TP\" if result.startswith(\"TP\") else \"SL\"\n        self.master_rows.append({\n""",
    ),
    (
        """            \"signal_time\": r.get(\"opened_at\", \"\"),\n""",
        """            \"signal_time\": signal_time,\n""",
    ),
    (
        """            \"result\": \"TP HIT\" if result == \"TP\" else \"SL HIT\",\n""",
        """            \"result\": \"TP HIT\" if result_short == \"TP\" else \"SL HIT\",\n""",
    ),
    (
        """            normalized = {**r, \"signal_time\": r.get(\"opened_at\", r.get(\"signal_time\", \"\"))}\n""",
        """            normalized = {**r, \"signal_time\": r.get(\"signal_time\") or r.get(\"opened_at\", \"\")}\n""",
    ),
]
for old, new in replacements:
    if old not in s:
        raise SystemExit(f'patch target missing: {old[:100]!r}')
    s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')
print('FORWARD MASTER COMPAT PATCH OK')
