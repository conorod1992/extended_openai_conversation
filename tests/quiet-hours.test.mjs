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

test("renders schedule semantics and automation hooks clearly", () => {
  const html = renderQuietHours(panel({
    active: false,
    config: baseConfig,
    satellites: [],
    state_entity_id: "binary_sensor.extended_openai_quiet_hours",
  }));

  assert.match(html, /Outside Quiet Hours/);
  assert.match(html, /Enable daily schedule/);
  assert.match(html, /ceiling, not a target/);
  assert.match(html, /binary_sensor\.extended_openai_quiet_hours/);
  assert.match(html, /enable_quiet_hours/);
  assert.match(html, /disable_quiet_hours/);
  assert.match(html, /keep checking as satellites become available/);
});

test("distinguishes active and disabled schedules", () => {
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

test("keeps an unavailable manual override visible instead of silently showing automatic", () => {
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
      media_player_entity_id: "media_player.missing",
      media_player_source: "manual",
      wake_sound_entity_id: null,
      wake_sound_source: null,
    }],
  }));

  assert.match(html, /Unavailable · media_player\.missing/);
  assert.match(html, /Manual override/);
  assert.match(html, /normal for many non-Voice-PE satellites/);
});
