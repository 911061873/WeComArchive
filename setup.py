from pathlib import Path

from setuptools import setup  # type: ignore
from setuptools.command.install_lib import install_lib  # type: ignore


class InstallLibWithMigrations(install_lib):
    """Restore Alembic source scripts after Nuitka removes Python sources."""

    def run(self):
        super().run()
        source = Path(__file__).parent / "src" / "wecomarchive" / "migrations"
        destination = Path(self.install_dir) / "wecomarchive" / "migrations"
        for script in sorted(source.rglob("*.py")):
            target = destination / script.relative_to(source)
            self.mkpath(str(target.parent))
            self.copy_file(str(script), str(target))


setup(cmdclass={"install_lib": InstallLibWithMigrations})
