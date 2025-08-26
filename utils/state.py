import json, os
from typing import Any, Dict

STATE_FILE = os.getenv("STATE_FILE", "state.json")

def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {"positions": {}}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"positions": {}}

def save_state(state: Dict[str, Any]):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)
