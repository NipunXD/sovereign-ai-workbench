"""Application configuration.

Everything is environment-driven with a ``WORKBENCH_`` prefix, so the same image
runs on a laptop and inside an air-gapped rack with only the environment
changing. Paths that hold runtime state are resolved relative to the repo root
so a developer never has to think about the working directory.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/workbench/settings.py -> src/workbench -> src -> api -> apps -> repo root
REPO_ROOT = Path(__file__).resolve().parents[4]

Environment = Literal["development", "test", "production"]
ProviderName = Literal["lmstudio", "ollama", "vllm", "mock"]
VectorBackend = Literal["qdrant", "faiss"]
SandboxBackend = Literal["docker", "fake"]
ModelProfile = Literal["quality", "balanced", "fast", "demo"]


class Settings(BaseSettings):
    """Runtime configuration, read once and cached for the process lifetime."""

    model_config = SettingsConfigDict(
        env_prefix="WORKBENCH_",
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        # `model_` is our own domain vocabulary, not pydantic's namespace.
        protected_namespaces=(),
    )

    # ------------------------------------------------------------ environment
    env: Environment = "development"
    log_level: str = "INFO"
    app_name: str = "Sovereign AI Workbench"

    #: Freeze clock, UUIDs and sampling seeds. Golden-file tests and the eval
    #: harness depend on this; never enable it in production.
    deterministic: bool = False

    # -------------------------------------------------------------- database
    database_url: str = "postgresql+asyncpg://workbench:workbench@localhost:5433/workbench"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # ---------------------------------------------------------- vector store
    vector_backend: VectorBackend = "qdrant"
    qdrant_url: str = "http://localhost:6333"
    faiss_index_dir: Path = REPO_ROOT / "data" / "runtime" / "faiss"

    # ------------------------------------------------------------------ queue
    redis_url: str = "redis://localhost:6379/0"

    # ----------------------------------------------------------------- models
    #: Default backend. LM Studio's MLX builds are the fastest option on Apple
    #: Silicon; the air-gapped GPU server switches this to "vllm".
    provider: ProviderName = "lmstudio"
    model_profile: ModelProfile = "balanced"
    models_manifest: Path = REPO_ROOT / "config" / "models.yaml"
    router_config: Path = REPO_ROOT / "config" / "router.yaml"
    rbac_config: Path = REPO_ROOT / "config" / "rbac.yaml"
    tools_config: Path = REPO_ROOT / "config" / "tools.yaml"
    ingest_config: Path = REPO_ROOT / "config" / "ingest.yaml"
    rerank_enabled: bool = True

    # --------------------------------------------------------------- security
    jwt_secret: SecretStr = SecretStr("change-me-openssl-rand-hex-32")
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    seed_password: SecretStr = SecretStr("workbench123")
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # ---------------------------------------------------------------- sandbox
    sandbox_backend: SandboxBackend = "docker"
    sandbox_image: str = "workbench/sandbox:0.1.0"
    sandbox_allow_unsafe: bool = False
    sandbox_timeout_s: int = 60
    sandbox_memory_mb: int = 1024
    sandbox_cpus: float = 2.0
    sandbox_max_concurrent: int = 2

    # ------------------------------------------------------------- storage
    data_dir: Path = REPO_ROOT / "data" / "runtime"
    seed_dir: Path = REPO_ROOT / "data" / "seed"
    eval_results_dir: Path = REPO_ROOT / "evals" / "results"
    eval_datasets_dir: Path = REPO_ROOT / "evals" / "datasets"
    template_dir: Path = REPO_ROOT / "data" / "templates"

    # -------------------------------------------------------------------- api
    api_host: str = "0.0.0.0"  # noqa: S104 - bound inside the trusted network
    api_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    max_upload_mb: int = 200

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    # ------------------------------------------------------- derived paths
    @property
    def blob_dir(self) -> Path:
        """Content-addressed original uploads."""
        return self.data_dir / "blobs"

    @property
    def page_image_dir(self) -> Path:
        """Rendered page PNGs backing the document viewer."""
        return self.data_dir / "page_images"

    @property
    def artifact_dir(self) -> Path:
        """Generated Word/Excel/PowerPoint/PDF outputs."""
        return self.data_dir / "artifacts"

    @property
    def workspace_dir(self) -> Path:
        """Per-execution scratch directories mounted into the sandbox."""
        return self.data_dir / "workspaces"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def ensure_directories(self) -> None:
        """Create every runtime directory. Called once during app startup."""
        for path in (
            self.data_dir,
            self.blob_dir,
            self.page_image_dir,
            self.artifact_dir,
            self.workspace_dir,
            self.faiss_index_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
