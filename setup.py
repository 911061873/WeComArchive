import ast
from pathlib import Path

from setuptools import setup  # type: ignore
from setuptools.command.build_py import build_py  # type: ignore
from setuptools.command.install_lib import install_lib  # type: ignore


class BuildPyWithStubs(build_py):
    """只在构建目录中生成类型存根，源码目录保留内联类型。"""

    def run(self):
        from mypy.stubgen import main

        super().run()
        source = Path(__file__).parent / "src" / "wecomarchive"
        destination = Path(self.build_lib) / "wecomarchive"
        # 清除增量构建中已经被删除或改名的模块存根。
        for stub in destination.rglob("*.pyi"):
            stub.unlink()
        modules = sorted(path for path in source.rglob("*.py") if "migrations" not in path.parts)
        main(["--no-import", "--include-docstrings", *map(str, modules), "-o", self.build_lib])
        # stubgen 会丢弃 Field 默认值及 model_config；模型声明直接取自源码，
        # 仅将方法实现替换为省略号，保留默认值、别名及验证器装饰器。
        for name in ("config", "models"):
            tree = ast.parse((source / f"{name}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    docstring = ast.get_docstring(node, clean=False)
                    node.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
                    if docstring is not None:
                        node.body.insert(0, ast.Expr(value=ast.Constant(value=docstring)))
            (destination / f"{name}.pyi").write_text(ast.unparse(tree) + "\n", encoding="utf-8")


class InstallLibWithMigrations(install_lib):
    """在 Nuitka 移除 Python 源码后恢复 Alembic 迁移脚本。"""

    def run(self):
        super().run()
        source = Path(__file__).parent / "src" / "wecomarchive" / "migrations"
        destination = Path(self.install_dir) / "wecomarchive" / "migrations"
        for script in sorted(source.rglob("*.py")):
            target = destination / script.relative_to(source)
            self.mkpath(str(target.parent))
            self.copy_file(str(script), str(target))


setup(cmdclass={"build_py": BuildPyWithStubs, "install_lib": InstallLibWithMigrations})
