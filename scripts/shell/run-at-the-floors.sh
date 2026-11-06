# The lockfile holds every dependency at its newest, so every other row passes whatever a floor in
# pyproject.toml says. This one installs the package fresh with each direct dependency at the lowest
# version its floor allows, on the lowest Python, and runs the command line: the version, a project
# written and judged without a browser, the whole schema, and a refused flag, which reaches the
# parser's own refusal through the classes the command line subclasses and catches.
#
# Run as `sh -euc` from the repository root in the default Pixi environment, whose Python is the
# floor, with the floor's version as $1 so a run on any other Python stops before it installs.
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
python -c 'import sys; found = "%d.%d" % sys.version_info[:2]; sys.exit(None if found == sys.argv[1] else f"this runs on Python {found}, and the floor is {sys.argv[1]}")' "$1"
# Each direct dependency pinned to its floor as a pip constraint, which holds the direct
# dependencies there and leaves every other package free, as a lowest-direct resolution does.
python - "$scratch/floors.txt" <<'PY'
import sys
import tomllib

from packaging.requirements import Requirement

with open("pyproject.toml", "rb") as manifest:
    dependencies = tomllib.load(manifest)["project"]["dependencies"]
with open(sys.argv[1], "w", encoding="utf-8") as constraints:
    for line in dependencies:
        requirement = Requirement(line)
        floors = [spec.version for spec in requirement.specifier if spec.operator in (">=", "==", "~=")]
        if len(floors) != 1:
            sys.exit(f"{line} names no one floor, so this row cannot install it at its floor")
        constraints.write(f"{requirement.name}=={floors[0]}\n")
PY
python -m venv "$scratch/venv"
"$scratch/venv/bin/python" -m pip install --quiet --disable-pip-version-check --constraint "$scratch/floors.txt" .
decktalk="$scratch/venv/bin/decktalk"
"$decktalk" --version
"$decktalk" init "$scratch/project" --no-input
"$decktalk" check --no-pages -p "$scratch/project"
"$decktalk" schema >/dev/null
set +e
"$decktalk" check --no-such-flag -p "$scratch/project"
code=$?
set -e
if [ "$code" -ne 2 ]; then
  echo "expected exit 2 for a flag the command does not take, got $code"
  exit 1
fi
