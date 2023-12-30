# Demo Data Platform API/Lib
Tools to:
- super objectively interact with the steam API to collect data about servers + players
- facilitate collecting and serving demo data
- API to wrap everything together


Installation and Development:

This project uses [PDM](https://pdm-project.org/latest/) for development.

Note that it can still be installed with `pip` into an existing project from source.


clone and install:

```sh
git clone https://github.com/MegaAntiCheat/api.git
cd api
pdm sync

# `pdm sync -G:all` for development dependencies
```


## Steam API

First one needs to make their steam API available. You can just use it in function calls like below:

```py
from api.servers import Query


filters = {
    "appid": 440,
    "empty": False,
    "linux": True,
    "gametype": "valve"
}

limit = 1

servers = Query("MY_STEAM_API_KEY", filters, limit).query()

for server in servers:
    print(server)

    server_info = server.query("MY_STEAM_API_KEY")
    print(server_info)
```
Results in:
```json
addr='169.254.189.228:41928' gameport=41928 steamid='90178913460028417' name='Valve Matchmaking Server (Washington srcds1002-eat1 #94)' appid=440 gamedir='tf' version='8604597' product='tf' region=255 players=24 max_players=32 bots=0 map='pl_pier' secure=True dedicated=True os='l' gametype='hidden,increased_maxplayers,payload,valve' URL='https://api.steampowered.com/IGameServersService/QueryByFakeIP/v1/' QUERY_TYPES={1: 'ping_data', 2: 'players_data', 3: 'rules_data'}
{
    "ping_data": {
        "server_ip": {
            "v4": 2852044260
        },
        "query_port": 41928,
        "game_port": 41928,
        "server_name": "Valve Matchmaking Server (Washington srcds1002-eat1 #94)",
        "steamid": "90178913460028417",
        "app_id": 440,
        "gamedir": "tf",
        "map": "pl_pier",
        "game_description": "Team Fortress",
        "gametype": "hidden,increased_maxplayers,payload,valve",
        "num_players": 23,
        "max_players": 32,
        "num_bots": 0,
        "password": false,
        "secure": true,
        "dedicated": true,
        "version": "8604597",
        "sdr_popid": 7173992
    },
    "players_data": {
        "players": [
            {
                "name": "Javaris Jamar Javarison-Lamar",
                "score": 0,
                "time_played": 3079
            },
            {
                "name": "DamitriusDamarcusBartholamyJame",
                "score": 1,
                "time_played": 2733
            },
            {
                "name": "joe",
                "score": 1,
                "time_played": 1800
            },
            {
                "name": "soysauce20001",
                "score": 2,
                "time_played": 1302
            },
            {
                "name": "Buhda",
                "score": 4,
                "time_played": 1153
            },
            {
                "name": "[LZBZ]Dejeezus",
                "score": 0,
                "time_played": 556
            },
            {
                "name": "Zenshure",
                "score": 1,
                "time_played": 425
            },
            {
                "name": "War Pilot Snoopy",
                "score": 2,
                "time_played": 272
            },
            {
                "name": "who",
                "score": 2,
                "time_played": 271
            },
            {
                "name": "freefish",
                "score": 1,
                "time_played": 228
            },
            {
                "name": "cosmic goose",
                "score": 2,
                "time_played": 216
            },
            {
                "name": "Booger (cool)",
                "score": 1,
                "time_played": 201
            },
            {
                "name": "sklink97",
                "score": 2,
                "time_played": 156
            },
            {
                "name": "Kid behind the mask",
                "score": 1,
                "time_played": 156
            },
            {
                "name": "Bo Stunkulus",
                "score": 4,
                "time_played": 118
            },
            {
                "name": "Festive SkeleTom",
                "score": 0,
                "time_played": 109
            },
            {
                "name": "atroyt",
                "score": 0,
                "time_played": 95
            },
            {
                "name": "mudasir",
                "score": 1,
                "time_played": 95
            },
            {
                "name": "MuburTheGuide",
                "score": 0,
                "time_played": 76
            },
            {
                "name": "Zombie cleo",
                "score": 3,
                "time_played": 69
            },
            {
                "name": "Xbox 360",
                "score": 0,
                "time_played": 40
            },
            {
                "name": "SapTheDoc",
                "score": 0,
                "time_played": 31
            },
            {
                "name": "Wendigo",
                "score": 0,
                "time_played": 22
            }
        ]
    },
    "rules_data": {
        "rules": [
            {
                "rule": "coop",
                "value": "0"
            },
            {
                "rule": "deathmatch",
                "value": "1"
            },
            {
                "rule": "decalfrequency",
                "value": "10"
            },
            {
                "rule": "mp_allowNPCs",
                "value": "1"
            },
            {
                "rule": "mp_autocrosshair",
                "value": "1"
            },
            {
                "rule": "mp_autoteambalance",
                "value": "1"
            },
            {
                "rule": "mp_disable_respawn_times",
                "value": "0"
            },
            {
                "rule": "mp_fadetoblack",
                "value": "0"
            },
            {
                "rule": "mp_falldamage",
                "value": "0"
            },
            {
                "rule": "mp_flashlight",
                "value": "0"
            },
            {
                "rule": "mp_footsteps",
                "value": "1"
            },
            {
                "rule": "mp_forceautoteam",
                "value": "1"
            },
            {
                "rule": "mp_forcerespawn",
                "value": "1"
            },
            {
                "rule": "mp_fraglimit",
                "value": "0"
            },
            {
                "rule": "mp_friendlyfire",
                "value": "0"
            },
            {
                "rule": "mp_highlander",
                "value": "0"
            },
            {
                "rule": "mp_holiday_nogifts",
                "value": "0"
            },
            {
                "rule": "mp_match_end_at_timelimit",
                "value": "0"
            },
            {
                "rule": "mp_maxrounds",
                "value": "2"
            },
            {
                "rule": "mp_respawnwavetime",
                "value": "10.0"
            },
            {
                "rule": "mp_scrambleteams_auto",
                "value": "1"
            },
            {
                "rule": "mp_scrambleteams_auto_windifference",
                "value": "2"
            },
            {
                "rule": "mp_stalemate_enable",
                "value": "0"
            },
            {
                "rule": "mp_stalemate_meleeonly",
                "value": "0"
            },
            {
                "rule": "mp_teamlist",
                "value": "hgrunt;scientist"
            },
            {
                "rule": "mp_teamplay",
                "value": "0"
            },
            {
                "rule": "mp_timelimit",
                "value": "0"
            },
            {
                "rule": "mp_tournament",
                "value": "1"
            },
            {
                "rule": "mp_tournament_readymode",
                "value": "1"
            },
            {
                "rule": "mp_tournament_readymode_countdown",
                "value": "10"
            },
            {
                "rule": "mp_tournament_readymode_min",
                "value": "0"
            },
            {
                "rule": "mp_tournament_readymode_team_size",
                "value": "0"
            },
            {
                "rule": "mp_tournament_stopwatch",
                "value": "0"
            },
            {
                "rule": "mp_weaponstay",
                "value": "0"
            },
            {
                "rule": "mp_windifference",
                "value": "0"
            },
            {
                "rule": "mp_windifference_min",
                "value": "0"
            },
            {
                "rule": "mp_winlimit",
                "value": "0"
            },
            {
                "rule": "nextlevel",
                "value": ""
            },
            {
                "rule": "r_AirboatViewDampenDamp",
                "value": "1.0"
            },
            {
                "rule": "r_AirboatViewDampenFreq",
                "value": "7.0"
            },
            {
                "rule": "r_AirboatViewZHeight",
                "value": "0.0"
            },
            {
                "rule": "r_JeepViewDampenDamp",
                "value": "1.0"
            },
            {
                "rule": "r_JeepViewDampenFreq",
                "value": "7.0"
            },
            {
                "rule": "r_JeepViewZHeight",
                "value": "10.0"
            },
            {
                "rule": "r_VehicleViewDampen",
                "value": "1"
            },
            {
                "rule": "sv_accelerate",
                "value": "10"
            },
            {
                "rule": "sv_airaccelerate",
                "value": "10"
            },
            {
                "rule": "sv_alltalk",
                "value": "0"
            },
            {
                "rule": "sv_bounce",
                "value": "0"
            },
            {
                "rule": "sv_cheats",
                "value": "0"
            },
            {
                "rule": "sv_contact",
                "value": ""
            },
            {
                "rule": "sv_footsteps",
                "value": "1"
            },
            {
                "rule": "sv_friction",
                "value": "4"
            },
            {
                "rule": "sv_gravity",
                "value": "800"
            },
            {
                "rule": "sv_maxspeed",
                "value": "320"
            },
            {
                "rule": "sv_maxusrcmdprocessticks",
                "value": "24"
            },
            {
                "rule": "sv_noclipaccelerate",
                "value": "5"
            },
            {
                "rule": "sv_noclipspeed",
                "value": "5"
            },
            {
                "rule": "sv_password",
                "value": "0"
            },
            {
                "rule": "sv_pausable",
                "value": "0"
            },
            {
                "rule": "sv_registration_message",
                "value": "No account specified"
            },
            {
                "rule": "sv_registration_successful",
                "value": "0"
            },
            {
                "rule": "sv_rollangle",
                "value": "0"
            },
            {
                "rule": "sv_rollspeed",
                "value": "200"
            },
            {
                "rule": "sv_specaccelerate",
                "value": "5"
            },
            {
                "rule": "sv_specnoclip",
                "value": "1"
            },
            {
                "rule": "sv_specspeed",
                "value": "3"
            },
            {
                "rule": "sv_steamgroup",
                "value": ""
            },
            {
                "rule": "sv_stepsize",
                "value": "18"
            },
            {
                "rule": "sv_stopspeed",
                "value": "100"
            },
            {
                "rule": "sv_tags",
                "value": "hidden,increased_maxplayers,payload,valve"
            },
            {
                "rule": "sv_voiceenable",
                "value": "1"
            },
            {
                "rule": "sv_vote_quorum_ratio",
                "value": "0.6"
            },
            {
                "rule": "sv_wateraccelerate",
                "value": "10"
            },
            {
                "rule": "sv_waterfriction",
                "value": "1"
            },
            {
                "rule": "tf_allow_player_name_change",
                "value": "0"
            },
            {
                "rule": "tf_allow_player_use",
                "value": "0"
            },
            {
                "rule": "tf_arena_change_limit",
                "value": "1"
            },
            {
                "rule": "tf_arena_first_blood",
                "value": "1"
            },
            {
                "rule": "tf_arena_force_class",
                "value": "0"
            },
            {
                "rule": "tf_arena_max_streak",
                "value": "3"
            },
            {
                "rule": "tf_arena_override_cap_enable_time",
                "value": "-1"
            },
            {
                "rule": "tf_arena_preround_time",
                "value": "10"
            },
            {
                "rule": "tf_arena_round_time",
                "value": "0"
            },
            {
                "rule": "tf_arena_use_queue",
                "value": "1"
            },
            {
                "rule": "tf_beta_content",
                "value": "0"
            },
            {
                "rule": "tf_birthday",
                "value": "0"
            },
            {
                "rule": "tf_bot_count",
                "value": "0"
            },
            {
                "rule": "tf_classlimit",
                "value": "0"
            },
            {
                "rule": "tf_ctf_bonus_time",
                "value": "10"
            },
            {
                "rule": "tf_damage_disablespread",
                "value": "1"
            },
            {
                "rule": "tf_force_holidays_off",
                "value": "0"
            },
            {
                "rule": "tf_gamemode_arena",
                "value": "0"
            },
            {
                "rule": "tf_gamemode_community",
                "value": "0"
            },
            {
                "rule": "tf_gamemode_cp",
                "value": "0"
            },
            {
                "rule": "tf_gamemode_ctf",
                "value": "0"
            },
            {
                "rule": "tf_gamemode_misc",
                "value": "0"
            },
            {
                "rule": "tf_gamemode_mvm",
                "value": "0"
            },
            {
                "rule": "tf_gamemode_passtime",
                "value": "0"
            },
            {
                "rule": "tf_gamemode_payload",
                "value": "1"
            },
            {
                "rule": "tf_gamemode_pd",
                "value": "0"
            },
            {
                "rule": "tf_gamemode_rd",
                "value": "0"
            },
            {
                "rule": "tf_gamemode_sd",
                "value": "0"
            },
            {
                "rule": "tf_gamemode_tc",
                "value": "0"
            },
            {
                "rule": "tf_gravetalk",
                "value": "1"
            },
            {
                "rule": "tf_halloween_allow_truce_during_boss_event",
                "value": "0"
            },
            {
                "rule": "tf_max_charge_speed",
                "value": "750"
            },
            {
                "rule": "tf_medieval",
                "value": "0"
            },
            {
                "rule": "tf_medieval_autorp",
                "value": "1"
            },
            {
                "rule": "tf_mm_servermode",
                "value": "1"
            },
            {
                "rule": "tf_mm_strict",
                "value": "1"
            },
            {
                "rule": "tf_mm_trusted",
                "value": "1"
            },
            {
                "rule": "tf_mvm_death_penalty",
                "value": "0"
            },
            {
                "rule": "tf_mvm_defenders_team_size",
                "value": "6"
            },
            {
                "rule": "tf_mvm_min_players_to_start",
                "value": "3"
            },
            {
                "rule": "tf_overtime_nag",
                "value": "0"
            },
            {
                "rule": "tf_passtime_ball_damping_scale",
                "value": "0.01f"
            },
            {
                "rule": "tf_passtime_ball_drag_coefficient",
                "value": "0.01f"
            },
            {
                "rule": "tf_passtime_ball_inertia_scale",
                "value": "1.0f"
            },
            {
                "rule": "tf_passtime_ball_mass",
                "value": "1.0f"
            },
            {
                "rule": "tf_passtime_ball_model",
                "value": "models/passtime/ball/passtime_ball.mdl"
            },
            {
                "rule": "tf_passtime_ball_reset_time",
                "value": "15"
            },
            {
                "rule": "tf_passtime_ball_rotdamping_scale",
                "value": "1.0f"
            },
            {
                "rule": "tf_passtime_ball_seek_range",
                "value": "128"
            },
            {
                "rule": "tf_passtime_ball_seek_speed_factor",
                "value": "3f"
            },
            {
                "rule": "tf_passtime_ball_sphere_collision",
                "value": "1"
            },
            {
                "rule": "tf_passtime_ball_sphere_radius",
                "value": "7.2f"
            },
            {
                "rule": "tf_passtime_ball_takedamage",
                "value": "1"
            },
            {
                "rule": "tf_passtime_ball_takedamage_force",
                "value": "800.0f"
            },
            {
                "rule": "tf_passtime_experiment_autopass",
                "value": "0"
            },
            {
                "rule": "tf_passtime_experiment_instapass",
                "value": "0"
            },
            {
                "rule": "tf_passtime_experiment_instapass_charge",
                "value": "0"
            },
            {
                "rule": "tf_passtime_experiment_telepass",
                "value": "0"
            },
            {
                "rule": "tf_passtime_flinch_boost",
                "value": "0"
            },
            {
                "rule": "tf_passtime_mode_homing_lock_sec",
                "value": "1.5f"
            },
            {
                "rule": "tf_passtime_mode_homing_speed",
                "value": "1000.0f"
            },
            {
                "rule": "tf_passtime_overtime_idle_sec",
                "value": "5"
            },
            {
                "rule": "tf_passtime_pack_hp_per_sec",
                "value": "2.0f"
            },
            {
                "rule": "tf_passtime_pack_range",
                "value": "512"
            },
            {
                "rule": "tf_passtime_pack_speed",
                "value": "1"
            },
            {
                "rule": "tf_passtime_player_reticles_enemies",
                "value": "1"
            },
            {
                "rule": "tf_passtime_player_reticles_friends",
                "value": "2"
            },
            {
                "rule": "tf_passtime_powerball_airtimebonus",
                "value": "40"
            },
            {
                "rule": "tf_passtime_powerball_decayamount",
                "value": "1"
            },
            {
                "rule": "tf_passtime_powerball_decaysec",
                "value": "4.5f"
            },
            {
                "rule": "tf_passtime_powerball_decaysec_neutral",
                "value": "1.5f"
            },
            {
                "rule": "tf_passtime_powerball_decay_delay",
                "value": "10"
            },
            {
                "rule": "tf_passtime_powerball_maxairtimebonus",
                "value": "100"
            },
            {
                "rule": "tf_passtime_powerball_passpoints",
                "value": "25"
            },
            {
                "rule": "tf_passtime_powerball_threshold",
                "value": "80"
            },
            {
                "rule": "tf_passtime_save_stats",
                "value": "0"
            },
            {
                "rule": "tf_passtime_scores_per_round",
                "value": "5"
            },
            {
                "rule": "tf_passtime_score_crit_sec",
                "value": "5.0f"
            },
            {
                "rule": "tf_passtime_speedboost_on_get_ball_time",
                "value": "2.0f"
            },
            {
                "rule": "tf_passtime_steal_on_melee",
                "value": "1"
            },
            {
                "rule": "tf_passtime_teammate_steal_time",
                "value": "45"
            },
            {
                "rule": "tf_passtime_throwarc_demoman",
                "value": "0.15f"
            },
            {
                "rule": "tf_passtime_throwarc_engineer",
                "value": "0.2f"
            },
            {
                "rule": "tf_passtime_throwarc_heavy",
                "value": "0.175f"
            },
            {
                "rule": "tf_passtime_throwarc_medic",
                "value": "0.0f"
            },
            {
                "rule": "tf_passtime_throwarc_pyro",
                "value": "0.1f"
            },
            {
                "rule": "tf_passtime_throwarc_scout",
                "value": "0.1f"
            },
            {
                "rule": "tf_passtime_throwarc_sniper",
                "value": "0.0f"
            },
            {
                "rule": "tf_passtime_throwarc_soldier",
                "value": "0.1f"
            },
            {
                "rule": "tf_passtime_throwarc_spy",
                "value": "0.0f"
            },
            {
                "rule": "tf_passtime_throwspeed_demoman",
                "value": "850.0f"
            },
            {
                "rule": "tf_passtime_throwspeed_engineer",
                "value": "850.0f"
            },
            {
                "rule": "tf_passtime_throwspeed_heavy",
                "value": "850.0f"
            },
            {
                "rule": "tf_passtime_throwspeed_medic",
                "value": "900.0f"
            },
            {
                "rule": "tf_passtime_throwspeed_pyro",
                "value": "750.0f"
            },
            {
                "rule": "tf_passtime_throwspeed_scout",
                "value": "700.0f"
            },
            {
                "rule": "tf_passtime_throwspeed_sniper",
                "value": "900.0f"
            },
            {
                "rule": "tf_passtime_throwspeed_soldier",
                "value": "800.0f"
            },
            {
                "rule": "tf_passtime_throwspeed_spy",
                "value": "900.0f"
            },
            {
                "rule": "tf_passtime_throwspeed_velocity_scale",
                "value": "0.33f"
            },
            {
                "rule": "tf_playergib",
                "value": "1"
            },
            {
                "rule": "tf_powerup_mode",
                "value": "0"
            },
            {
                "rule": "tf_server_identity_disable_quickplay",
                "value": "0"
            },
            {
                "rule": "tf_spawn_glows_duration",
                "value": "10"
            },
            {
                "rule": "tf_spec_xray",
                "value": "1"
            },
            {
                "rule": "tf_spells_enabled",
                "value": "0"
            },
            {
                "rule": "tf_use_fixed_weaponspreads",
                "value": "0"
            },
            {
                "rule": "tf_weapon_criticals",
                "value": "1"
            },
            {
                "rule": "tf_weapon_criticals_melee",
                "value": "1"
            },
            {
                "rule": "tv_enable",
                "value": "0"
            },
            {
                "rule": "tv_password",
                "value": "0"
            },
            {
                "rule": "tv_relaypassword",
                "value": "0"
            }
        ]
    }
}
```

One can also make their steam api key available through the helper methods in `src/api/auth.py` through a json, toml, or environment variable.


## Database API:
This is a Litestar API


## Database:

This is a postgres database with migrations/schemas managed by Alembic