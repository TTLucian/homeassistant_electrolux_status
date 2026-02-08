"""Number platform for Electrolux Status."""

import asyncio
import logging
from typing import Any, cast

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, NUMBER
from .entity import ElectroluxEntity
from .util import (
    AuthenticationError,
    ElectroluxApiClient,
    format_command_for_appliance,
    map_command_error_to_home_assistant_error,
    time_minutes_to_seconds,
    time_seconds_to_minutes,
)

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Configure number platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if appliances := coordinator.data.get("appliances", None):
        for appliance_id, appliance in appliances.appliances.items():
            entities = [
                entity for entity in appliance.entities if entity.entity_type == NUMBER
            ]
            _LOGGER.debug(
                "Electrolux add %d NUMBER entities to registry for appliance %s",
                len(entities),
                appliance_id,
            )
            async_add_entities(entities)


class ElectroluxNumber(ElectroluxEntity, NumberEntity):
    """Electrolux Status number class."""

    @property
    def entity_domain(self) -> str:
        """Entity domain for the entry. Used for consistent entity_id."""
        return NUMBER

    @property
    def mode(self) -> str:
        """Return the mode for the number entity."""
        # Use box input for unsupported entities (shows "NA")
        if not self._is_supported_by_program():
            return "box"
        # Use box input for start time (max 1439 minutes = 23:59)
        if self.entity_attr == "startTime":
            return "box"
        # Use slider for other controls with step constraints
        return "slider"

    @property
    def native_value(self) -> float | None:
        """Return the value reported by the number."""
        if not self._is_supported_by_program():
            return None

        if self.unit == UnitOfTime.SECONDS:
            value = time_seconds_to_minutes(self.extract_value())
        else:
            value = self.extract_value()

        # Special handling for targetDuration in minutes
        if self.entity_attr == "targetDuration" and self.unit == UnitOfTime.MINUTES:
            value = value / 60 if value else 0

        if not value:
            value = self.capability.get("default", None)
            if value == "INVALID_OR_NOT_SET_TIME":
                value = self.capability.get("min", None)
        if not value:
            return self._cached_value
        if isinstance(self.unit, UnitOfTemperature):
            value = round(value, 2)
        elif isinstance(self.unit, UnitOfTime):
            # Electrolux bug - prevent negative/disabled timers
            value = max(value, 0)

        # Clamp value to current program-specific min/max range
        min_val = self.native_min_value
        max_val = self.native_max_value
        if min_val is not None and value < min_val:
            value = min_val
        if max_val is not None and value > max_val:
            value = max_val

        self._cached_value = value
        return value

    @property
    def native_max_value(self) -> float:
        """Return the max value."""
        # Check current program for program-specific constraints
        current_program = self.reported_state.get("program")

        if (
            current_program
            and hasattr(self.get_appliance, "data")
            and self.get_appliance.data
        ):
            appliance_data = self.get_appliance.data
            if hasattr(appliance_data, "capabilities") and appliance_data.capabilities:
                # Use normalized entity key to match program capabilities
                entity_key = self.entity_attr
                program_caps = (
                    appliance_data.capabilities.get("program", {})
                    .get("values", {})
                    .get(current_program, {})
                    .get(entity_key, {})
                )
                if "max" in program_caps:
                    max_val = program_caps["max"]
                    if max_val is not None:
                        if self.unit == UnitOfTime.SECONDS:
                            max_val = time_seconds_to_minutes(cast(float, max_val))
                        elif self.unit == UnitOfTime.MINUTES:
                            # Program capabilities are in seconds, convert to minutes
                            max_val = time_seconds_to_minutes(cast(float, max_val))
                        return float(cast(float, max_val))

        # Fallback to global capability
        if self.unit == UnitOfTime.SECONDS:
            max_val = time_seconds_to_minutes(self.capability.get("max", 100))
            return float(max_val) if max_val is not None else 100.0
        if self.unit == UnitOfTemperature.CELSIUS:
            return float(self.capability.get("max", 300))
        return float(self.capability.get("max", 100))

    @property
    def native_min_value(self) -> float:
        """Return the min value."""
        # Check current program for program-specific constraints
        current_program = self.reported_state.get("program")

        if (
            current_program
            and hasattr(self.get_appliance, "data")
            and self.get_appliance.data
        ):
            appliance_data = self.get_appliance.data
            if hasattr(appliance_data, "capabilities") and appliance_data.capabilities:
                # Use normalized entity key to match program capabilities
                entity_key = self.entity_attr
                program_caps = (
                    appliance_data.capabilities.get("program", {})
                    .get("values", {})
                    .get(current_program, {})
                    .get(entity_key, {})
                )
                if "min" in program_caps:
                    min_val = program_caps["min"]
                    if min_val is not None:
                        if self.unit == UnitOfTime.SECONDS:
                            min_val = time_seconds_to_minutes(cast(float, min_val))
                        elif self.unit == UnitOfTime.MINUTES:
                            # Program capabilities are in seconds, convert to minutes
                            min_val = time_seconds_to_minutes(cast(float, min_val))
                        return float(cast(float, min_val))

        # Fallback to global capability
        if self.unit == UnitOfTime.SECONDS:
            min_val = time_seconds_to_minutes(self.capability.get("min", 0))
            return float(min_val) if min_val is not None else 0.0
        return float(self.capability.get("min", 0))

    @property
    def native_step(self) -> float:
        """Return the step value."""
        # Check current program for program-specific constraints
        current_program = self.reported_state.get("program")

        if (
            current_program
            and hasattr(self.get_appliance, "data")
            and self.get_appliance.data
        ):
            appliance_data = self.get_appliance.data
            if hasattr(appliance_data, "capabilities") and appliance_data.capabilities:
                # Use normalized entity key to match program capabilities
                entity_key = self.entity_attr
                program_caps = (
                    appliance_data.capabilities.get("program", {})
                    .get("values", {})
                    .get(current_program, {})
                    .get(entity_key, {})
                )
                if "step" in program_caps:
                    step_val = program_caps["step"]
                    if step_val is not None:
                        if self.unit == UnitOfTime.SECONDS:
                            step_val = time_seconds_to_minutes(cast(float, step_val))
                        elif self.unit == UnitOfTime.MINUTES:
                            # Program capabilities are in seconds, convert to minutes
                            step_val = time_seconds_to_minutes(cast(float, step_val))
                        return float(cast(float, step_val))

        # Fallback to global capability
        if self.unit == UnitOfTime.SECONDS:
            step_val = time_seconds_to_minutes(self.capability.get("step", 1))
            return float(step_val) if step_val is not None else 1.0
        if self.unit == UnitOfTemperature.CELSIUS:
            return float(self.capability.get("step", 1))
        return float(self.capability.get("step", 1))

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        # Prevent setting values for unsupported programs
        if not self._is_supported_by_program():
            # Special error message for food probe when not inserted
            if self.entity_attr == "targetFoodProbeTemperatureC":
                food_probe_state = self.reported_state.get("foodProbeInsertionState")
                if food_probe_state == "NOT_INSERTED":
                    _LOGGER.warning(
                        "Cannot set %s for appliance %s: food probe is not inserted",
                        self.entity_attr,
                        self.pnc_id,
                    )
                    raise HomeAssistantError(
                        "Food probe must be inserted to set target temperature"
                    )

            _LOGGER.warning(
                "Cannot set %s for appliance %s: not supported by current program",
                self.entity_attr,
                self.pnc_id,
            )
            raise HomeAssistantError(
                f"Control '{self.entity_attr}' is not supported by the current program"
            )

        # Check if remote control is enabled
        remote_control = (
            self.appliance_status.get("properties", {})
            .get("reported", {})
            .get("remoteControl")
        )
        _LOGGER.debug(
            "Number control remote control check for %s: status=%s",
            self.entity_attr,
            remote_control,
        )
        # Check for disabled states
        if remote_control is not None and (
            "ENABLED" not in str(remote_control) or "DISABLED" in str(remote_control)
        ):
            _LOGGER.warning(
                "Cannot set %s for appliance %s: remote control is %s",
                self.entity_attr,
                self.pnc_id,
                remote_control,
            )
            raise HomeAssistantError(
                f"Remote control disabled (status: {remote_control})"
            )

        if self.native_unit_of_measurement == UnitOfTime.MINUTES:
            converted = time_minutes_to_seconds(value)
            value = float(converted) if converted is not None else value
        if self.capability.get("step", 1) == 1:
            value = int(value)

        client: ElectroluxApiClient = self.api

        # Format the value according to appliance capabilities
        formatted_value = format_command_for_appliance(
            self.capability, self.entity_attr, value
        )

        # Apply program-specific constraints (min, max, step) that may differ from global capabilities
        min_val = self.native_min_value
        max_val = self.native_max_value
        step_val = self.native_step

        # Convert to seconds for time-based entities since formatted_value is in seconds
        if self.unit == UnitOfTime.SECONDS:
            if min_val is not None:
                min_val = time_minutes_to_seconds(min_val)
            if max_val is not None:
                max_val = time_minutes_to_seconds(max_val)
            if step_val is not None:
                step_val = time_minutes_to_seconds(step_val)
        elif self.unit == UnitOfTime.MINUTES:
            if min_val is not None:
                min_val = time_minutes_to_seconds(min_val)
            if max_val is not None:
                max_val = time_minutes_to_seconds(max_val)
            if step_val is not None:
                step_val = time_minutes_to_seconds(step_val)

        # Clamp to current min/max bounds
        if min_val is not None:
            formatted_value = max(formatted_value, min_val)
        if max_val is not None:
            formatted_value = min(formatted_value, max_val)

        # Round to nearest valid step
        if step_val is not None and step_val > 0:
            step_base = min_val if min_val is not None else 0
            steps_from_base = (formatted_value - step_base) / step_val
            formatted_value = step_base + round(steps_from_base) * step_val

        # Update cached value with the constrained value for immediate UI feedback
        if self.unit == UnitOfTime.SECONDS:
            self._cached_value = time_seconds_to_minutes(formatted_value)
        else:
            self._cached_value = formatted_value

        # --- START OF OUR FIX ---
        command = {}
        if self.entity_source == "latamUserSelections":
            _LOGGER.debug(
                "Electrolux: Detected latamUserSelections, building full command."
            )
            # Get the current state of all latam selections
            current_selections = (
                self.appliance_status.get("properties", {})
                .get("reported", {})
                .get("latamUserSelections", {})
            )
            if not current_selections:
                _LOGGER.error(
                    "Could not retrieve current latamUserSelections to build command."
                )
                return

            # Create a copy to modify
            new_selections = current_selections.copy()
            # Update only the value we want to change
            new_selections[self.entity_attr] = formatted_value
            # Assemble the final command with the entire block
            command = {"latamUserSelections": new_selections}
        # --- END OF OUR FIX ---

        # Original logic as a fallback for other entities
        elif self.entity_source == "userSelections":
            # Safer access to avoid KeyError if userSelections is missing
            reported = self.appliance_status.get("properties", {}).get("reported", {})
            program_uid = reported.get("userSelections", {}).get("programUID")
            command = {
                self.entity_source: {
                    "programUID": program_uid,
                    self.entity_attr: formatted_value,
                },
            }
        elif self.entity_source:
            command = {self.entity_source: {self.entity_attr: formatted_value}}
        else:
            command = {self.entity_attr: formatted_value}

        _LOGGER.debug("Electrolux set value %s", command)
        try:
            result = await client.execute_appliance_command(self.pnc_id, command)
        except AuthenticationError as auth_ex:
            # Handle authentication errors by triggering reauthentication
            await self.coordinator.handle_authentication_error(auth_ex)
        except Exception as ex:
            # Use shared error mapping for all errors
            raise map_command_error_to_home_assistant_error(
                ex, self.entity_attr, _LOGGER, self.capability
            ) from ex
        _LOGGER.debug("Electrolux set value result %s", result)
        await self.coordinator.async_request_refresh()

        # For temperature changes, the appliance may change program asynchronously
        # Add a delay and refresh again to ensure program state is updated
        if "temperature" in self.entity_attr.lower():
            await asyncio.sleep(2)
            await self.coordinator.async_request_refresh()

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement."""
        if self.unit == UnitOfTime.SECONDS:
            return UnitOfTime.MINUTES
        return self.unit

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        # Food probe temperature is only available when probe is inserted
        if self.entity_attr == "targetFoodProbeTemperatureC":
            food_probe_state = self.reported_state.get("foodProbeInsertionState")
            if food_probe_state == "NOT_INSERTED":
                return False
        # Always available for other entities - unsupported programs show "NA" instead
        return True

    def _is_supported_by_program(self) -> bool:
        """Check if the entity is supported by the current program."""
        current_program = self.reported_state.get("program")
        if not current_program:
            return True  # If no program, assume supported

        # Check if the appliance has program-specific capabilities
        if not (hasattr(self.get_appliance, "data") and self.get_appliance.data):
            return True

        appliance_data = self.get_appliance.data
        if not (
            hasattr(appliance_data, "capabilities") and appliance_data.capabilities
        ):
            return True

        program_caps = (
            appliance_data.capabilities.get("program", {})
            .get("values", {})
            .get(current_program, {})
        )

        # If the entity is not in the program capabilities, it's not supported
        if self.entity_attr not in program_caps:
            return False

        # Start with the base disabled state from program capabilities
        entity_cap = program_caps[self.entity_attr]
        disabled = False
        if isinstance(entity_cap, dict):
            disabled = entity_cap.get("disabled", False)

        # Process triggers that affect this entity
        all_capabilities = appliance_data.capabilities
        for cap_name, cap_def in all_capabilities.items():
            if isinstance(cap_def, dict) and "triggers" in cap_def:
                for trigger in cap_def["triggers"]:
                    if isinstance(trigger, dict) and "action" in trigger:
                        action = trigger["action"]
                        # Check if this trigger affects our entity
                        if self.entity_attr in action:
                            # Check if the condition is met
                            if self._evaluate_trigger_condition(
                                trigger.get("condition", {})
                            ):
                                # Apply the action
                                entity_action = action[self.entity_attr]
                                if (
                                    isinstance(entity_action, dict)
                                    and "disabled" in entity_action
                                ):
                                    disabled = entity_action["disabled"]
                                    _LOGGER.debug(
                                        "Trigger applied to %s: disabled=%s (trigger from %s)",
                                        self.entity_attr,
                                        disabled,
                                        cap_name,
                                    )

        # If disabled by triggers or program settings, not supported
        if disabled:
            return False

        # Special check for food probe temperature: only available if probe is inserted
        if self.entity_attr == "targetFoodProbeTemperatureC":
            food_probe_state = self.reported_state.get("foodProbeInsertionState")
            if food_probe_state == "NOT_INSERTED":
                return False

        return True

    def _evaluate_trigger_condition(self, condition: dict) -> bool:
        """Evaluate a trigger condition."""
        if not condition:
            return True

        operator = condition.get("operator", "eq")
        operand1 = condition.get("operand_1")
        operand2 = condition.get("operand_2")

        # Handle nested operands
        if isinstance(operand1, dict):
            operand1 = self._evaluate_operand(operand1)
        if isinstance(operand2, dict):
            operand2 = self._evaluate_operand(operand2)

        # Evaluate based on operator
        if operator == "eq":
            return operand1 == operand2
        elif operator == "and":
            return bool(operand1) and bool(operand2)
        elif operator == "or":
            return bool(operand1) or bool(operand2)

        return False

    def _evaluate_operand(self, operand: dict) -> Any:
        """Evaluate a trigger operand."""
        if "operand_1" in operand and "operand_2" in operand:
            # This is a nested condition
            return self._evaluate_trigger_condition(operand)
        elif "operand_1" in operand:
            # Reference to another capability
            cap_name = operand["operand_1"]
            if cap_name == "value":
                # Special case: refers to the capability that has the trigger
                # For now, return the current value of the capability that has the trigger
                # This is complex to implement fully, so let's use a simpler approach
                return self.reported_state.get(cap_name)
            else:
                # Get the value from reported state
                return self.reported_state.get(cap_name)
        else:
            # Literal value
            return operand.get("value")

    @property
    def state(self) -> str:
        """Return the state of the entity."""
        if not self._is_supported_by_program():
            return "NA"
        # Use the default state behavior for supported programs
        if (native_val := self.native_value) is not None:
            return str(native_val)
        return "unavailable"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Return if the entity should be enabled when first added to the entity registry."""
        # Always enable entities by default - availability is controlled by the available property
        return True
