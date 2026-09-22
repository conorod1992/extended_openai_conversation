import assert from "node:assert/strict";
import test from "node:test";

import {renderQuietHours} from "../custom_components/extended_openai_conversation_responses/frontend/quiet-hours-ui.js";

function panel(result, draft = null, states = {}) {
  return {
    _result: result,
    _quietHoursDraft: draft,
    _hass: {states},
    _e(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    },
  };
}

const baseConfig = {
  enabled: true,
  start: "22:00",
  end: "07:00",
  max_volume: 0.2,
  wake_sound: "off",
  overrides: {},
};

test("renders schedule semantics and automation hooks in plain language", () => {
  const html = renderQuietHours(panel({
    active: false,
    config: baseConfig,
    satellites: [],
    state_entity_id: "binary_sensor.extended_openai_quiet_hours",
  }));

  assert.match(html, /Outside Quiet Hours/);
  assert.match(html, /Enable daily schedule/);
  assert.match(html, /only turns louder satellites down/);
  assert.match(html, /never turns a quieter satellite up/);
  assert.match(html, /other audio from that speaker may also be quieter/);
  assert.match(html, /chime played when a satellite hears its wake word/);
  assert.match(html, /binary_sensor\.extended_openai_quiet_hours/);
  assert.match(html, /enable_quiet_hours/);
  assert.match(html, /disable_quiet_hours/);
  assert.match(html, /keep checking and will pick them up/);
});

test("status describes saved state rather than unsaved draft changes", () => {
  const unsavedDisable = renderQuietHours(panel(
    {active: true, config: baseConfig, satellites: []},
    {...baseConfig, enabled: false, start: "23:00"},
  ));
  assert.match(unsavedDisable, /Quiet Hours active now/);
  assert.match(unsavedDisable, /22:00–07:00 every day/);

  const savedDisabled = {...baseConfig, enabled: false};
  const unsavedEnable = renderQuietHours(panel(
    {active: false, config: savedDisabled, satellites: []},
    {...baseConfig, enabled: true},
  ));
  assert.match(unsavedEnable, /Quiet Hours schedule disabled/);
  assert.match(unsavedEnable, /saved schedule is currently turned off/);
});

test("distinguishes active and disabled saved schedules", () => {
  const active = renderQuietHours(panel({
    active: true,
    config: baseConfig,
    satellites: [],
  }));
  assert.match(active, /Quiet Hours active now/);
  assert.match(active, />Active</);

  const disabled = renderQuietHours(panel({
    active: false,
    config: {...baseConfig, enabled: false},
    satellites: [],
  }));
  assert.match(disabled, /Quiet Hours schedule disabled/);
});

test("renders native device-scoped override pickers and preserves manual status", () => {
  const config = {
    ...baseConfig,
    overrides: {
      "assist_satellite.bedroom": {
        media_player_entity_id: "media_player.missing",
      },
    },
  };
  const html = renderQuietHours(panel({
    active: false,
    config,
    satellites: [{
      satellite_entity_id: "assist_satellite.bedroom",
      name: "Bedroom Voice",
      device_id: "device-bedroom",
      media_player_entity_id: "media_player.missing",
      media_player_source: "manual",
      media_player_candidates: ["media_player.bedroom"],
      wake_sound_entity_id: null,
      wake_sound_source: null,
      wake_sound_candidates: ["switch.bedroom_wake_sound"],
    }],
  }));

  assert.match(html, /<ha-entity-picker class="qh-override"/);
  assert.match(html, /data-kind="media_player_entity_id"/);
  assert.match(html, /data-domain="media_player"/);
  assert.match(html, /data-kind="wake_sound_entity_id"/);
  assert.match(html, /data-domain="switch"/);
  assert.match(html, /Speaker unavailable/);
  assert.doesNotMatch(html, /Speaker ready/);
  assert.match(html, /Manually selected for this satellite/);
  assert.doesNotMatch(html, /<select class="qh-override"/);
});

test("marks a mapped speaker ready only when a numeric volume is available", () => {
  const satellite = {
    satellite_entity_id: "assist_satellite.bedroom",
    name: "Bedroom Voice",
    media_player_entity_id: "media_player.bedroom",
    media_player_source: "auto",
    wake_sound_entity_id: null,
    wake_sound_source: null,
  };
  const ready = renderQuietHours(panel(
    {active: false, config: baseConfig, satellites: [satellite]},
    null,
    {"media_player.bedroom": {state: "idle", attributes: {volume_level: 0.4}}},
  ));
  assert.match(ready, /Speaker ready/);

  const unavailable = renderQuietHours(panel(
    {active: false, config: baseConfig, satellites: [satellite]},
    null,
    {"media_player.bedroom": {state: "idle", attributes: {}}},
  ));
  assert.match(unavailable, /Speaker unavailable/);
});
