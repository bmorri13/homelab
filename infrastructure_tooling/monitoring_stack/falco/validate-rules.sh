#!/usr/bin/env bash
# Validate the Falco rules with the Falco release install.sh pins. No root needed:
# the static release tarball runs `falco -V` and `--dry-run` without a driver.
#   1. Syscall rules: the stable falco_rules.yaml from the same release, then
#      rules.d/*.yaml in load order. Fails on any error or warning (unknown field or
#      macro, unused list, condition that doesn't compile).
#   2. With config.d/homelab-rules.yaml, the enabled rules must be exactly: every
#      stable rule, the selected incubating/sandbox rules, and every homelab rule.
#   3. The k8saudit overrides (falco-k8saudit customRules in falco_k3s/values.yaml)
#      against the k8saudit plugin's rules, with the plugins they need.
# Needs bash, curl, tar, sha256sum and node. Used by .github/workflows/falco-rules.yml.
set -euo pipefail

cd "$(dirname "$0")"
here=$PWD
VERSION=$(sed -n 's/^VERSION=//p' install.sh)
SHA256=$(sed -n 's/^SHA256_ARCH=//p' install.sh)
# What falco-k8saudit runs (falcoctl refs k8saudit:0.16, k8saudit-rules:0.16, and the
# json plugin they depend on), pinned for repeatable checks.
K8SAUDIT_VERSION=0.16.1
K8SAUDIT_SHA256=f6d23589776d89a26075bcc19d41403581a45e720f8e023813fda40713ad07e4
JSON_VERSION=0.7.4
JSON_SHA256=d1370d6387f9ae0d622cad3673701aac4d9e61a78e3af77089146dc111972117
K8SAUDIT_RULES_SHA256=9c776e9ed6041c645d2313e3063a5745be6e625d47656c0a0c99bc96ae823379

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
get() {  # <url> <file> <sha256>
    curl -fsSL --retry 3 -o "$work/$2" "$1"
    echo "$3  $work/$2" | sha256sum -c --quiet - || { echo "$2: SHA-256 mismatch" >&2; exit 1; }
}

get "https://download.falco.org/packages/bin/x86_64/falco-${VERSION}-x86_64.tar.gz" falco.tar.gz "$SHA256"
tar -xzf "$work/falco.tar.gz" -C "$work"
F="$work/falco-${VERSION}-x86_64"
falco="$F/usr/bin/falco"
stable="$F/etc/falco/falco_rules.yaml"

cat > "$work/syscall.yaml" <<EOF
plugins:
  - name: container
    library_path: $F/usr/share/falco/plugins/libcontainer.so
    init_config: ""
load_plugins: [container]
rules_files:
  - $stable
  - $here/rules.d
config_files:
  - $here/config.d/homelab-rules.yaml
stdout_output:
  enabled: true
EOF

# The checks that need JSON parsing, in node (no PyYAML or jq on the runners).
cat > "$work/check.js" <<'EOF'
const fs = require('fs');
const [mode, ...args] = process.argv.slice(2);
if (mode === 'validate') {
  // falco -V JSON: one entry per file, each carrying the warnings left after loading it.
  const raw = fs.readFileSync(args[0], 'utf8');
  let res;
  try { res = JSON.parse(raw).falco_load_results; } catch (e) { console.log(raw); process.exit(1); }
  let bad = false;
  for (const r of res) {
    console.log(`${r.name}: ${r.successful ? 'Ok' : 'Invalid'}`);
    for (const e of r.errors || []) { bad = true; console.log(`  error ${e.code}: ${e.message}`); }
  }
  for (const w of res[res.length - 1].warnings || []) {
    bad = true;
    const loc = (w.context.locations || []).map((l) => l.item_name).filter(Boolean).join(' / ');
    console.log(`  warning ${w.code}: ${loc}`);
  }
  console.log(bad ? 'FAILED' : `${res.length} files: no errors, no warnings`);
  process.exit(bad ? 1 : 0);
}
if (mode === 'selection') {
  // Expected: stable + selected + homelab (no maturity tag); actual: the dry run's list.
  const listed = JSON.parse(fs.readFileSync(args[0], 'utf8')).rules.map((r) => r.info);
  const selected = fs.readFileSync(args[1], 'utf8').split('\n')
    .map((l) => l.match(/^\s+rule:\s*(.+?)\s*$/)).filter(Boolean).map((m) => m[1]);
  // The dry run logs "Enabled rules:" then one indented rule name per line.
  const log = fs.readFileSync(args[2], 'utf8').split('\n');
  const start = log.findIndex((l) => /: Enabled rules:$/.test(l));
  if (start < 0) { console.log('no "Enabled rules:" in the dry run log'); process.exit(1); }
  const actual = new Set();
  for (const l of log.slice(start + 1)) {
    const m = l.match(/^[A-Z][a-z]{2} [A-Z][a-z]{2} .*?: {4}(\S.*)$/);
    if (!m) break;
    actual.add(m[1].trim());
  }
  const maturity = (r) => (r.tags.find((t) => t.startsWith('maturity_')) || 'homelab').replace('maturity_', '');
  const groups = {};
  const expected = new Set();
  for (const r of listed) {
    const m = maturity(r);
    if (m === 'stable' || m === 'homelab' || selected.includes(r.name)) {
      expected.add(r.name);
      (groups[m] = groups[m] || []).push(r.name);
    }
  }
  for (const s of selected) if (!listed.some((r) => r.name === s)) { console.log(`selected rule not loaded: ${s}`); process.exit(1); }
  const missing = [...expected].filter((n) => !actual.has(n));
  const extra = [...actual].filter((n) => !expected.has(n));
  for (const [m, names] of Object.entries(groups)) console.log(`${m}: ${names.length}`);
  console.log(`enabled: ${actual.size}, expected: ${expected.size}`);
  if (missing.length || extra.length) {
    console.log(`missing: ${missing.join(', ') || '-'}\nunexpected: ${extra.join(', ') || '-'}`);
    process.exit(1);
  }
  process.exit(0);
}
EOF

echo "== syscall rules (Falco $VERSION, stable + rules.d in load order)"
validate=(-V "$stable")
for f in rules.d/*.yaml; do validate+=(-V "$here/$f"); done
"$falco" -c "$work/syscall.yaml" -o json_output=true "${validate[@]}" > "$work/validate.json" 2> "$work/validate.err" || true
node "$work/check.js" validate "$work/validate.json" || { cat "$work/validate.err" >&2; exit 1; }

echo "== rule selection (config.d/homelab-rules.yaml)"
"$falco" -c "$work/syscall.yaml" -o json_output=true -L > "$work/list.json" 2>/dev/null
"$falco" -c "$work/syscall.yaml" -o engine.kind=nodriver -o log_level=debug -o log_stderr=true \
    -o json_output=false --dry-run -v > "$work/dryrun.log" 2>&1
node "$work/check.js" selection "$work/list.json" config.d/homelab-rules.yaml "$work/dryrun.log"

echo "== k8saudit overrides (falco_k3s/values.yaml, k8saudit $K8SAUDIT_VERSION)"
get "https://download.falco.org/plugins/stable/k8saudit-${K8SAUDIT_VERSION}-linux-x86_64.tar.gz" k8saudit.tar.gz "$K8SAUDIT_SHA256"
get "https://download.falco.org/plugins/stable/json-${JSON_VERSION}-linux-x86_64.tar.gz" json.tar.gz "$JSON_SHA256"
get "https://raw.githubusercontent.com/falcosecurity/plugins/plugins/k8saudit/v${K8SAUDIT_VERSION}/plugins/k8saudit/rules/k8s_audit_rules.yaml" \
    k8s_audit_rules.yaml "$K8SAUDIT_RULES_SHA256"
tar -xzf "$work/k8saudit.tar.gz" -C "$work"
tar -xzf "$work/json.tar.gz" -C "$work"
# The block scalar under "homelab_k8saudit.yaml: |-", up to the first less-indented line.
awk '/^ *homelab_k8saudit\.yaml: \|-$/ {on=1; next}
     on { if (!ind) { match($0, /^ */); ind=RLENGTH }
          match($0, /^ */); if ($0 != "" && RLENGTH < ind) exit
          print substr($0, ind + 1) }' ../../falco_k3s/values.yaml > "$work/homelab_k8saudit.yaml"
[[ -s "$work/homelab_k8saudit.yaml" ]] || { echo "no homelab_k8saudit.yaml in falco_k3s/values.yaml" >&2; exit 1; }
cat > "$work/k8saudit.yaml" <<EOF
plugins:
  - name: k8saudit
    library_path: $work/libk8saudit.so
    init_config: ""
  - name: json
    library_path: $work/libjson.so
    init_config: ""
load_plugins: [k8saudit, json]
EOF
"$falco" -c "$work/k8saudit.yaml" -o json_output=true \
    -V "$work/k8s_audit_rules.yaml" -V "$work/homelab_k8saudit.yaml" > "$work/k8saudit.json" 2> "$work/k8saudit.err" || true
node "$work/check.js" validate "$work/k8saudit.json" || { cat "$work/k8saudit.err" >&2; exit 1; }
