"""Generate notebooks/00_install_engine.py with the engine source embedded.

Why: the notebooks can be imported on their own, with no folder upload.
Run after any change to engine/*.py:   python3 build_notebooks.py
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["__init__.py", "models.py", "backend.py", "llm.py", "serialize.py", "workload.py", "measure.py", "profiles.py", "enforce.py", "negotiate.py"]

parts = ["# Databricks notebook source",
         "# MAGIC %md\n# MAGIC # Install the Contract Court engine\n"
         "# MAGIC Writes the engine's Python files to disk so other notebooks can `import engine`.\n"
         "# MAGIC Other notebooks start with `%run ./00_install_engine`. Nothing to configure.",
         "# COMMAND ----------",
         "import os, sys\nENGINE_ROOT = '/tmp/contract_court'\nos.makedirs(ENGINE_ROOT + '/engine', exist_ok=True)"]

for f in FILES:
    src = open(os.path.join(HERE, "engine", f)).read()
    parts.append("# COMMAND ----------")
    parts.append(f"open(ENGINE_ROOT + '/engine/{f}', 'w').write({src!r})")

parts += ["# COMMAND ----------",
          "if ENGINE_ROOT not in sys.path:\n    sys.path.insert(0, ENGINE_ROOT)\n"
          "import importlib, engine\nimportlib.reload(engine)\nprint('engine ready:', engine.__file__)"]

out = os.path.join(HERE, "notebooks", "00_install_engine.py")
open(out, "w").write("\n\n".join(parts) + "\n")
print("wrote", out)
