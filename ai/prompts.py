import json

def holding_prompt(holding: dict, analysis_config) -> str:
    value = holding.get("value", {})
    cost = holding.get("cost", {})
    current_ret = holding.get("current_return", {})
    xirr_raw = current_ret.get("xirr")
    xirr_pct = round(xirr_raw * 100, 2) if isinstance(xirr_raw, (int, float)) else None
    percent_raw = current_ret.get("percent")
    percent_pct = round(percent_raw, 2) if isinstance(percent_raw, (int, float)) else None

    symbol = holding.get("symbol", "the stock")
    units = value.get("units", 0)

    base_facts = f"""
Instrument: {symbol}
Units held: {units}
Avg buy price: ₹{cost.get("per_unit")}
Total invested: ₹{cost.get("total")}
Current value: ₹{value.get("current")}
Gain/Loss: ₹{current_ret.get("absolute")} ({percent_pct}%)
XIRR: {xirr_pct}%
"""
    questions = analysis_config.questions
    question_block = "\n".join(f'''    {{"q": {json.dumps(q["question"])},"a": {json.dumps(q["instruction"])}}}'''
                               for q in questions )

    rules_block = "\n".join(f"- {rule}"
        for rule in analysis_config.rules )

    return f"""You are {analysis_config.role}.

Your goal is {analysis_config.goal}.

{base_facts}

Respond ONLY with valid JSON — nothing else — exactly in this structure:

{{
  "summary": "Direct 3–5 sentence opinion on whether this holding still deserves to stay in the portfolio at current size, including rough valuation view and your recommended action stance right now.",
  "insights": [
{question_block}
  ]
}}

Rules you MUST follow:
{rules_block}
"""