#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Apply platform/kafka/topics.yml to the broker. Idempotent: safe to run on
# every startup, and the second run makes no changes at all.
#
# It runs in two modes, so the same script serves the compose init container
# and a human on the host:
#   * If the Kafka CLI is reachable locally (KAFKA_BIN or on PATH) it is used
#     directly.  This is the init-container path.
#   * Otherwise the commands are piped through `docker compose exec kafka`.
#
# Every broker call is a JVM start, so the broker is described ONCE up front
# and only genuine differences are applied. That is what keeps a no-op run
# down to a single call.
#
# Env:
#   BOOTSTRAP   broker to talk to           (default: localhost:9092 / kafka:9092)
#   TOPICS_FILE path to topics.yml          (default: alongside this script)
#   DRY_RUN=1   print the plan, change nothing
# ---------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOPICS_FILE="${TOPICS_FILE:-$HERE/topics.yml}"
COMPOSE_FILE="${COMPOSE_FILE:-$HERE/../docker-compose.infra.yml}"

if [ -n "${KAFKA_BIN:-}" ] || command -v kafka-topics.sh >/dev/null 2>&1; then
  KAFKA_BIN="${KAFKA_BIN:-$(dirname "$(command -v kafka-topics.sh)")}"
  BOOTSTRAP="${BOOTSTRAP:-kafka:9092}"
  # </dev/null: these must never consume the loop's input.
  kcli() { "$KAFKA_BIN/$1" "${@:2}" </dev/null; }
else
  BOOTSTRAP="${BOOTSTRAP:-localhost:9092}"
  kcli() { docker compose -f "$COMPOSE_FILE" exec -T kafka "/opt/kafka/bin/$1" "${@:2}" </dev/null; }
fi

DLQ_RETENTION=7776000000 # 90 days, per `defaults.dlq` in topics.yml

# --- parse topics.yml -------------------------------------------------------
# The file has a fixed, flat shape (see topics.yml), so a small awk pass keeps
# this script dependency-free inside the Kafka image, which has no Python.
# tests/test_topics_registry.py asserts this parser agrees with a real YAML read.
plan() {
  awk '
    /^[[:space:]]*#/ { next }
    /^  - name:/ {
      if (name != "") print name "\t" parts "\t" cleanup "\t" retention "\t" dlq
      name=$3; parts=""; cleanup=""; retention=""; dlq="false"; next
    }
    /^    partitions:/   { parts=$2; next }
    /^    cleanup:/      { cleanup=$2; next }
    /^    retention_ms:/ { retention=$2; next }
    /^    dlq:/          { dlq=$2; next }
    END { if (name != "") print name "\t" parts "\t" cleanup "\t" retention "\t" dlq }
  ' "$TOPICS_FILE"
}

# Expand each declared topic into itself plus its .dlq companion.
expanded_plan() {
  while IFS=$'\t' read -r name partitions cleanup retention dlq; do
    [ -n "$name" ] || continue
    printf '%s\t%s\t%s\t%s\n' "$name" "$partitions" "$cleanup" "$retention"
    if [ "$dlq" = "true" ]; then
      printf '%s.dlq\t%s\tdelete\t%s\n' "$name" "$partitions" "$DLQ_RETENTION"
    fi
  done < <(plan)
}

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "==> plan for $TOPICS_FILE (dry run, broker untouched)"
  while IFS=$'\t' read -r name partitions cleanup retention; do
    echo "PLAN $name partitions=$partitions cleanup.policy=$cleanup retention.ms=$retention"
  done < <(expanded_plan)
  exit 0
fi

echo "==> applying $TOPICS_FILE to $BOOTSTRAP"

# One describe call for the whole broker: "Topic: x ... PartitionCount: 6 ...
# Configs: cleanup.policy=delete,retention.ms=..." for every topic that exists.
SNAPSHOT="$(kcli kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --describe 2>/dev/null | grep '^Topic: ' || true)"

describe_field() { # topic, field name -> value, empty if the topic is unknown
  echo "$SNAPSHOT" | awk -v topic="$1" -v field="$2" '
    $2 == topic {
      for (i = 1; i <= NF; i++) if ($i == field ":" || $i == field) { print $(i + 1); exit }
      split($0, parts, field ": "); if (length(parts) > 1) { print parts[2]; exit }
    }' | head -1
}

created=0
altered=0
unchanged=0

while IFS=$'\t' read -r topic partitions cleanup retention; do
  current_partitions="$(describe_field "$topic" "PartitionCount")"

  if [ -z "$current_partitions" ]; then
    # --if-not-exists so that a human running `make topics` while the init
    # container is still applying the same file is a no-op, not a failure.
    kcli kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --create --if-not-exists --topic "$topic" \
      --partitions "$partitions" --replication-factor 1 \
      --config "cleanup.policy=$cleanup" \
      --config "retention.ms=$retention" \
      --config "min.insync.replicas=1" >/dev/null
    echo "create $topic partitions=$partitions cleanup=$cleanup retention.ms=$retention"
    created=$((created + 1))
    continue
  fi

  changed=0

  if [ "$current_partitions" -lt "$partitions" ]; then
    kcli kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --alter --topic "$topic" \
      --partitions "$partitions" >/dev/null
    echo "alter  $topic partitions $current_partitions -> $partitions"
    changed=1
  elif [ "$current_partitions" -gt "$partitions" ]; then
    echo "WARN   $topic has $current_partitions partitions, topics.yml declares $partitions" >&2
    echo "       Kafka cannot shrink a topic; recreate it deliberately if this matters." >&2
  fi

  configs="$(describe_field "$topic" "Configs")"
  case "$configs" in
    *"cleanup.policy=$cleanup"*) policy_ok=1 ;;
    *) policy_ok=0 ;;
  esac
  case "$configs" in
    *"retention.ms=$retention"*) retention_ok=1 ;;
    *) retention_ok=0 ;;
  esac

  if [ "$policy_ok" = "0" ] || [ "$retention_ok" = "0" ]; then
    kcli kafka-configs.sh --bootstrap-server "$BOOTSTRAP" --entity-type topics \
      --entity-name "$topic" --alter \
      --add-config "cleanup.policy=$cleanup,retention.ms=$retention,min.insync.replicas=1" >/dev/null
    echo "alter  $topic configs -> cleanup.policy=$cleanup retention.ms=$retention"
    changed=1
  fi

  if [ "$changed" = "0" ]; then
    unchanged=$((unchanged + 1))
  else
    altered=$((altered + 1))
  fi
done < <(expanded_plan)

echo "==> $((created + altered + unchanged)) topics reconciled: $created created, $altered altered, $unchanged unchanged"
