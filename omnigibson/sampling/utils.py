import omnigibson as og
from omnigibson.objects import DatasetObject
from omnigibson.systems import MicroPhysicalParticleSystem, get_system
from bddl.activity import Conditions, evaluate_state
import numpy as np
import csv
import json
import os
import bddl
import gspread
import getpass
import copy
from omnigibson.macros import gm, macros

"""
1. gcloud auth login
2. gcloud auth application-default login
3. gcloud config set project lucid-inquiry-205018
4. gcloud iam service-accounts create cremebrule
5. gcloud iam service-accounts keys create key.json --iam-account=cremebrule@lucid-inquiry-205018.iam.gserviceaccount.com
6. mv key.json /home/cremebrule/.config/gcloud/key.json
"""

folder_path = os.path.dirname(os.path.abspath(__file__))

SAMPLING_SHEET_KEY = "1Vt5s3JrFZ6_iCkfzZr0eb9SBt2Pkzx3xxzb4wtjEaDI"
CREDENTIALS = os.environ.get("CREDENTIALS_FPATH", os.path.join(folder_path, "key.json"))
WORKSHEET = "GTC2024 - 5a2d64"
USER = getpass.getuser()

client = gspread.service_account(filename=CREDENTIALS)
worksheet = client.open_by_key(SAMPLING_SHEET_KEY).worksheet(WORKSHEET)

ACTIVITY_TO_ROW = {activity: i + 2 for i, activity in enumerate(worksheet.col_values(1)[1:])}

SCENE_INFO_FPATH = os.path.join(folder_path, "BEHAVIOR-1K Scenes.csv")
TASK_INFO_FPATH = os.path.join(folder_path, "BEHAVIOR-1K Tasks.csv")
SYNSET_INFO_FPATH = os.path.join(folder_path, "BEHAVIOR-1K Synsets.csv")


UNSUPPORTED_PREDICATES = {"broken", "assembled", "attached"}

# CAREFUL!! Only run this ONCE before starting sampling!!!
def write_activities_to_spreadsheet():
    valid_tasks_sorted = sorted(get_valid_tasks())
    n_tasks = len(valid_tasks_sorted)
    cell_list = worksheet.range(f"A{2}:A{2 + n_tasks - 1}")
    for cell, task in zip(cell_list, valid_tasks_sorted):
        cell.value = task
    worksheet.update_cells(cell_list)


# CAREFUL!! Only run this ONCE before starting sampling!!!
def write_scenes_to_spreadsheet():
    # Get scenes
    scenes_sorted = get_scenes()
    n_scenes = len(scenes_sorted)
    cell_list = worksheet.range(f"R{2}:R{2 + n_scenes - 1}")
    for cell, scene in zip(cell_list, scenes_sorted):
        cell.value = scene
    worksheet.update_cells(cell_list)


def validate_scene_can_be_sampled(scene):
    scenes_sorted = get_scenes()
    n_scenes = len(scenes_sorted)
    # Sanity check scene -- only scenes are allowed that whose user field is either:
    # (a) blank or (b) filled with USER
    # scene_user_list = worksheet.range(f"R{2}:S{2 + n_scenes - 1}")
    def get_user(val):
        return None if (len(val) == 1 or val[1] == "") else val[1]

    scene_user_mapping = {val[0]: get_user(val) for val in worksheet.get(f"T{2}:U{2 + n_scenes - 1}")}

    # Make sure scene is valid
    assert scene in scene_user_mapping, f"Got invalid scene name to sample: {scene}"

    # Assert user is None or is USER, else False
    scene_user = scene_user_mapping[scene]
    assert scene_user is None or scene_user == USER, \
        f"Cannot sample scene {scene} with user {USER}! Scene already has user: {scene_user}."

    # Fill in this value to reserve it
    idx = scenes_sorted.index(scene)
    scene_row = 2 + idx
    worksheet.update_acell(f"U{scene_row}", USER)

    return scene_row


def prune_unevaluatable_predicates(init_conditions):
    pruned_conditions = []
    for condition in init_conditions:
        if condition.body[0] in {"insource", "future", "real"}:
            continue
        pruned_conditions.append(condition)

    return pruned_conditions


def get_predicates(conds):
    preds = []
    if isinstance(conds, str):
        return preds
    assert isinstance(conds, list)
    contains_list = np.any([isinstance(ele, list) for ele in conds])
    if contains_list:
        for ele in conds:
            preds += get_predicates(ele)
    else:
        preds.append(conds[0])
    return preds


def get_subjects(conds):
    subjs = []
    if isinstance(conds, str):
        return subjs
    assert isinstance(conds, list)
    contains_list = np.any([isinstance(ele, list) for ele in conds])
    if contains_list:
        for ele in conds:
            subjs += get_subjects(ele)
    else:
        subjs.append(conds[1])
    return subjs


def get_rooms(conds):
    rooms = []
    if isinstance(conds, str):
        return rooms
    assert isinstance(conds, list)
    contains_list = np.any([isinstance(ele, list) for ele in conds])
    if contains_list:
        for ele in conds:
            rooms += get_rooms(ele)
    elif conds[0] == "inroom":
        rooms.append(conds[2])
    return rooms


def get_scenes():
    scenes = set()
    with open(SCENE_INFO_FPATH) as csvfile:
        reader = csv.reader(csvfile, delimiter=",", quotechar='"')
        for i, row in enumerate(reader):
            # Skip first row since it's the header
            if i == 0:
                continue
            scenes.add(row[0])

    return tuple(sorted(scenes))


def get_valid_tasks():
    return set(activity for activity in os.listdir(os.path.join(bddl.__path__[0], "activity_definitions")))


def get_notready_synsets():
    notready_synsets = set()
    with open(SYNSET_INFO_FPATH) as csvfile:
        reader = csv.reader(csvfile, delimiter=",", quotechar='"')
        for i, row in enumerate(reader):
            if i == 0:
                continue
            synset, status = row[:2]
            if status == "Not Ready":
                notready_synsets.add(synset)

    return notready_synsets


def parse_task_mapping(fpath):
    mapping = dict()
    rows = []
    with open(fpath) as csvfile:
        reader = csv.reader(csvfile, delimiter=",", quotechar='"')
        for row in reader:
            rows.append(row)

    notready_synsets = get_notready_synsets()

    for row in rows[1:]:
        activity_name = row[0].split("-")[0]

        # Skip any that is missing a synset
        required_synsets = set(list(entry.strip() for entry in row[1].split(","))[:-1])
        if len(notready_synsets.intersection(required_synsets)) > 0:
            continue

        # Write matched ready scenes
        ready_scenes = set(list(entry.strip() for entry in row[2].split(","))[:-1])

        # There's always a leading whitespace
        if len(ready_scenes) == 0:
            continue

        mapping[activity_name] = ready_scenes

    return mapping


def get_dns_activities():
    n_tasks = len(get_valid_tasks())
    return {val[0] for val in worksheet.get(f"A{2}:C{2 + n_tasks - 1}") if val[-1] is not None and str(val[-1]).lower() == "dns"}

def get_non_misc_activities():
    n_tasks = len(get_valid_tasks())
    return {val[0] for val in worksheet.get(f"A{2}:I{2 + n_tasks - 1}") if val[-1] == "-"}


def get_scene_compatible_activities(scene_model, mapping):
    return [activity for activity, scenes in mapping.items() if scene_model in scenes]

def _validate_object_state_stability(obj_name, obj_dict, strict=False):
    lin_vel_threshold = 0.001 if strict else 1.
    ang_vel_threshold = 0.005 if strict else np.pi
    joint_vel_threshold = 0.01 if strict else 1.
    # Check close to zero root link velocity
    for key, atol in zip(("lin_vel", "ang_vel"), (lin_vel_threshold, ang_vel_threshold)):
        val = obj_dict["root_link"].get(key, 0.0)
        if not np.all(np.isclose(np.array(val), 0.0, atol=atol, rtol=0.0)):
            return False, f"{obj_name} root link {key} is not close to 0: {val}"

    # Check close to zero joint velocities
    for jnt_name, jnt_info in obj_dict["joints"].items():
        val = jnt_info["vel"]
        if not np.all(np.isclose(np.array(val), 0.0, atol=joint_vel_threshold, rtol=0.0)):
            return False, f"{obj_name} joint {jnt_name}'s velocity is not close to 0: {val}"

    # If all passes, return True
    return True, None

def create_stable_scene_json(args):
    cfg = {
        "scene": {
            "type": "InteractiveTraversableScene",
            "scene_model": args.scene_model,
        },
    }

    # Disable sleeping
    macros.prims.entity_prim.DEFAULT_SLEEP_THRESHOLD = 0.0

    # Create the environment
    # env = create_env_with_stable_objects(cfg)
    env = og.Environment(configs=copy.deepcopy(cfg))

    # Take a few steps to let objects settle, then update the scene initial state
    # This is to prevent nonzero velocities from causing objects to fall through the floor when we disable them
    # if they're not relevant for a given task
    for _ in range(300):
        og.sim.step(render=False)

    # Sanity check for zero velocities for all objects
    stable_state = og.sim.dump_state()
    invalid_msgs = []
    for obj_name, obj_info in stable_state["object_registry"].items():
        valid_obj, err_msg = _validate_object_state_stability(obj_name, obj_info, strict=False)
        if not valid_obj:
            invalid_msgs.append(err_msg)

    if len(invalid_msgs) > 0:
        print("Creating stable scene failed! Invalid messages:")
        for msg in invalid_msgs:
            print(msg)
        raise ValueError("Scene is not stable!")

    for obj in env.scene.objects:
        obj.keep_still()
    env.scene.update_initial_state()

    # Save this as a stable file
    path = os.path.join(gm.DATASET_PATH, "scenes", og.sim.scene.scene_model, "json", f"{args.scene_model}_stable.json")
    og.sim.save(json_path=path)

    og.sim.stop()
    og.sim.clear()

def validate_task(task, task_scene_dict, default_scene_dict):
    assert og.sim.is_playing()

    conditions = task.activity_conditions
    relevant_rooms = set(get_rooms(conditions.parsed_initial_conditions))
    active_obj_names = set()
    for obj in og.sim.scene.objects:
        if isinstance(obj, DatasetObject):
            obj_rooms = {"_".join(room.split("_")[:-1]) for room in obj.in_rooms}
            active = len(relevant_rooms.intersection(obj_rooms)) > 0 or obj.category in {"floors", "walls"}
            if active:
                active_obj_names.add(obj.name)

    # 1. Sanity check all object poses wrt their original pre-loaded poses
    print(f"Step 1: Checking loaded task environment...")
    def _validate_identical_object_kinematic_state(obj_name, default_obj_dict, obj_dict, check_vel=True):
        # Check root link state
        for key, val in default_obj_dict["root_link"].items():
            # Skip velocities if requested
            if not check_vel and "vel" in key:
                continue
            obj_val = obj_dict["root_link"][key]
            atol = 1. if "vel" in key else 0.05
            # # TODO: Update ori value to be larger tolerance
            # tol = 0.15 if "ori" in key else 0.05
            # If particle positions are being checked, only check the min / max
            if "particle" in key:
                # Only check position
                if "position" in key:
                    particle_positions = np.array(val)
                    current_particle_positions = np.array(obj_val)
                    pos_min, pos_max = np.min(particle_positions, axis=0), np.max(particle_positions, axis=0)
                    curr_pos_min, curr_pos_max = np.min(current_particle_positions, axis=0), np.max(current_particle_positions, axis=0)
                    for name, pos, curr_pos in zip(("min", "max"), (pos_min, pos_max), (curr_pos_min, curr_pos_max)):
                        if not np.all(np.isclose(pos, curr_pos, atol=0.05)):
                            return False, f"Got mismatch in cloth {obj_name} particle positions range: {name} {pos} vs. {curr_pos}"

                else:
                    continue
            if not np.all(np.isclose(np.array(val), np.array(obj_val), atol=atol, rtol=0.0)):
                return False, f"{obj_name} root link mismatch in {key}: default_obj_dict has: {val}, obj_dict has: {obj_val}"

        # Check any non-robot joint values
        # This is because the controller can cause the robot to drift over time
        if "robot" not in obj_name:
            # Check joint states
            for jnt_name, jnt_info in default_obj_dict["joints"].items():
                for key, val in jnt_info.items():
                    if "effort" in key or "target" in key:
                        # Don't check effort or any target values
                        continue
                    atol = 1. if "vel" in key else 0.05
                    obj_val = obj_dict["joints"][jnt_name][key]
                    if not np.all(np.isclose(np.array(val), np.array(obj_val), atol=atol, rtol=0.0)):
                        return False, f"{obj_name} joint mismatch in {jnt_name} - {key}: default_obj_dict has: {val}, obj_dict has: {obj_val}"

        # If all passes, return True
        return True, None

    task_state_t0 = og.sim.dump_state(serialized=False)
    for obj_name, obj_info in task_scene_dict["state"]["object_registry"].items():
        current_obj_info = task_state_t0["object_registry"][obj_name]
        valid_obj, err_msg = _validate_identical_object_kinematic_state(obj_name, obj_info, current_obj_info, check_vel=True)
        if not valid_obj:
            return False, f"Failed validation step 1: Task scene json and loaded task environment do not have similar kinematic states. Specific error: {err_msg}"

    # We should never use this after
    task_scene_dict = None

    # 2. Validate the native USDs jsons are stable and similar -- compare all object kinematics (poses, joint
    #       states) with respect to the native scene file
    print(f"Step 2: Checking poses and joint states for non-task-relevant objects and velocities for all objects...")

    # Sanity check all non-task-relevant object poses
    for obj_name, default_obj_info in default_scene_dict["state"]["object_registry"].items():
        # Skip any active objects since they may have changed
        if obj_name in active_obj_names:
            continue
        obj_info = task_state_t0["object_registry"][obj_name]
        valid_obj, err_msg = _validate_identical_object_kinematic_state(obj_name, default_obj_info, obj_info, check_vel=True)
        if not valid_obj:
            return False, f"Failed validation step 2: stable scene state and task scene state do not have similar kinematic states for non-task-relevant objects. Specific error: {err_msg}"

    # Sanity check for zero velocities for all objects
    for obj_name, obj_info in task_state_t0["object_registry"].items():
        valid_obj, err_msg = _validate_object_state_stability(obj_name, obj_info, strict=False)
        if not valid_obj:
            return False, f"Failed validation step 2: task scene state does not have close to zero velocities. Specific error: {err_msg}"

    # Need to enable transition rules before running step 3 and 4
    original_transition_rule_flag = gm.ENABLE_TRANSITION_RULES
    gm.ENABLE_TRANSITION_RULES = True

    # 3. Validate object set is consistent (no faulty transition rules occurring) -- we expect the number
    #       of active systems (and number of active particles) and the number of objects to be the same after
    #       taking a physics step, and also make sure init state is True
    print(f"Step 3: Checking BehaviorTask initial conditions and scene stability...")
    # Take a single physics step
    og.sim.step(render=False)
    task_state_t1 = og.sim.dump_state()
    def _validate_scene_stability(task, task_state, current_state, check_particle_positions=True):
        def _validate_particle_system_consistency(system_name, system_state, current_system_state, check_particle_positions=True):
            is_micro_physical = issubclass(get_system(system_name), MicroPhysicalParticleSystem)
            n_particles_key = "instancer_particle_counts" if is_micro_physical else "n_particles"
            if not np.all(np.isclose(system_state[n_particles_key], current_system_state[n_particles_key])):
                return False, f"Got inconsistent number of system {system_name} particles: {system_state['n_particles']} vs. {current_system_state['n_particles']}"
            # Validate that no particles went flying -- maximum ranges of positions should be roughly close
            n_particles = np.sum(system_state[n_particles_key])
            if n_particles > 0 and check_particle_positions:
                if is_micro_physical:
                    particle_positions = np.concatenate([inst_state["particle_positions"] for inst_state in system_state["particle_states"].values()], axis=0)
                    current_particle_positions = np.concatenate([inst_state["particle_positions"] for inst_state in current_system_state["particle_states"].values()], axis=0)
                else:
                    particle_positions = np.array(system_state["positions"])
                    current_particle_positions = np.array(current_system_state["positions"])
                pos_min, pos_max = np.min(particle_positions, axis=0), np.max(particle_positions, axis=0)
                curr_pos_min, curr_pos_max = np.min(current_particle_positions, axis=0), np.max(current_particle_positions, axis=0)
                for name, pos, curr_pos in zip(("min", "max"), (pos_min, pos_max), (curr_pos_min, curr_pos_max)):
                    if not np.all(np.isclose(pos, curr_pos, atol=0.05)):
                        return False, f"Got mismatch in system {system_name} particle positions range: {name} {pos} vs. {curr_pos}"

            return True, None

        # Sanity check consistent objects
        task_objects = {obj_name for obj_name in task_state["object_registry"].keys()}
        curr_objects = {obj_name for obj_name in current_state["object_registry"].keys()}
        mismatched_objs = set.union(task_objects, curr_objects) - set.intersection(task_objects, curr_objects)
        if len(mismatched_objs) > 0:
            return False, f"Got mismatch in active objects: {mismatched_objs}"

        for obj_name, obj_info in task_state["object_registry"].items():
            current_obj_info = current_state["object_registry"][obj_name]
            valid_obj, err_msg = _validate_identical_object_kinematic_state(obj_name, obj_info, current_obj_info, check_vel=True)
            if not valid_obj:
                return False, f"task state and current state do not have similar kinematic states: {err_msg}"

        # Sanity check consistent particle systems
        task_systems = {system_name for system_name in task_state["system_registry"].keys() if system_name != "cloth"}
        curr_systems = {system_name for system_name in current_state["system_registry"].keys() if system_name != "cloth"}
        mismatched_systems = set.union(task_systems, curr_systems) - set.intersection(task_systems, curr_systems)
        if len(mismatched_systems) > 0:
            return False, f"Got mismatch in active systems: {mismatched_systems}"

        for system_name in task_systems:
            system_state = task_state["system_registry"][system_name]
            curr_system_state = current_state["system_registry"][system_name]
            valid_system, err_msg = _validate_particle_system_consistency(system_name, system_state, curr_system_state, check_particle_positions=check_particle_positions)
            if not valid_system:
                return False, f"Particle systems do not have consistent state. Specific error: {err_msg}"

        # Sanity check initial state
        valid_init_state, results = evaluate_state(prune_unevaluatable_predicates(task.activity_initial_conditions))
        if not valid_init_state:
            return False, f"BDDL Task init conditions were invalid. Results: {results}"

        return True, None

    # Sanity check scene
    valid_scene, err_msg = _validate_scene_stability(task=task, task_state=task_state_t0, current_state=task_state_t1, check_particle_positions=True)
    if not valid_scene:
        gm.ENABLE_TRANSITION_RULES = original_transition_rule_flag
        return False, f"Failed verification step 3: {err_msg}"

    # 4. Validate longer-term stability -- take N=10 timesteps, and make sure all object positions and velocities
    #       are still stable (positions don't drift too much, and velocities are close to 0), as well as verifying
    #       that all BDDL conditions are satisfied
    print(f"Step 4: Checking longer-term BehaviorTask initial conditions and scene stability...")

    # Take 10 steps
    for _ in range(10):
        og.sim.step(render=False)

    # Sanity check scene
    # Don't check particle positions since some particles may be falling
    # TODO: Tighten this constraint once we figure out a way to stably sample particles
    task_state_t11 = og.sim.dump_state(serialized=False)
    valid_scene, err_msg = _validate_scene_stability(task=task, task_state=task_state_t0, current_state=task_state_t11, check_particle_positions=False)
    if not valid_scene:
        gm.ENABLE_TRANSITION_RULES = original_transition_rule_flag
        return False, f"Failed verification step 4: {err_msg}"

    gm.ENABLE_TRANSITION_RULES = original_transition_rule_flag

    return True, None
