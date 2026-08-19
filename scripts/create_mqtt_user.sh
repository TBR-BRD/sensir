#!/usr/bin/env bash
# Creates/updates mosquitto/config/passwd with the MQTT_USERNAME/MQTT_PASSWORD
# from .env, so the sensors and the backend can authenticate against the
# broker. Re-run after changing credentials in .env, then restart mosquitto.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo ".env not found - copy .env.example to .env first" >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a; source .env; set +a

if [ -z "${MQTT_USERNAME:-}" ] || [ -z "${MQTT_PASSWORD:-}" ]; then
  echo "MQTT_USERNAME / MQTT_PASSWORD must be set in .env" >&2
  exit 1
fi

touch mosquitto/config/passwd

docker run --rm \
  -v "$(pwd)/mosquitto/config:/mosquitto/config" \
  eclipse-mosquitto \
  mosquitto_passwd -b /mosquitto/config/passwd "$MQTT_USERNAME" "$MQTT_PASSWORD"

echo "Wrote credentials for '$MQTT_USERNAME' to mosquitto/config/passwd"
