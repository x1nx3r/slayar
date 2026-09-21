"""Laya Router — Jev-compatible System One API.

Jev shape (docs.typesafe.ai/api): POST {state, model, questions}
-> {model, answers, usage}. Questions are noul / choice / score.
This server speaks that shape on both /predict and /v1/systemone,
plus a laya-only `routing` block explaining the checkpoint choice.
"""
import os
from typing import Annotated, Any, Dict, List, Literal, Optional, Union
from contextlib import asynccontextmanager

os.environ.setdefault("USE_TF", "0")

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from router import Router, MODEL_DIRS, route

_bearer = HTTPBearer(auto_error=False)


def require_key(cred: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)):
    """Bearer auth, active only when LAYA_API_KEY is set. Empty = open dev mode."""
    expected = os.getenv("LAYA_API_KEY", "")
    if not expected:
        return
    if cred is None or cred.scheme.lower() != "bearer" or cred.credentials != expected:
        raise HTTPException(401, "Missing or invalid API key. Send Authorization: Bearer <LAYA_API_KEY>.")

# ---------------------------------------------------------------- request
# Jev: instructions / criteria entries may be string, object, array (or null).
Instruction = Union[str, Dict[str, Any], List[Any]]
MaybeInstruction = Union[str, Dict[str, Any], List[Any], None]


class NoulCriteria(BaseModel):
    true: Optional[Instruction] = Field(default=None, description="What yes (near 1) means")
    false: Optional[Instruction] = Field(default=None, description="What no (near 0) means")


class NoulQuestion(BaseModel):
    type: Literal["noul"]
    instructions: Instruction = Field(description="Yes/no question, or statement to judge")
    criteria: Optional[NoulCriteria] = None


class ChoiceQuestion(BaseModel):
    type: Literal["choice"]
    instructions: Instruction = Field(description="What to decide")
    criteria: Dict[str, MaybeInstruction] = Field(
        description="Option -> rubric. Null when the name is self-explanatory. Max 255 options."
    )


class ScoreQuestion(BaseModel):
    type: Literal["score"]
    instructions: Instruction = Field(description="What to rate")
    criteria: List[Instruction] = Field(description="Ordered levels, low to high. 2-10 levels.")


Question = Annotated[Union[NoulQuestion, ChoiceQuestion, ScoreQuestion], Field(discriminator="type")]

# jev-latest / jev-preview / jev-1.13.0 are accepted and served by the English
# checkpoint (closest single match); native names pick checkpoints directly.
MODEL_ALIASES = {"jev-latest": "english", "jev-preview": "english", "jev-1.13.0": "english"}


def resolve_model(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    if name in MODEL_DIRS:
        return name
    if name in MODEL_ALIASES:
        return MODEL_ALIASES[name]
    raise HTTPException(422, f"unknown model {name!r}, pick from {sorted(MODEL_DIRS) + sorted(MODEL_ALIASES)}")


class PredictRequest(BaseModel):
    state: Union[str, Dict[str, Any], List[Any]] = Field(
        description="Content to evaluate: plain text or structured state (object/array)"
    )
    questions: Dict[str, Question] = Field(
        min_length=1, description="Map of question-id -> typed question. Answers come back under the same ids."
    )
    model: Optional[str] = Field(
        default=None,
        description="english | multilingual | typed-decisions (or jev-latest alias -> english). Omit to auto-route.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "state": "Help! My payouts have been failing for 3 days.",
                    "model": "english",
                    "questions": {
                        "department": {
                            "type": "choice",
                            "instructions": "Which team should handle this?",
                            "criteria": {
                                "billing": "Payments, invoicing, refunds",
                                "technical": "Bugs, outages, integrations",
                            },
                        },
                        "is_urgent": {"type": "noul", "instructions": "Does this convey urgency?"},
                        "frustration": {
                            "type": "score",
                            "instructions": "How frustrated is the customer?",
                            "criteria": ["Calm", "Frustrated", "Very angry"],
                        },
                    },
                }
            ]
        }
    }


# ---------------------------------------------------------------- response
class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float = Field(description="P(yes), 0=no to 1=yes")


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str = Field(description="Highest-probability option")
    probabilities: Dict[str, float] = Field(description="Every option -> probability, sums to 1")
    confidence: float


class ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: float = Field(description="Probability-weighted position across levels")
    legend: Dict[str, Any] = Field(description="Level index (string) -> description")
    probabilities: Dict[str, float]
    confidence: float


Answer = Annotated[Union[NoulAnswer, ChoiceAnswer, ScoreAnswer], Field(discriminator="type")]


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int = 0


class Routing(BaseModel):
    model: str
    repo: str
    reason: str


class PredictResponse(BaseModel):
    model: str = Field(description="Checkpoint id that answered (laya extension: see routing for detail)")
    answers: Dict[str, Answer]
    usage: Usage
    routing: Optional[Routing] = Field(default=None, description="Laya-only: which checkpoint served this and why")


router_obj: Optional[Router] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global router_obj
    max_loaded = int(os.getenv("MAX_LOADED", "1"))
    preload = os.getenv("PRELOAD", "").lower()
    router_obj = Router(device=os.getenv("DEVICE"), max_loaded=max_loaded)
    if preload in ("1", "true", "all"):
        router_obj.preload()
    elif preload:
        router_obj.preload([m.strip() for m in preload.split(",") if m.strip() in MODEL_DIRS])
    if not os.getenv("LAYA_API_KEY"):
        print("WARNING: LAYA_API_KEY unset — API is open (set it to require Bearer auth)")
    yield
    router_obj.unload()


app = FastAPI(
    title="Laya Router — Jev-compatible System One API",
    version="1.0.0",
    description=(
        "State + typed questions (noul/choice/score) -> calibrated answers in one forward pass. "
        "Jev-compatible shape: POST /v1/systemone takes {state, model, questions} "
        "and returns {model, answers, usage}. `routing` is a laya-only extra. "
        "Interactive docs: /docs (Swagger UI), /redoc, /openapi.json."
    ),
    lifespan=lifespan,
)

# Browser demo calls the API cross-origin. Restrict in prod via ALLOWED_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _predict(req: PredictRequest) -> dict:
    name = resolve_model(req.model)
    questions = {k: v.model_dump(exclude_none=True) for k, v in req.questions.items()}
    # Jev limits: max 255 options per choice, 2-10 levels per score
    for qid, q in questions.items():
        if q["type"] == "choice" and len(q.get("criteria") or {}) > 255:
            raise HTTPException(422, f"question {qid!r}: max 255 options per choice")
        if q["type"] == "score" and not (2 <= len(q.get("criteria") or []) <= 10):
            raise HTTPException(422, f"question {qid!r}: score needs 2-10 levels")
    try:
        return router_obj.predict(req.state, questions, model=name)
    except ValueError as e:  # e.g. options don't fit head_max_len
        raise HTTPException(422, str(e))
    except RuntimeError as e:  # e.g. weights not mounted
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@app.get("/health", tags=["ops"])
def health():
    return {"ok": True, "loaded": list(router_obj._agents.keys()) if router_obj else []}


@app.get("/models", tags=["ops"], dependencies=[Depends(require_key)])
def models():
    return {"models": sorted(MODEL_DIRS.keys()), "aliases": MODEL_ALIASES, "loaded": list(router_obj._agents.keys())}


@app.get("/v1/models", tags=["jev-compat"], dependencies=[Depends(require_key)])
def jev_models():
    """Jev-compatible model listing (GET /v1/models)."""
    return {
        "models": [
            {"name": "english", "description": "ModernBERT-large, English routing/triage/guardrails"},
            {"name": "multilingual", "description": "mmBERT-base, 100+ languages"},
            {"name": "typed-decisions", "description": "ModernBERT-large fine-tuned on typed-decision workflows"},
            {"name": "jev-latest", "description": "Alias -> english"},
        ]
    }


@app.post("/predict", response_model=PredictResponse, tags=["predict"], dependencies=[Depends(require_key)])
def predict(req: PredictRequest):
    """Native endpoint. Omit `model` to auto-route by script."""
    return _predict(req)


@app.post("/v1/systemone", response_model=PredictResponse, tags=["jev-compat"], dependencies=[Depends(require_key)])
def systemone(req: PredictRequest):
    """Jev-compatible endpoint. Accepts {state, model, questions}; jev-* model names alias to english."""
    return _predict(req)


@app.post("/route", tags=["predict"], dependencies=[Depends(require_key)])
def dry_route(req: PredictRequest):
    """Inspect the routing decision without running any forward pass."""
    name, reason = route(req.state, resolve_model(req.model))
    return {"model": name, "reason": reason}
