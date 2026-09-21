import os
import sys
import json
import unicodedata
from collections import OrderedDict
from typing import Any, Dict, Optional

# transformers probes TF at import and can deadlock model build when TF is installed
os.environ.setdefault("USE_TF", "0")

# Model root: local ./laya for dev, /models/laya when mounted from a PV.
# Overridden with MODEL_BASE env var. The dir must hold the full hf repo
# (rl_agent_api.py / rl_common.py ship with it, so no code is baked in).
BASE = os.path.dirname(os.path.abspath(__file__))
MODEL_BASE = os.getenv("MODEL_BASE", os.path.join(BASE, "laya"))
for _p in (MODEL_BASE, os.path.join(BASE, "laya")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
MODEL_DIRS = {
    "english": MODEL_BASE,
    "multilingual": os.path.join(MODEL_BASE, "multilingual"),
    "typed-decisions": os.path.join(MODEL_BASE, "typed-decisions"),
}

# Imported lazily so the API boots (and /health answers) even with no
# weights mounted; first /predict then fails with a clear 503.
RLAgent = None


def _rl_agent_class():
    global RLAgent
    if RLAgent is None:
        from rl_agent_api import RLAgent as _C
        RLAgent = _C
    return RLAgent


def serialize_state(state: Any) -> str:
    if isinstance(state, str):
        return state
    try:
        return json.dumps(state, ensure_ascii=False)
    except Exception:
        return str(state)


def script_split(text: str):
    latin = non_latin = 0
    for ch in text:
        if not ch.isalpha():
            continue
        try:
            name = unicodedata.name(ch)
        except ValueError:
            non_latin += 1
            continue
        if "LATIN" in name:
            latin += 1
        else:
            non_latin += 1
    return latin, non_latin


def route(state: Any, explicit: Optional[str] = None):
    """Pure-python <1ms routing. Returns (model_name, reason)."""
    if explicit and explicit in MODEL_DIRS:
        return explicit, "explicit override"
    text = serialize_state(state)
    latin, non_latin = script_split(text)
    total = latin + non_latin
    if total > 0 and non_latin / total > 0.3:
        return "multilingual", f"non-Latin script ({non_latin}/{total} letters); English checkpoint cannot read it"
    return "english", "Latin script, default English checkpoint"


class Router:
    def __init__(self, device=None, max_loaded=1):
        self.device = device
        self.max_loaded = max(1, max_loaded)
        self._agents: "OrderedDict[str, RLAgent]" = OrderedDict()

    def _get(self, name: str) -> "RLAgent":
        if name in self._agents:
            self._agents.move_to_end(name)
            return self._agents[name]
        weights = os.path.join(MODEL_DIRS[name], "model.safetensors")
        if not os.path.exists(weights):
            raise RuntimeError(f"weights not found: {weights} (mount the model PV at MODEL_BASE={MODEL_BASE})")
        agent = _rl_agent_class()(MODEL_DIRS[name], device=self.device)
        self._agents[name] = agent
        while len(self._agents) > self.max_loaded:
            _, old = self._agents.popitem(last=False)
            del old
        return agent

    def preload(self, names=None):
        for n in names or list(MODEL_DIRS):
            self._get(n)

    def unload(self):
        self._agents.clear()

    def predict(self, state: Any, questions: Dict[str, Any], model: Optional[str] = None):
        name, reason = route(state, model)
        agent = self._get(name)
        out = agent.system_one(state, questions)
        out["routing"] = {"model": name, "repo": f"convaiinnovations/laya{f'/{name}' if name != 'english' else ''}", "reason": reason}
        return out
