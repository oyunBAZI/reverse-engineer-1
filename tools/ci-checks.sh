#!/usr/bin/env bash
# Validation checks for the reverse-engineering pipeline.
#
#   bash tools/ci-checks.sh
#
# Run by CI (.github/workflows/ci.yml) and usable locally. Everything here is
# fast and needs no APK, so it is safe to run on every push.
#
# The recovered source under source-app/ IS committed, so these checks cover
# both halves: the pipeline itself (does it parse, compile, refuse bad input)
# and the committed payload (is anything oversized, and does any ignore rule
# accidentally swallow a tracked file).
#
# Exit status is 0 only if every check passes; `set -euo pipefail` is kept so no
# failure is masked by a pipeline filter.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

pass=0
fail=0

ok()   { pass=$((pass + 1)); printf '  ok    %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  FAIL  %s\n' "$1" >&2; }

echo "== shell syntax"
for s in tools/*.sh; do
  if bash -n "$s"; then ok "$s"; else bad "$s (bash -n)"; fi
done

echo "== python syntax"
for p in tools/*.py; do
  if python3 -m py_compile "$p"; then ok "$p"; else bad "$p (py_compile)"; fi
done
rm -rf tools/__pycache__ 2>/dev/null || true

echo "== lua decompiler driver compiles"
scratchcls="$(mktemp -d)"
if javac -cp tools/unluac-batch/unluac.jar \
     -d "$scratchcls" \
     tools/unluac-batch/UnluacBatch.java tools/unluac-batch/DisasmOne.java 2>/dev/null; then
  ok "UnluacBatch.java + DisasmOne.java"
else
  bad "UnluacBatch.java + DisasmOne.java (javac)"
fi
rm -rf "$scratchcls"

# The patch is what makes the three hard chunks decompile at all, so it has to
# keep building from the vendored source rather than only the prebuilt classes.
if sh tools/unluac-batch/build-patch.sh >/dev/null 2>&1; then
  ok "unluac patch builds"
else
  bad "unluac patch failed to build (javac against unluac.jar)"
fi

# It also has to actually be on the classpath ahead of the jar, or it is inert.
# A reverted order compiles fine and silently decompiles nothing extra, so the
# only way to see it is to check the script text.
if grep -q 'classes:.*unluac.jar' tools/decompile.sh; then
  ok "patch classes precede unluac.jar on the classpath"
else
  bad "decompile.sh puts unluac.jar before the patch classes - the fix is inert"
fi

# The argument guards run before any real work, so they are safe to exercise in
# a scratch copy that has neither the APK nor the extracted output. Copying
# rather than running in place keeps this check from ever kicking off a full
# multi-gigabyte decompile on a developer machine.
echo "== argument guards (in a scratch copy)"

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/tools"
cp tools/*.sh "$scratch/tools/"
cp tools/re-env.sh "$scratch/tools/"

expect_exit() {
  # expect_exit <want> <label> <cmd...>
  local want="$1" label="$2"; shift 2
  local got=0
  set +e
  ( cd "$scratch" && "$@" ) >"$scratch/out.txt" 2>&1
  got=$?
  set -e
  if [ "$got" -eq "$want" ]; then
    ok "$label (exit $got)"
  else
    bad "$label (exit $got, wanted $want)"
    sed 's/^/        /' "$scratch/out.txt" | head -5 >&2
  fi
}

expect_exit 2 "decompile.sh without an APK refuses" \
  bash tools/decompile.sh
expect_exit 2 "decompile-csharp.sh without extracted output refuses" \
  bash tools/decompile-csharp.sh

echo "== docs reference real files"

# Only tools/ paths are checked: everything else the READMEs mention is either
# the recovered payload (absent from a pipeline-only checkout) or gitignored
# output, so a missing path here is only meaningful for the toolchain itself.
docs="$(grep -oh 'tools/[A-Za-z0-9_./-]*' README.md unity-project/README.md \
        | sed 's/[.,:)`]*$//' | sort -u || true)"
if [ -z "$docs" ]; then
  bad "no tools/ paths found in the docs (grep is broken?)"
else
  for p in $docs; do
    case "$p" in *'*'*) continue ;; esac
    if [ -e "$p" ]; then ok "$p"; else bad "$p named in docs but missing"; fi
  done
fi

echo "== committed payload is pushable and unswallowed"

# GitHub hard-rejects any blob over 100 MB, so one oversized file makes the
# whole push fail. Check the tracked tree, not the working tree: the point is
# what a clone would actually have to download.
limit=$((100 * 1024 * 1024))
tracked="$(git ls-files -z | tr -cd '\0' | wc -c)"
if [ "$tracked" -eq 0 ]; then
  bad "git ls-files returned nothing (is this a git checkout?)"
else
  biggest="$(git ls-files -z \
             | xargs -0 -r stat -c '%s %n' 2>/dev/null \
             | sort -rn | sed -n '1p' || true)"
  biggest_bytes="${biggest%% *}"
  biggest_bytes="${biggest_bytes:-0}"
  if [ "$biggest_bytes" -gt "$limit" ]; then
    bad "tracked file over 100 MB: $biggest"
  else
    ok "no tracked file over 100 MB (largest ${biggest_bytes} B of $tracked files)"
  fi
fi

# A tracked file whose bytes git would rewrite on `git add` is a landmine: the
# blob in the repository is the correct one today, but the next person who adds
# it commits something else. `.gitattributes` is what decides this, and a
# mis-scoped rule is silent - an earlier version marked the vendored unluac.jar
# as text via `/tools/**`, which would have stripped its CR bytes.
#
# Scoped to generated files, because this script is also run by hand: a
# developer part-way through editing ci-checks.sh should not see it go red.
# --really-refresh defeats git's stat cache so this compares content.
if [ -d source-app ]; then
  git update-index --really-refresh -q >/dev/null 2>&1 || :
  drifted="$(git diff-files --name-only -- source-app tools/unluac-batch/unluac.jar | head -5)"
  if [ -z "$drifted" ]; then
    ok "payload matches its stored blobs byte for byte"
  else
    bad "generated file(s) differ from their stored blob - check .gitattributes"
    printf '%s\n' "$drifted" | sed 's/^/        /' >&2
  fi
fi

# A tracked file that also matches an ignore rule is always a mistake: git stops
# tracking it on the next `git add`, so it silently vanishes from the repository.
# This is not hypothetical - an unanchored `input/` rule hid 269 recovered files
# under androidx/compose/**/input/. --no-index is what makes check-ignore look at
# tracked paths at all; without it, tracked files are skipped by design.
swallowed="$(git ls-files -z | git check-ignore --stdin -z --no-index 2>/dev/null \
             | tr -cd '\0' | wc -c || true)"
swallowed="${swallowed:-0}"
if [ "$swallowed" -eq 0 ]; then
  ok "no tracked file matches an ignore rule"
else
  bad "$swallowed tracked file(s) are also ignored - anchor the rule or untrack the path"
  git ls-files -z | git check-ignore --stdin -z --no-index 2>/dev/null \
    | tr '\0' '\n' | head -5 | sed 's/^/        /' >&2
fi

# Rules that reach the recovered payload are just as dangerous as rules that miss
# it: normalising 19,572 Lua modules or 7,390 Unity JSON dumps would corrupt the
# very files this repository exists to preserve. Asked of git rather than of a
# hardcoded extension list, so this catches any rule - present or future - that
# marks payload content as text.
if [ -d source-app ]; then
  # check-attr emits path/attr/value NUL triples; `paste` folds them into rows so
  # awk can pick out the ones where `text` is set.
  leaks="$(git ls-files -z source-app | git check-attr --stdin -z text 2>/dev/null \
            | tr '\0' '\n' | paste -d'|' - - - \
            | awk -F'|' '$2 == "text" && $3 == "set"' | head -3 || true)"
  leaked_n="$(git ls-files -z source-app | git check-attr --stdin -z text 2>/dev/null \
              | tr '\0' '\n' | paste -d'|' - - - \
              | awk -F'|' '$2 == "text" && $3 == "set"' | wc -l | tr -d ' ')"
  if [ "${leaked_n:-0}" -eq 0 ]; then
    ok "no payload file is marked text by .gitattributes"
  else
    bad "$leaked_n payload file(s) marked text - they would be normalised on add"
    printf '%s\n' "$leaks" | sed 's/^/        /' >&2
  fi
fi

# The recovered Lua must load. unluac exits 0 on chunks whose output is not
# valid Lua (a goto whose label sits inside a block), so "decompiled" and
# "usable" are different claims and only compiling the tree tells them apart.
if [ -d source-app/lua/src ]; then
  if out="$(python3 tools/check-lua-syntax.py source-app/lua/src 2>&1)"; then
    ok "every recovered Lua module compiles ($(printf '%s' "$out" | tail -1))"
  else
    bad "recovered Lua does not compile"
    # Show the FAIL lines when there are any, but fall back to the tail: the
    # gate also exits non-zero when it cannot run at all (no lupa), and
    # reporting only "does not compile" for that sends whoever is reading the
    # log looking at 18,300 files that are in fact fine.
    if printf '%s\n' "$out" | grep -q '^FAIL'; then
      printf '%s\n' "$out" | grep '^FAIL' | head -5 | sed 's/^/        /' >&2
    fi
    printf '%s\n' "$out" | tail -5 | sed 's/^/        /' >&2
  fi
fi

# The bundle sweep is resumable and can be cut short by its time budget, so the
# extracted assets and the inventory describing them can drift apart in ways no
# file browser shows. Like the syntax gate below, this only runs when the
# payload is present; a pipeline-only clone has nothing to compare.
if [ -d source-app/game-assets/assets ]; then
  if out="$(python3 tools/check-asset-inventory.py source-app/game-assets 2>&1)"; then
    ok "extracted assets match their inventory ($(printf '%s' "$out" | grep -c '^  ok') checks)"
  else
    bad "extracted assets do not match their inventory"
    printf '%s\n' "$out" | grep '^  FAIL' | head -5 | sed 's/^/        /' >&2
  fi
fi

# "Which assets are in the APK and which are CDN-only" is a question the
# README answers in prose; asset_completeness_report.{md,json} answers it with
# numbers for every one of the 95,602 paths in the gameres manifest. Like the
# gate above, this only runs where the pipeline inputs are present (a fresh
# clone has neither the inventory nor the manifest), and it fails when the
# generated report no longer says what its inputs say.
if [ -f source-app/game-assets/inventory.jsonl ] \
   && [ -f decompiled/apktool/assets/AssetBundles/gameres ]; then
  if out="$(python3 tools/asset-completeness-report.py --check 2>&1)"; then
    ok "asset completeness report matches its inputs $(printf '%s' "$out" | sed 's/^asset completeness report is current //')"
  else
    bad "asset completeness report is stale - rerun tools/asset-completeness-report.py"
    printf '%s\n' "$out" | tail -3 | sed 's/^/        /' >&2
  fi
fi

# The recovered overlay is applied on top of unluac output, so it has to be
# idempotent: the pipeline runs it after every Lua stage, and a second run that
# failed would break every subsequent decompile.
if out="$(sh tools/unluac-batch/apply-recovered.sh 2>&1)"; then
  ok "recovered Lua overlay is idempotent ($(printf '%s' "$out" | tail -1))"
else
  bad "recovered Lua overlay does not apply cleanly"
  printf '%s\n' "$out" | tail -3 | sed 's/^/        /' >&2
fi

# The recovered payload is only useful if it is what the README claims. Counts
# tracked files, i.e. what a clone really receives - the working tree still holds
# the gitignored .luac and art files, which are not part of the promise. Runs
# whenever the payload is checked out (CI clones it in full); a pipeline-only
# clone has nothing to compare and says so instead of pretending to pass.
if [ -d source-app/lua ]; then
  # Reads the count out of the "What is and is not in version control" table by
  # matching the path in the row, so re-running the pipeline and updating the
  # docs is a deliberate edit rather than something the check silently accepts.
  doc_count() {
    awk -v p="$1" '
      $0 ~ "`" p "`" {
        n = split($0, a, "|"); last = ""
        for (i = 1; i <= n; i++) {
          v = a[i]; gsub(/[ \t,]/, "", v)
          if (v ~ /^[0-9]+$/) last = v
        }
        if (last != "") { print last; exit }
      }' README.md
  }
  agree() { # agree <label> <path-in-readme> <dir> - both sides count TRACKED files
    local want have
    want="$(doc_count "$2")"
    have="$(git ls-files -- "$3" | wc -l | tr -d ' ')"
    if [ -z "$want" ]; then
      bad "README has no count row for $2"
    elif [ "$want" = "$have" ]; then
      ok "$1 matches README ($have files)"
    else
      bad "$1: README says $want, tree tracks $have"
    fi
  }
  agree "Lua tree"    'source-app/lua/'             source-app/lua
  agree "table Lua"   'source-app/data-tables-lua/' source-app/data-tables-lua
  agree "C# tree"     'source-app/csharp/'          source-app/csharp
  agree "Java tree"   'source-app/src/'             source-app/src
  agree "Unity tree"  'source-app/unity-assets/'    source-app/unity-assets
else
  echo "  n/a   payload not checked out - tree/count check skipped"
fi

echo
printf 'checks: %d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
