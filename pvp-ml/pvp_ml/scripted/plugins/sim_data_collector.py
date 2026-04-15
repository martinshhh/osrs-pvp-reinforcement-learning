import csv
import json
import os
import random
from datetime import datetime
from typing import Any

from pvp_ml.scripted.plugins.prayer_predictor_plugin import PrayerPredictorPlugin
from pvp_ml.scripted.script_plugin import ScriptPlugin

OUTPUT_CSV = os.path.normpath(os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..", "..", "..", "Kuri",
    "kuri-prayer-predictor-api", "enemy_gear_log.csv",
))

# Load spec weapon constants from the single source of truth.
# combat_config.json lives in kuri-prayer-predictor-api and is shared with
# the Python training code and the Java plugin — edit it there, not here.
_CONFIG_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..", "..", "..", "Kuri",
    "kuri-prayer-predictor-api", "combat_config.json",
))
with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
    _cfg = json.load(_f)

_MAGIC_SPEC_WEAPONS = {int(k): v for k, v in _cfg["magic_spec_weapons"].items()}
_MELEE_SPEC_WEAPONS = {int(k): v for k, v in _cfg["melee_spec_weapons"].items()}
_LOW_HP_THRESHOLD = _cfg["low_hp_threshold"]

CSV_HEADER = [
    "tick", "weapon_id", "weapon_type", "body_id", "body_type",
    "legs_id", "legs_type", "helm_id", "helm_type",
    "shield_id", "shield_type", "distance", "is_frozen", "spec_energy_pct",
    "player_hp_pct",
]

SPEC_ENERGY_THRESHOLD = 0.5

_FARCAST_OPTIONS = ["farcast_4_tiles", "farcast_5_tiles", "farcast_6_tiles", "farcast_7_tiles"]

_PRAYER_MASKS = {
    "mage_prayer": lambda mage_prayer, **_: mage_prayer,
    "ranged_prayer": lambda ranged_prayer, **_: ranged_prayer,
    "melee_prayer": lambda melee_prayer, **_: melee_prayer,
}


class SimDataCollectorPlugin(ScriptPlugin):
    """Logs opponent gear to enemy_gear_log.csv and plays with real NH tactics.

    Each fight is buffered in memory and flushed atomically to the CSV on fight
    end, so parallel envs never interleave rows. Tactical decisions mirror a real
    NH player: freeze to farcast, spec on frozen targets, eat and pot to survive,
    and use PrayerPredictorPlugin for defensive prayers.
    """

    def __init__(self, env_id: str = "0") -> None:
        self._env_id = env_id
        self._tick = 0
        self._fight_active = False
        self._episodes = 0
        self._row_buffer: list[list] = []
        self._prayer_predictor = PrayerPredictorPlugin()
        print(f"[DC:{self._env_id}] CSV → {OUTPUT_CSV}")

    def predict(
            self,
            target_weapon_id: float = 0.0,
            target_body_id: float = 0.0,
            target_legs_id: float = 0.0,
            target_helm_id: float = 0.0,
            target_shield_id: float = 0.0,
            target_using_melee: bool = False,
            target_using_ranged: bool = False,
            target_using_mage: bool = False,
            target_frozen_ticks: float = 0.0,
            target_frozen_immunity_ticks: float = 0.0,
            player_frozen_ticks: float = 0.0,
            player_to_target_distance: float = 1.0,
            player_location_can_melee: bool = False,
            destination_to_target_distance: float = 0.0,
            special_energy_percent: float = 0.0,
            player_health_percent: float = 1.0,
            prayer_points: float = 1.0,
            ranged_level: float = 1.0,
            strength_level: float = 1.0,
            target_special_percent: float = 0.0,
            target_attack_cycle_ticks: float = 0.0,
            is_ice_magic_attack_available: bool = False,
            is_blood_magic_attack_available: bool = False,
            is_range_attack_available: bool = False,
            is_range_spec_attack_available: bool = False,
            is_melee_attack_available: bool = False,
            is_melee_spec_attack_available: bool = False,
            is_magic_spec_attack_available: bool = False,
            mage_attack: bool = False,
            ranged_attack: bool = False,
            melee_attack: bool = False,
            basic_ranged_attack: bool = False,
            ranged_special_attack: bool = False,
            basic_melee_attack: bool = False,
            melee_special_attack: bool = False,
            use_ice_spell: bool = False,
            use_blood_spell: bool = False,
            use_magic_spec: bool = False,
            is_melee_spec_dclaws: bool = False,
            is_melee_spec_ags: bool = False,
            is_melee_spec_dds: bool = False,
            is_ranged_spec_weapon_zaryte_cbow: bool = False,
            is_mage_spec_weapon_nightmare_staff: bool = False,
            is_mage_spec_weapon_loadout: bool = False,
            is_ranged_spec_weapon_loadout: bool = False,
            is_veng_active: bool = False,
            player_veng_cooldown_ticks: float = 0.0,
            use_veng: bool = False,
            move_next_to_target: bool = False,
            move_under_target: bool = False,
            move_to_farcast_tile: bool = False,
            move_diagonal_to_target: bool = False,
            mage_prayer: bool = False,
            ranged_prayer: bool = False,
            melee_prayer: bool = False,
            eat_primary_food: bool = False,
            eat_karambwan: bool = False,
            use_restore_potion: bool = False,
            use_combat_potion: bool = False,
            use_ranged_potion: bool = False,
            use_brew: bool = False,
            **kwargs: Any,
    ) -> dict[str, str]:

        target_present = target_using_melee or target_using_ranged or target_using_mage
        style = 2 if target_using_mage else (1 if target_using_ranged else 0)

        if target_present and not self._fight_active:
            self._fight_active = True
            self._tick = 0
            self._episodes += 1
            self._row_buffer = []
            self._prayer_predictor._fight_active = False
            print(f"[DC:{self._env_id}] Episode {self._episodes} started")
        elif not target_present and self._fight_active:
            self._fight_active = False
            self._flush_fight()
            print(f"[DC:{self._env_id}] Episode {self._episodes} ended — {self._tick} ticks")

        if player_health_percent <= 0 and self._fight_active:
            self._fight_active = False
            self._flush_fight()
            print(f"[DC:{self._env_id}] Episode {self._episodes} ended (died) — {self._tick} ticks")

        if self._fight_active and int(target_weapon_id) != 0:
            weapon_id = int(target_weapon_id)
            spec = int(target_special_percent)
            hp_pct = round(float(player_health_percent), 3)

            self._row_buffer.append([
                self._tick,
                weapon_id, style,
                int(target_body_id), style,
                int(target_legs_id), style,
                int(target_helm_id), 0,
                int(target_shield_id), 0,
                int(player_to_target_distance),
                1 if target_frozen_ticks > 0 else 0,
                spec,
                hp_pct
            ])
            self._tick += 1

        actions: dict[str, str] = {}

        target_is_frozen = target_frozen_ticks > 0
        target_can_be_frozen = target_frozen_immunity_ticks == 0
        we_are_frozen = player_frozen_ticks > 0
        distance = float(player_to_target_distance)
        has_spec = special_energy_percent >= SPEC_ENERGY_THRESHOLD

        if player_health_percent < 0.55 and eat_primary_food:
            actions["food"] = "eat_primary_food"
        if eat_karambwan and player_health_percent < 0.40:
            actions["karambwan"] = "eat_karambwan"
        if prayer_points < 0.45 and use_restore_potion:
            actions["potion"] = "use_restore_potion"
        elif ranged_level < 0.95 and use_ranged_potion:
            actions["potion"] = "use_ranged_potion"
        elif strength_level < 0.95 and use_combat_potion:
            actions["potion"] = "use_combat_potion"

        if use_veng and not is_veng_active and player_veng_cooldown_ticks == 0:
            actions["veng"] = "use_veng"

        prayer_action = self._prayer_predictor._predict_prayer(
            weapon_id=int(target_weapon_id), body_id=int(target_body_id),
            legs_id=int(target_legs_id), helm_id=int(target_helm_id),
            shield_id=int(target_shield_id), weapon_type=style,
            body_type=style, legs_type=style,
            cooldown=int(target_attack_cycle_ticks),
            distance=max(1, int(player_to_target_distance)),
            is_frozen=target_frozen_ticks > 0,
            spec_energy=int(target_special_percent),
            player_hp_pct=float(player_health_percent),
            fallback_style=style,
        )
        if prayer_action and _PRAYER_MASKS.get(prayer_action, lambda **_: False)(
                mage_prayer=mage_prayer, ranged_prayer=ranged_prayer, melee_prayer=melee_prayer):
            actions["prayer"] = prayer_action

        if not target_is_frozen:
            # Not frozen: close the gap and try to freeze
            if mage_attack and use_ice_spell and is_ice_magic_attack_available and target_can_be_frozen:
                actions["attack"] = "mage_attack"
                actions["mage_attack_type"] = "use_ice_spell"
            elif ranged_attack and basic_ranged_attack and is_range_attack_available:
                actions["attack"] = "ranged_attack"
                actions["ranged_attack_type"] = "basic_ranged_attack"
            elif melee_attack and basic_melee_attack and is_melee_attack_available:
                actions["attack"] = "melee_attack"
                actions["melee_attack_type"] = "basic_melee_attack"
            if distance > 1 and move_next_to_target:
                actions["movement"] = "move_next_to_target"

        elif not we_are_frozen:
            # Target frozen, we can move: farcast and spec if possible
            if move_to_farcast_tile:
                actions["movement"] = "move_to_farcast_tile"
                actions["farcast_distance"] = random.choice(_FARCAST_OPTIONS)
            if ranged_attack and ranged_special_attack and is_range_spec_attack_available and has_spec:
                actions["attack"] = "ranged_attack"
                actions["ranged_attack_type"] = "ranged_special_attack"
            elif melee_attack and melee_special_attack and is_melee_spec_attack_available and has_spec and player_location_can_melee:
                actions["attack"] = "melee_attack"
                actions["melee_attack_type"] = "melee_special_attack"
            elif mage_attack and use_blood_spell and is_blood_magic_attack_available:
                actions["attack"] = "mage_attack"
                actions["mage_attack_type"] = "use_blood_spell"
            elif ranged_attack and basic_ranged_attack and is_range_attack_available:
                actions["attack"] = "ranged_attack"
                actions["ranged_attack_type"] = "basic_ranged_attack"

        else:
            # Both frozen: attack in place
            if ranged_attack and ranged_special_attack and is_range_spec_attack_available and has_spec:
                actions["attack"] = "ranged_attack"
                actions["ranged_attack_type"] = "ranged_special_attack"
            elif melee_attack and melee_special_attack and is_melee_spec_attack_available and has_spec and player_location_can_melee:
                actions["attack"] = "melee_attack"
                actions["melee_attack_type"] = "melee_special_attack"
            elif mage_attack and use_blood_spell and is_blood_magic_attack_available:
                actions["attack"] = "mage_attack"
                actions["mage_attack_type"] = "use_blood_spell"
            elif ranged_attack and basic_ranged_attack and is_range_attack_available:
                actions["attack"] = "ranged_attack"
                actions["ranged_attack_type"] = "basic_ranged_attack"
            elif melee_attack and basic_melee_attack and is_melee_attack_available:
                actions["attack"] = "melee_attack"
                actions["melee_attack_type"] = "basic_melee_attack"

        return actions

    def _flush_fight(self) -> None:
        """Appends the buffered fight to the CSV atomically, then clears the buffer.

        Writes a fight-start marker, a fresh header row, all buffered data rows,
        and a fight-end marker. The header is also written once at the top of a
        new file so the CSV is self-describing without the markers.
        """
        if not self._row_buffer:
            print(f"[DC:{self._env_id}] Episode {self._episodes} had 0 rows — skipping")
            return

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        is_new = not os.path.exists(OUTPUT_CSV) or os.path.getsize(OUTPUT_CSV) == 0
        os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)

        with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow(CSV_HEADER)
            f.write(f"--- NEW FIGHT STARTED @ {ts} ---\n")
            writer.writerow(CSV_HEADER)
            for row in self._row_buffer:
                writer.writerow(row)
            f.write(f"--- FIGHT ENDED @ {ts} ticks={len(self._row_buffer)} ---\n\n")

        print(f"[DC:{self._env_id}] Episode {self._episodes} → {len(self._row_buffer)} rows flushed")
        self._row_buffer = []

    def __del__(self) -> None:
        try:
            if self._fight_active and self._row_buffer:
                self._flush_fight()
        except Exception:
            pass
