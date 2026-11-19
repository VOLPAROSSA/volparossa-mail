#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Installs the shipped unit temporarily on an ephemeral GitHub VM. Both server
# lifetimes and the bootstrap have only private loopback interfaces.
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && $(id -u) != 0 ]]
[[ $(cat /proc/1/comm) == systemd ]]
binary=$(realpath -- "$1")
expected=$2
out=$(realpath -- "$3")
[[ ${#expected} == 64 && $(sha256sum "$binary" | cut -d ' ' -f 1) == "$expected" ]]
lib=/usr/local/libexec/volparossa-mail
state=/var/lib/volparossa-mail
fixture=/run/volparossa-owner-mail-trial
unit=/run/systemd/system/volparossa-mail.service
account=volparossa-mail
for path in "$lib" "$state" "$fixture" "$unit" "$unit.d" /etc/systemd/system/volparossa-mail.service /usr/lib/systemd/system/volparossa-mail.service; do
  [[ ! -e "$path" && ! -L "$path" ]]
done
if getent passwd "$account" >/dev/null || getent group "$account" >/dev/null; then exit 1; fi
fixture_before=$(mktemp "$RUNNER_TEMP/owner-service-before.XXXXXX")
fixture_after=$(mktemp "$RUNNER_TEMP/owner-service-after.XXXXXX")
host_namespace=$(readlink /proc/self/ns/net)
cleanup() {
  if [[ -f "$unit" ]]; then sudo systemctl stop volparossa-mail.service; fi
  sudo rm -rf -- "$fixture" "$unit" "$unit.d" "$state" "$lib"
  sudo systemctl daemon-reload
  if getent passwd "$account" >/dev/null; then sudo userdel "$account"; fi
  if getent group "$account" >/dev/null; then sudo groupdel "$account"; fi
}
trap 'cleanup; rm -f -- "$fixture_before" "$fixture_after"' EXIT
sudo systemd-sysusers "$PWD/packaging/systemd/volparossa-mail.conf"
sudo install -d -m 0755 "$lib" "$fixture/scripts" "$fixture/tests" "$unit.d"
sudo install -d -m 0700 -o "$account" -g "$account" "$state"
sudo install -m 0755 "$binary" "$lib/stalwart"
sudo install -m 0644 packaging/systemd/{store.json,initialize-owner.py} "$lib/"
sudo install -m 0644 packaging/systemd/volparossa-mail.service "$unit"
sudo install -m 0644 packaging/systemd/local-jmap.conf "$unit.d/"
sudo install -m 0644 scripts/{smoke_stalwart,stalwart_owner_smtp}.py "$fixture/scripts/"
sudo install -m 0644 tests/owner_service_guest.py "$fixture/tests/"
printf '[Service]\nPrivateNetwork=yes\nEnvironment=STALWART_HOSTNAME=mail.volparossa.test\n' |
  sudo tee "$unit.d/disposable.conf" >/dev/null
sudo systemctl daemon-reload
sudo systemd-analyze verify "$unit"
sudo systemctl start volparossa-mail
[[ $(systemctl show volparossa-mail -p ConditionResult --value) == no ]]
[[ $(systemctl show volparossa-mail -p MainPID --value) == 0 ]]
guest=/usr/bin/python3
probe="$fixture/tests/owner_service_guest.py"
sudo systemd-run --quiet --pipe --wait --collect --property=PrivateNetwork=yes \
  --property=User="$account" --property=Group="$account" --property=WorkingDirectory="$state" \
  "$guest" -B "$probe" bootstrap "$host_namespace"
sudo systemctl start volparossa-mail
first=$(systemctl show volparossa-mail -p MainPID --value)
[[ "$first" =~ ^[1-9][0-9]*$ ]]
read_as_owner() {
  sudo nsenter --target "$1" --net -- runuser -u "$account" -- "$guest" -B "$probe" "$2" "$host_namespace"
}
read_as_owner "$first" deliver > "$fixture_before"
sudo systemctl kill --kill-who=main --signal=KILL volparossa-mail
for ((attempt=0; attempt<100; attempt++)); do
  second=$(systemctl show volparossa-mail -p MainPID --value)
  if [[ "$second" =~ ^[1-9][0-9]*$ && "$second" != "$first" ]]; then break; fi
  sleep 0.2
done
[[ "$second" =~ ^[1-9][0-9]*$ && "$second" != "$first" ]]
read_as_owner "$second" read > "$fixture_after"
cmp "$fixture_before" "$fixture_after"
restarts=$(systemctl show volparossa-mail -p NRestarts --value)
[[ "$restarts" == 1 && $(systemctl is-active volparossa-mail) == active ]]
sudo systemctl stop volparossa-mail
[[ $(systemctl show volparossa-mail -p MainPID --value) == 0 ]]
cleanup
trap 'rm -f -- "$fixture_before" "$fixture_after"' EXIT
[[ ! -e "$state" && ! -e "$lib" && ! -e "$unit" && ! -e "$fixture" ]]
if getent passwd "$account" >/dev/null; then exit 1; fi
jq --arg sha "$expected" --argjson restarts "$restarts" \
  '{server_sha256:$sha, missing_store_skipped:true, first_owner_provisioned:true, existing_store_preserved:true, automatic_restarts:$restarts,
    same_mail_and_sqlite:true, private_loopback:true, clean_stop:true, removed:true,
    original_bytes,original_sha256,received_bytes,received_sha256}' \
  "$fixture_after" > "$out/owner-service.json"
