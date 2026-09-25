from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    router_model: str = "qwen2.5:1.5b-instruct"
    worker_model: str = "qwen2.5-coder:3b"
    vision_model: str = "moondream"
    embed_model: str = "nomic-embed-text"
    ctx_router: int = 2048
    ctx_worker: int = 4096
    vram_total_mb: int = 4096
    vram_reserve_mb: int = 700
    pg_dsn: str = "postgresql://ultron:ultron@127.0.0.1:5432/ultron"
    ollama_host: str = "http://127.0.0.1:11434"
    graph_nodes: set[str] = Field(default_factory=lambda: {
        "supervisor", "dev_agent", "web_agent", "os_agent", "rag_agent"})

    session_token: str = ""
    music_folder: str = ""
    online_music_enabled: bool = True
    connectivity_probe_host: str = "1.1.1.1"
    connectivity_probe_port: int = 443
    dev_validate: bool = False
    debug_endpoints: bool = False
    vram_poll_hz: float = 2.0
    worker_timeout_s: float = 90.0
    router_timeout_s: float = 30.0
    worker_max_tokens: int = 512
    rag_candidates: int = 20        # per branch (vector, lexical)
    rag_grade_k: int = 4            # chunks shown to the grader
    rag_max_relevant: int = 3       # stop grading once this many are relevant
    rag_grade_chars: int = 900      # excerpt length shown to the grader
    rag_context_chars: int = 1400   # per-chunk length sent to the generator
    style_examples: int = 3         # dialogue examples injected into prompts (0 disables)
    embed_batch: int = 16
    # Postgres by default so a killed backend can resume a LangGraph run's state on restart.
    # Safe to default on: graph/checkpointer.py falls back to the in-memory saver with a visible
    # startup warning when PostgreSQL or the checkpoint tables are unavailable.
    checkpointer: str = "postgres"   # "memory" | "postgres"
    pid_file: str = "ultron.pid"     # relative to backend-ai, like .env
    web_allowlist: list[str] = ["flipkart.com", "amazon.in", "amazon.com", "google.com",
                                "youtube.com", "music.youtube.com", "wikipedia.org",
                                "duckduckgo.com"]
    confirm_graceful_close: bool = False
    approval_timeout_s: float = 60.0
    music_local_min_score: int = 85
    media_embed_min: float = 0.6
    mpv_path: str = "mpv"
    browser_step_limit: int = 8
    browser_timeout_s: float = 20.0
    web_timeout_s: float = 150.0
    ocr_max_chars: int = 3000
    stt_model: str = "base.en"
    stt_compute_type: str = "int8"
    stt_cpu_threads: int = 4
    stt_sample_rate: int = 16000
    stt_block_ms: int = 32
    stt_silence_ms: int = 700
    stt_max_utterance_s: float = 25.0
    stt_min_utterance_s: float = 0.3
    stt_rms_threshold: float = 0.02   # tune with scripts/list_audio_devices.py --meter
    stt_input_device: str | None = None   # NAME SUBSTRING (e.g. "Headset Microphone"), not an index
    stt_input_device_exclude: str = "Stereo Mix"   # never auto-pick a loopback device as a mic
    stt_download_root: str | None = None
    stt_speech_confirm_blocks: int = 3
    voice_enabled: bool = True
    tts_voice: str = "en_GB-alba-medium"
    tts_model_path: str = "models/piper/en_GB-alba-medium.onnx"
    tts_output_device: int | None = None
    tts_duck_pct: int = 25
    tts_max_chars: int = 900
    wake_word_enabled: bool = True
    wake_word_model: str = "hey_jarvis"
    wake_word_threshold: float = 0.4   # the log shows each score >= 0.15; tune from that
    wake_word_cooldown_s: float = 2.0
    wake_word_download_dir: str | None = None

    @field_validator("ollama_host")
    @classmethod
    def _add_scheme(cls, v: str) -> str:
        # The real env var OLLAMA_HOST ("127.0.0.1:11434", no scheme) is the Ollama
        # SERVER's bind address and overrides .env. httpx needs a scheme.
        v = v.strip().rstrip("/")
        return v if v.startswith(("http://", "https://")) else f"http://{v}"


settings = Settings()
if not settings.session_token:
    raise RuntimeError("SESSION_TOKEN is empty - set it in backend-ai/.env")

CPU_MODELS = {settings.router_model, settings.embed_model}
GPU_MODELS = {settings.worker_model, settings.vision_model}
VRAM_COST_MB = {settings.worker_model: 2200, settings.vision_model: 1800}


def is_cpu(model: str) -> bool:
    return model in CPU_MODELS
