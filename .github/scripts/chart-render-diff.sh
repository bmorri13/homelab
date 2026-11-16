#!/usr/bin/env bash
# Render every Helm chart a change touches, before and after, and report what differs
# in the manifests Argo CD would apply.
#
#   chart-render-diff.sh <base-ref> [summary-file]
#
# Compares the working tree with <base-ref> (the merge base is used). Writes Markdown
# to summary-file (default: stdout). Read-only: helm template and git, no cluster.
#
# Exit status 1 when a chart that rendered at the base no longer renders, or when the
# archives vendored in charts/ do not match the versions Chart.yaml names (Helm then
# renders the old chart, or silently drops the dependency). Every other difference is
# reported, not judged: a person reads it.
set -euo pipefail

base_ref="${1:?usage: chart-render-diff.sh <base-ref> [summary-file]}"
summary="${2:-/dev/stdout}"
max_diff_lines=300
# A change to one of these lines says the chart version moved, not what it does.
noise='^[+-] *(image:|helm\.sh/chart:|app\.kubernetes\.io/version:|checksum/[a-z-]+:|chart:|version:)'

root="$(git rev-parse --show-toplevel)"
cd "$root"
base="$(git merge-base "$base_ref" HEAD)"
work="$(mktemp -d)"
trap 'git worktree remove --force "$work/base" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT
git worktree add --detach --quiet "$work/base" "$base"

# The chart a file belongs to: the nearest directory, in either tree, with a Chart.yaml.
chart_of() {
  local dir
  dir="$(dirname "$1")"
  while [[ "$dir" != "." && "$dir" != "/" ]]; do
    if [[ -f "$dir/Chart.yaml" || -f "$work/base/$dir/Chart.yaml" ]]; then
      # a file inside charts/ or templates/ belongs to the chart above it
      echo "$dir"
      return
    fi
    dir="$(dirname "$dir")"
  done
}

charts="$(git diff --name-only "$base" -- | while read -r f; do chart_of "$f"; done | sort -u)"

# render <tree> <chart> <out-dir>: 0 rendered, 1 failed, 2 chart absent in that tree
render() {
  [[ -f "$1/$2/Chart.yaml" ]] || return 2
  helm template render "$1/$2" --include-crds --output-dir "$3" >/dev/null 2>"$3.err"
}

failed=0
{
  echo "## Chart render diff"
  echo
  if [[ -z "$charts" ]]; then
    echo "No Helm chart is touched by this change."
  fi
  for chart in $charts; do
    name="${chart//\//_}"
    old="$work/old/$name" new="$work/new/$name"
    mkdir -p "$old" "$new"
    old_rc=0 new_rc=0
    render "$work/base" "$chart" "$old" || old_rc=$?
    render "$root" "$chart" "$new" || new_rc=$?
    echo "### \`$chart\`"
    echo
    if [[ $new_rc -eq 2 ]]; then
      echo "Chart removed."
      echo
      continue
    fi
    stale="$(helm dependency list "$root/$chart" 2>/dev/null | awk 'NR > 1 && NF && $NF != "ok" && $NF != "unpacked"' || true)"
    if [[ -n "$stale" ]]; then
      failed=1
      echo "**The archives in \`charts/\` do not match \`Chart.yaml\`.** Helm renders what is in \`charts/\`, and"
      echo "drops a dependency it cannot match, so merging this would deploy the old chart or remove"
      echo "workloads. Run \`helm dependency update\` on the branch and commit the new archive."
      echo
      echo '```'
      echo "$stale"
      echo '```'
      echo
    fi
    if [[ $new_rc -ne 0 ]]; then
      if [[ $old_rc -eq 0 ]]; then
        failed=1
        echo "**Does not render any more.** It rendered before this change. For a vendored chart this"
        echo "usually means \`Chart.yaml\` names a version whose archive is not in \`charts/\`."
      else
        echo "Does not render, and did not before this change either: not checked."
      fi
      echo
      echo '```'
      tail -n 15 "$new.err"
      echo '```'
      echo
      continue
    fi
    if [[ $old_rc -ne 0 ]]; then
      echo "New chart, or one that did not render before: nothing to compare with."
      echo
      continue
    fi
    diff -ruN "$old" "$new" >"$work/$name.diff" || true
    if [[ ! -s "$work/$name.diff" ]]; then
      echo "**No change to the rendered manifests.** If this change bumps a chart version, the new"
      echo "version is not what renders: check that the archive in \`charts/\` was updated too."
      echo
      continue
    fi
    changed="$(grep -E "^[+-]" "$work/$name.diff" | grep -vcE "^(\+\+\+|---) " || true)"
    files="$(grep -c '^diff -ruN' "$work/$name.diff" || true)"
    real="$(grep -E '^[+-]' "$work/$name.diff" | grep -vE '^(\+\+\+|---) ' | grep -vcE "$noise" || true)"
    removed="$({ diff -rq "$old" "$new" || true; } | grep -c "^Only in $old" || true)"
    crds="$({ diff -rqN "$old" "$new" | awk '{print $2; print $4}' \
              | xargs -r grep -lsE '^kind: CustomResourceDefinition' || true; } | sed -E 's#.*/##' | sort -u | wc -l)"
    echo "| | |"
    echo "|---|---|"
    echo "| Template files that differ | $files |"
    echo "| Lines that differ beyond image tags and chart-version labels | $real |"
    echo "| Template files removed | $removed |"
    echo "| CRD files that differ | $crds |"
    echo
    if [[ "$real" -eq 0 ]]; then
      echo "Only image references and chart-version labels changed."
    fi
    if [[ "$crds" -gt 0 ]]; then
      echo "**CRDs change.** Argo CD applies them on sync, and a CRD change is not undone by reverting the chart."
    fi
    images="$(grep -E '^[+-] *image:' "$work/$name.diff" | sed -E 's/^([+-]) *image: *"?([^"]*)"?/\1 \2/' | sort -u || true)"
    if [[ -n "$images" ]]; then
      echo
      echo "Images:"
      echo
      echo '```diff'
      echo "$images"
      echo '```'
    fi
    echo
    echo "<details><summary>Rendered diff ($changed lines)</summary>"
    echo
    echo '```diff'
    sed -E -e "s#$work/(old|new)/$name/#\\1/#g" -e 's/^((---|\+\+\+) [^\t]+)\t.*/\1/' \
        -e "${max_diff_lines}q" "$work/$name.diff"
    echo '```'
    if [[ "$(wc -l <"$work/$name.diff")" -gt "$max_diff_lines" ]]; then
      echo
      echo "Cut at $max_diff_lines lines. Run \`.github/scripts/chart-render-diff.sh origin/main\` on the branch for all of it."
    fi
    echo
    echo "</details>"
    echo
  done
} >>"$summary"

exit "$failed"
