# The lockfile holds every dependency at its newest, so every other row passes whatever a floor in
# pyproject.toml says. This one installs the package fresh with each direct dependency at the lowest
# version its floor allows, on the lowest Python, and runs the command line: the version, a project
# written and judged without a browser, the whole schema, and a refused flag, which reaches the
# parser's own refusal through the classes the command line subclasses and catches.
#
# Run as `sh -euc` from the repository root, with the lowest supported Python as $1.
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
uv venv --quiet --python "$1" "$scratch/venv"
uv pip install --python "$scratch/venv" --resolution lowest-direct .
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
