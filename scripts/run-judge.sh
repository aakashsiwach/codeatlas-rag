#!/usr/bin/env bash
# Judged answer-quality runs on the frozen `quality` setup over the held-out TEST split. This BILLS the LLM.
#
#   1. refuse unless every reference answer is reviewed (no calls)
#   2. codeatlas doctor --llm                        (one 1-token call)
#   3. one answer + one judge call, and the estimated cost of N passes against --budget
#   4. ask for confirmation (skip with --yes)
#   5. N judged passes; stop as soon as the actual spend exceeds --budget
#   6. print the README answer-quality table
#
# Usage: scripts/run-judge.sh [--passes 3] [--budget 5] [--eval-set eval/petclinic-test.yaml]
#                             [--setup quality] [--out results] [--yes]
# Set CODEATLAS to the command to run (default: codeatlas on PATH).
set -euo pipefail

passes=3
budget=5
eval_set=eval/petclinic-test.yaml
setup=quality
out=results
yes=0

while [ $# -gt 0 ]; do
  case "$1" in
    --passes) passes=$2; shift 2 ;;
    --budget) budget=$2; shift 2 ;;
    --eval-set) eval_set=$2; shift 2 ;;
    --setup) setup=$2; shift 2 ;;
    --out) out=$2; shift 2 ;;
    --yes) yes=1; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

codeatlas=${CODEATLAS:-codeatlas}

step() { printf '\n== %s ==\n' "$1"; }

step "1/6 reference answers reviewed"
$codeatlas check-reviewed "$eval_set"

step "2/6 provider check (one 1-token call)"
$codeatlas doctor --llm --skip-models

step "3/6 single judge check and cost estimate"
$codeatlas judge-check "$eval_set" --setup "$setup" --passes "$passes" --budget "$budget" --final

if [ "$yes" != 1 ]; then
  read -r -p "Run $passes judged pass(es) of $eval_set with the $setup setup (budget \$$budget)? [y/N] " answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) echo "aborted; nothing was run"; exit 1 ;;
  esac
fi

run_dir="$out/judge-$(date +%Y%m%d-%H%M%S)"
reports=()
for i in $(seq 1 "$passes"); do
  step "4/6 judged pass $i of $passes"
  $codeatlas eval "$eval_set" --setup "$setup" --judge --final --out "$run_dir/pass-$i"
  reports+=("$run_dir/pass-$i"/eval-*.json)

  step "5/6 spend so far"
  spend=$($codeatlas judge-summary "${reports[@]}" --budget "$budget" 2>&1) || {
    status=$?
    printf '%s\n' "$spend" | tail -n 2
    echo "stopping after pass $i of $passes" >&2
    exit "$status"
  }
  printf '%s\n' "$spend" | tail -n 1
done

step "6/6 answer quality (paste into the README; reports in $run_dir)"
$codeatlas judge-summary "${reports[@]}"
