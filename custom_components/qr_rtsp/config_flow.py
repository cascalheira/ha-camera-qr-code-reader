"""Config flow for the QR Code RTSP Reader integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_COOLDOWN,
    CONF_DEFAULT_ALLOW_UNLISTED,
    CONF_FPS,
    CONF_NAME,
    CONF_RTSP_TRANSPORT,
    CONF_RULES,
    CONF_STREAM_URL,
    CONF_WIDTH,
    DEFAULT_ALLOW_UNLISTED,
    DEFAULT_COOLDOWN,
    DEFAULT_FPS,
    DEFAULT_NAME,
    DEFAULT_TRANSPORT,
    DEFAULT_WIDTH,
    DOMAIN,
    RULE_END_TIME,
    RULE_NAME,
    RULE_PAYLOAD,
    RULE_SCRIPT,
    RULE_START_TIME,
    RULE_VALID_FROM,
    RULE_VALID_UNTIL,
    RULE_WEEKDAYS,
    TRANSPORTS,
    WEEKDAYS,
)
from .rules import _parse_date, normalize_rule

_FPS = vol.All(vol.Coerce(float), vol.Range(min=0.1, max=30))
_WIDTH = vol.All(vol.Coerce(int), vol.Range(min=0, max=4096))
_COOLDOWN = vol.All(vol.Coerce(float), vol.Range(min=0, max=3600))

_WEEKDAY_LABELS = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}


def _user_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schema for the initial setup step."""
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)): str,
            vol.Required(
                CONF_STREAM_URL, default=defaults.get(CONF_STREAM_URL, "")
            ): str,
            vol.Optional(
                CONF_RTSP_TRANSPORT,
                default=defaults.get(CONF_RTSP_TRANSPORT, DEFAULT_TRANSPORT),
            ): vol.In(TRANSPORTS),
            vol.Optional(CONF_FPS, default=defaults.get(CONF_FPS, DEFAULT_FPS)): _FPS,
            vol.Optional(
                CONF_WIDTH, default=defaults.get(CONF_WIDTH, DEFAULT_WIDTH)
            ): _WIDTH,
            vol.Optional(
                CONF_COOLDOWN, default=defaults.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)
            ): _COOLDOWN,
        }
    )


def _optional(key: str, value: Any) -> vol.Marker:
    """An optional key that pre-fills its current value without forcing one."""
    if value in (None, "", []):
        return vol.Optional(key)
    return vol.Optional(key, description={"suggested_value": value})


def _rule_schema(rule: dict[str, Any]) -> vol.Schema:
    """Schema describing a single access rule."""
    weekday_options = [
        selector.SelectOptionDict(value=key, label=_WEEKDAY_LABELS[key])
        for key in WEEKDAYS
    ]
    return vol.Schema(
        {
            vol.Required(
                RULE_PAYLOAD,
                description={"suggested_value": rule.get(RULE_PAYLOAD)},
            ): selector.TextSelector(),
            _optional(RULE_NAME, rule.get(RULE_NAME)): selector.TextSelector(),
            _optional(
                RULE_VALID_FROM, rule.get(RULE_VALID_FROM)
            ): selector.DateSelector(),
            _optional(
                RULE_VALID_UNTIL, rule.get(RULE_VALID_UNTIL)
            ): selector.DateSelector(),
            _optional(RULE_WEEKDAYS, rule.get(RULE_WEEKDAYS)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=weekday_options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            _optional(
                RULE_START_TIME, rule.get(RULE_START_TIME)
            ): selector.TimeSelector(),
            _optional(RULE_END_TIME, rule.get(RULE_END_TIME)): selector.TimeSelector(),
            _optional(RULE_SCRIPT, rule.get(RULE_SCRIPT)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="script")
            ),
        }
    )


def _validate_rule(user_input: dict[str, Any]) -> dict[str, str]:
    """Validate rule input, returning a field->error map (empty if valid)."""
    errors: dict[str, str] = {}
    if not user_input.get(RULE_PAYLOAD, "").strip():
        errors[RULE_PAYLOAD] = "payload_required"
    start = _parse_date(user_input.get(RULE_VALID_FROM))
    end = _parse_date(user_input.get(RULE_VALID_UNTIL))
    if start and end and end < start:
        errors[RULE_VALID_UNTIL] = "invalid_dates"
    return errors


def _rule_label(rule: dict[str, Any]) -> str:
    """Human-friendly label for the rule picker."""
    name = rule.get(RULE_NAME)
    payload = rule.get(RULE_PAYLOAD, "")
    return f"{name} ({payload})" if name else payload


class QrRtspConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial configuration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the stream URL and tuning options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_STREAM_URL].strip()
            if not url.startswith(("rtsp://", "rtsps://", "http://", "https://")):
                errors[CONF_STREAM_URL] = "invalid_url"
            else:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return QrRtspOptionsFlow()


class QrRtspOptionsFlow(OptionsFlow):
    """Edit tuning settings and per-code access rules."""

    def __init__(self) -> None:
        """Initialize transient editing state."""
        self._edit_index: int | None = None

    def _rules(self) -> list[dict[str, Any]]:
        """Return a mutable copy of the stored rules."""
        return [dict(rule) for rule in self.config_entry.options.get(CONF_RULES, [])]

    def _save(self, **changes: Any) -> ConfigFlowResult:
        """Persist options, preserving keys not being changed."""
        return self.async_create_entry(
            data={**self.config_entry.options, **changes}
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the top-level options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "add_rule", "manage_rules"],
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit stream/scan tuning settings."""
        if user_input is not None:
            return self._save(**user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_RTSP_TRANSPORT,
                    default=current.get(CONF_RTSP_TRANSPORT, DEFAULT_TRANSPORT),
                ): vol.In(TRANSPORTS),
                vol.Optional(
                    CONF_FPS, default=current.get(CONF_FPS, DEFAULT_FPS)
                ): _FPS,
                vol.Optional(
                    CONF_WIDTH, default=current.get(CONF_WIDTH, DEFAULT_WIDTH)
                ): _WIDTH,
                vol.Optional(
                    CONF_COOLDOWN, default=current.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)
                ): _COOLDOWN,
                vol.Optional(
                    CONF_DEFAULT_ALLOW_UNLISTED,
                    default=current.get(
                        CONF_DEFAULT_ALLOW_UNLISTED, DEFAULT_ALLOW_UNLISTED
                    ),
                ): bool,
            }
        )
        return self.async_show_form(step_id="general", data_schema=schema)

    async def async_step_add_rule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add (or replace by payload) an access rule."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_rule(user_input)
            if not errors:
                new = normalize_rule(user_input)
                rules = [
                    r for r in self._rules() if r[RULE_PAYLOAD] != new[RULE_PAYLOAD]
                ]
                rules.append(new)
                return self._save(**{CONF_RULES: rules})

        return self.async_show_form(
            step_id="add_rule",
            data_schema=_rule_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_manage_rules(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an existing rule to edit or delete."""
        rules = self._rules()
        if not rules:
            return self.async_abort(reason="no_rules")
        if user_input is not None:
            self._edit_index = int(user_input["rule"])
            return await self.async_step_edit_rule()

        options = [
            selector.SelectOptionDict(value=str(index), label=_rule_label(rule))
            for index, rule in enumerate(rules)
        ]
        schema = vol.Schema(
            {
                vol.Required("rule"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options)
                )
            }
        )
        return self.async_show_form(step_id="manage_rules", data_schema=schema)

    async def async_step_edit_rule(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit or delete the selected rule."""
        rules = self._rules()
        assert self._edit_index is not None
        rule = rules[self._edit_index]

        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("delete"):
                del rules[self._edit_index]
                return self._save(**{CONF_RULES: rules})
            errors = _validate_rule(user_input)
            if not errors:
                rules[self._edit_index] = normalize_rule(user_input)
                return self._save(**{CONF_RULES: rules})
            rule = user_input

        schema = _rule_schema(rule).extend(
            {vol.Optional("delete", default=False): bool}
        )
        return self.async_show_form(
            step_id="edit_rule", data_schema=schema, errors=errors
        )
