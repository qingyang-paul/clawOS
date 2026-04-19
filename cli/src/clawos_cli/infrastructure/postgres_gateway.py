from abc import ABC, abstractmethod
from pathlib import Path


class PostgresGateway(ABC):
    @abstractmethod
    def backup(self, project_root: Path, output_file: Path) -> None:
        raise NotImplementedError

    @abstractmethod
    def restore(self, project_root: Path, input_file: Path) -> None:
        raise NotImplementedError


class SkeletonPostgresGateway(PostgresGateway):
    def backup(self, project_root: Path, output_file: Path) -> None:
        output_file.write_text(
            (
                "-- clawOS backup skeleton\n"
                f"-- project_root={project_root}\n"
                "-- replace this gateway with pg_dump integration in production\n"
            ),
            encoding="utf-8",
        )

    def restore(self, project_root: Path, input_file: Path) -> None:
        _ = project_root
        _ = input_file
        # v1 skeleton: restore pipeline is wired, execution implementation will be added later.

