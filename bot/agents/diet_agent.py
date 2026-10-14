from agno.agent import Agent
from agno.models.google import Gemini

from agents._shared import GEMINI_MODEL_PRIORITY, agno_db, run_with_model_chain
from config import GEMINI_API_KEY
from models.schemas import DietTargets

INSTRUCTIONS = """Ricevi il testo estratto da un piano alimentare scritto da un nutrizionista.
Stima i target medi giornalieri di kcal, proteine, carboidrati e grassi che il piano implica
(se il piano varia per giorno della settimana, calcola una media giornaliera ragionevole).
Scrivi in summary una frase che spiega brevemente come sei arrivato alla stima.

IMPORTANTE: rispondi SEMPRE in italiano, indipendentemente dalla lingua del testo ricevuto."""


def _build_agent(model_id: str) -> Agent:
    return Agent(
        model=Gemini(id=model_id, api_key=GEMINI_API_KEY),
        db=agno_db,
        output_schema=DietTargets,
        instructions=INSTRUCTIONS,
    )


_diet_agents = [_build_agent(model_id) for model_id in GEMINI_MODEL_PRIORITY]


def run_diet_agent(*args, **kwargs):
    return run_with_model_chain(_diet_agents, *args, **kwargs)
