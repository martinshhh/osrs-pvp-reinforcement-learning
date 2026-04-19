"""
collect_data.py
===============
Runs N parallel fights simultaneously, all logging to the same CSV.

Each env gets its own dedicated bot slot (e.g. FineTunedNhPure-0,
FineTunedNhZerk-1) so fights never collide in the sim. Requires
AgentBotLoader.java to have MAX_SLOTS instances per build registered.

Usage:
    python collect_data.py                          # 4 parallel fights, 300 episodes total
    python collect_data.py --num-envs 4 --num-episodes 600
    python collect_data.py --no-run-simulation      # connect to already-running sim
"""

import argparse
import asyncio
import logging
import random
import threading

import torch as th

from pvp_ml.env.pvp_env import PvpEnv
from pvp_ml.env.simulation import Simulation
from pvp_ml.scripted.plugins.sim_data_collector import SimDataCollectorPlugin

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-8s %(message)s",
    datefmt="%Y-%b-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BUILDS = [
    "FineTunedNhPure",
    "FineTunedNhZerk",
    "FineTunedNhMax"]

_BUILD_FOR_TARGET = {
    "FineTunedNhPure": "PURE",
    "FineTunedNhZerk": "ZERKER",
    "FineTunedNhMax":  "MAXED",
}

def _slot_target(build: str, slot: int) -> str:
    """e.g. slot=1, build='FineTunedNhZerk' → 'FineTunedNhZerk-1'"""
    return f"{build}-{slot}"


class RandomTargetPvpEnv(PvpEnv):
    """PvpEnv that picks a build and ensures BOTH bots use that build. Pure-Pure,Zerk-Zerk,Max-Max"""

    def __init__(self, slot: int, **kwargs):
        super().__init__(**kwargs)
        self._slot = slot

    async def reset_async(self, *, seed=None, options=None):
        # Pick the build for this episode
        build_label = random.choice(BUILDS)

        # Set the target (the bot on the server)
        self._target = _slot_target(build_label, self._slot)

        # 2. Set the Agent's OWN build to match the target
        self._reset_params["accountBuild"] = _BUILD_FOR_TARGET[build_label]

        logger.info(f"[{self._env_id}] Symmetric Match: {self._reset_params['accountBuild']} vs {self._target}")

        return await super().reset_async(seed=seed, options=options)

def make_env(slot: int, port: int, plugin: SimDataCollectorPlugin) -> RandomTargetPvpEnv:
    from pvp_ml.scripted.script_plugin_adapter import ScriptPluginAdapter
    from pvp_ml.util.contract_loader import load_environment_contract
    meta = load_environment_contract("NhEnv")
    adapter = ScriptPluginAdapter(plugin, "NhEnv", meta)

    env = RandomTargetPvpEnv(
        slot=slot,
        env_name="NhEnv",
        env_id=f"collect-{slot}",
        target=_slot_target(BUILDS[0], slot),
        remote_environment_port=port,
        remote_environment_host="localhost",
        reset_params={"accountBuild": "PURE"},
    )
    env._adapter = adapter
    return env

def _agent_name(build: str, slot: int) -> str:
    return f"Agent{build.replace('FineTunedNh', '')}-{slot}"

def _target_name(build: str, slot: int) -> str:
    return f"Target{build.replace('FineTunedNh', '')}-{slot}"

class FixedTargetPvpEnv(PvpEnv):
    def __init__(self, build_label: str, slot: int, **kwargs):
        super().__init__(**kwargs)
        self._build_label = build_label
        self._slot = slot

    async def reset_async(self, *, seed=None, options=None):
        # This Agent (AgentPure-0) attacks this Target (TargetPure-0)
        self._target = _target_name(self._build_label, self._slot)
        self._reset_params["accountBuild"] = _BUILD_FOR_TARGET[self._build_label]
        return await super().reset_async(seed=seed, options=options)

def make_env(build_label: str, slot: int, port: int, plugin: SimDataCollectorPlugin) -> FixedTargetPvpEnv:
    from pvp_ml.scripted.script_plugin_adapter import ScriptPluginAdapter
    from pvp_ml.util.contract_loader import load_environment_contract
    meta = load_environment_contract("NhEnv")
    adapter = ScriptPluginAdapter(plugin, "NhEnv", meta)

    env = FixedTargetPvpEnv(
        build_label=build_label,
        slot=slot,
        env_name="NhEnv",
        # Give the Python Agent a unique login name
        env_id=_agent_name(build_label, slot),
        target=_target_name(build_label, slot),
        remote_environment_port=port,
        remote_environment_host="localhost",
        reset_params={"accountBuild": _BUILD_FOR_TARGET[build_label]},
    )
    env._adapter = adapter
    return env

async def run_env(
        env: RandomTargetPvpEnv,
        episodes_counter: list,
        counter_lock: asyncio.Lock,
) -> None:
    adapter = env._adapter
    try:
        while True:
            async with counter_lock:
                if episodes_counter[0] >= episodes_counter[1]:
                    return
            obs, _ = await env.reset_async()
            while True:
                masks = env.get_action_masks()
                action = adapter.predict(
                    th.as_tensor(obs[None, :], device="cpu"),
                    th.as_tensor(masks[None, :], device="cpu"),
                )
                obs, _, done, _, _ = await env.step_async(action.cpu().numpy()[0])
                if done:
                    async with counter_lock:
                        episodes_counter[0] += 1
                        completed, total = episodes_counter
                    logger.info(f"[{env._env_id}] episode {completed}/{total} done")
                    break
    except Exception as e:
        logger.error(f"[{env._env_id}] error: {e}", exc_info=True)
    finally:
        await env.close_async()


async def run_all(envs: list, num_episodes: int) -> None:
    counter_lock = asyncio.Lock()
    episodes_counter = [0, num_episodes]
    await asyncio.gather(*[run_env(env, episodes_counter, counter_lock) for env in envs])


def _patch_csv_for_thread_safety(lock: threading.Lock) -> None:
    """Lock _flush_fight — the only method that writes to the file."""
    _orig = SimDataCollectorPlugin._flush_fight

    def _locked(self):
        with lock:
            _orig(self)

    SimDataCollectorPlugin._flush_fight = _locked


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect sim data with N parallel fights"
    )
    parser.add_argument("--num-episodes", type=int, default=300,
                        help="Total episodes across all envs")
    parser.add_argument("--fight-bots", type=bool, default=False)
    parser.add_argument("--num-envs", type=int, default=4)
    parser.add_argument("--simulation-port", type=int, default=43595)
    parser.add_argument("--remote-env-port", type=int, default=7070)
    parser.add_argument("--no-run-simulation", action="store_true")
    args = parser.parse_args()

    total_fights = args.num_envs * len(BUILDS)
    logger.info(f"Starting {total_fights} parallel fights ({args.num_envs} per build)")

    _patch_csv_for_thread_safety(threading.Lock())

    def _run(port: int) -> None:
        all_envs = []
        # Create N environments for every build
        for build in BUILDS:
            for i in range(args.num_envs):
                plugin = SimDataCollectorPlugin()
                env = make_env(build, i, port, plugin)
                all_envs.append(env)

        asyncio.run(run_all(all_envs, args.num_episodes))
    if args.no_run_simulation:
        _run(args.remote_env_port)
    else:
        with Simulation(
                game_port=args.simulation_port,
                remote_env_port=args.remote_env_port,
                sync_training=False,
                fight_bots=args.fight_bots, # Set to true to run simulation fights
                num_slots=args.num_envs # if fight_bots is set to true, this represents the amount of fights PER build (Pure, Zerk, Max)
        ) as sim:
            sim.wait_until_loaded()
            _run(sim.remote_env_port)

    logger.info("Collection complete. Ready to retrain:")
    logger.info("  cd kuri-prayer-predictor-api && python src/train.py --pattern")


if __name__ == "__main__":
    main()