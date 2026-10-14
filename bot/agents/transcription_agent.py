from agno.agent import Agent
from agno.media import Audio
from agno.models.google import Gemini

from agents._shared import GEMINI_MODEL_PRIORITY, run_with_model_chain
from config import GEMINI_API_KEY

INSTRUCTIONS = """Trascrivi fedelmente in italiano l'audio fornito (e' un messaggio vocale
Telegram in cui l'utente descrive cosa ha mangiato). Restituisci SOLO il testo trascritto,
senza commenti, spiegazioni, virgolette o formattazione aggiuntiva. Se l'audio contiene
un'altra lingua, traduci il contenuto in italiano."""


def _build_agent(model_id: str) -> Agent:
    # Nessun output_schema: qui vogliamo testo libero, non un JSON strutturato.
    return Agent(model=Gemini(id=model_id, api_key=GEMINI_API_KEY), instructions=INSTRUCTIONS)


_transcription_agents = [_build_agent(model_id) for model_id in GEMINI_MODEL_PRIORITY]


def _looks_like_real_transcript(content) -> bool:
    if not isinstance(content, str):
        return False
    text = content.strip()
    # Gemini a volte restituisce l'errore come stringa JSON invece di sollevare un'eccezione.
    return bool(text) and not text.startswith("{")


def transcribe_audio(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """Trascrive un vocale in testo con Gemini (nessuna dipendenza da ffmpeg/Whisper:
    Gemini legge direttamente l'ogg/opus delle note vocali Telegram).

    Ritorna il testo trascritto, oppure "Non sono riuscito a capire l'audio, errore: <err>"
    in caso di fallimento (non solleva mai eccezioni al chiamante).
    """
    try:
        response = run_with_model_chain(
            _transcription_agents,
            "Trascrivi questo audio.",
            audio=[Audio(content=audio_bytes, mime_type=mime_type)],
            is_valid=_looks_like_real_transcript,
        )
        return response.content.strip()
    except Exception as e:
        return f"Non sono riuscito a capire l'audio, errore: {e}"
