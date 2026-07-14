from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    state_dir: Path
    host: str = "127.0.0.1"
    port: int = 8000
    demo_profile: bool = True
    control_token: str = "lethe-control-demo"
    alice_token: str = "lethe-alice-demo"
    bob_token: str = "lethe-bob-demo"
    unsafe_token: str = "lethe-unsafe-demo"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            state_dir=Path(os.getenv("LETHE_STATE_DIR", ".lethe")).resolve(),
            host=os.getenv("LETHE_HOST", "127.0.0.1"),
            port=int(os.getenv("LETHE_PORT", "8000")),
            demo_profile=os.getenv("LETHE_DEMO_PROFILE", "1") == "1",
            control_token=os.getenv("LETHE_CONTROL_TOKEN", "lethe-control-demo"),
            alice_token=os.getenv("LETHE_ALICE_TOKEN", "lethe-alice-demo"),
            bob_token=os.getenv("LETHE_BOB_TOKEN", "lethe-bob-demo"),
            unsafe_token=os.getenv("LETHE_UNSAFE_TOKEN", "lethe-unsafe-demo"),
        )

    @property
    def database_path(self) -> Path:
        return self.state_dir / "state.db"

    @property
    def objects_dir(self) -> Path:
        return self.state_dir / "objects"

    @property
    def chroma_dir(self) -> Path:
        return self.state_dir / "chroma"

    @property
    def reports_dir(self) -> Path:
        return self.state_dir / "reports"

    def ensure_directories(self) -> None:
        for path in (self.state_dir, self.objects_dir, self.chroma_dir, self.reports_dir):
            path.mkdir(parents=True, exist_ok=True)
