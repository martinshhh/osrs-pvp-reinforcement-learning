package com.github.naton1.rl;

import com.elvarg.game.entity.impl.player.Player;
import com.elvarg.game.model.Location;
import com.github.naton1.rl.env.EnvironmentDescriptor;
import com.github.naton1.rl.env.nh.NhEnvironmentDescriptor;
import com.github.naton1.rl.env.nh.NhEnvironmentParams;
import java.util.ArrayList;
import java.util.List;
import lombok.Builder;
import lombok.Value;
import lombok.extern.slf4j.Slf4j;

@Slf4j
public class AgentBotLoader {

    private static final Location BASELINE_LOAD_TILE = new Location(3093, 3529);

    // Read the number of slots from the -DdataCollectionSlots flag. Defaults to 0.
    private static final int SLOTS = Integer.parseInt(
            System.getenv().getOrDefault("DATA_COLLECTION_SLOTS", System.getProperty("dataCollectionSlots", "0")));

    private final List<Player> loadedBots = new ArrayList<>();

    public void load() {
        if (SLOTS <= 0) return;

        List<BuildType> builds = List.of(
                new BuildType("Pure", NhEnvironmentParams.AccountBuild.PURE),
                new BuildType("Zerk", NhEnvironmentParams.AccountBuild.ZERKER),
                new BuildType("Max", NhEnvironmentParams.AccountBuild.MAXED));

        for (BuildType build : builds) {
            for (int i = 0; i < SLOTS; i++) {
                // Change the prefix to "Target"
                String botName = "Target" + build.label + "-" + i;

                log.info("Spawning BOT target: {}", botName);

                loadedBots.add(new AgentPlayerBot(
                        EnvConfig.getPredictionApiHost(),
                        EnvConfig.getPredictionApiPort(),
                        botName,
                        "FineTunedNh",
                        1,
                        new NhEnvironmentDescriptor(),
                        new NhEnvironmentParams().setAccountBuild(build.accountBuild),
                        false
                ));
            }
        }
    }

    public synchronized void unload() {
        loadedBots.forEach(Player::requestLogout);
        loadedBots.clear();
    }

    // Helper class to group our build labels and types
    @Value
    private static class BuildType {
        String label;
        NhEnvironmentParams.AccountBuild accountBuild;
    }

    @Builder
    @Value
    private static class AgentBotConfig<T> {
        private final String name;
        private final String model;
        private final int frameStack;
        private final EnvironmentDescriptor<T> environmentDescriptor;
        private final T environmentParams;
        private final boolean deterministic;
    }
}
