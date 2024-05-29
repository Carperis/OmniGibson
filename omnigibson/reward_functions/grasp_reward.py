import math

import numpy as np
from scipy.spatial.transform import Rotation as R

import omnigibson.utils.transform_utils as T
from omnigibson.reward_functions.reward_function_base import BaseRewardFunction
from omnigibson.utils.motion_planning_utils import detect_robot_collision_in_sim


class GraspReward(BaseRewardFunction):
    """
    Grasp reward
    """

    def __init__(
        self,
        obj_name,
        dist_coeff,
        dist_slope_coeff,
        grasp_reward,
        collision_penalty,
        eef_position_penalty_coef,
        eef_orientation_penalty_coef,
        regularization_coef,
    ):
        # Store internal vars
        self.prev_grasping = False
        self.prev_eef_pos = None
        self.prev_eef_rot = None
        self.obj_name = obj_name
        self.obj = None
        self.dist_coeff = dist_coeff
        self.dist_slope_coeff = dist_slope_coeff
        self.grasp_reward = grasp_reward
        self.collision_penalty = collision_penalty
        self.eef_position_penalty_coef = eef_position_penalty_coef
        self.eef_orientation_penalty_coef = eef_orientation_penalty_coef
        self.regularization_coef = regularization_coef

        # Run super
        super().__init__()

    def _step(self, task, env, action):
        self.obj = env.scene.object_registry("name", self.obj_name) if self.obj is None else self.obj

        robot = env.robots[0]
        obj_in_hand = robot._ag_obj_in_hand[robot.default_arm]
        current_grasping = obj_in_hand == self.obj

        info = {"grasp_success": current_grasping}

        # Reward varying based on combination of whether the robot was previously grasping the desired and object
        # and is currently grasping the desired object
        reward = 0.0

        # Penalize large accelerations
        action_mag = np.linalg.norm(robot.get_joint_efforts())
        regularization_penalty = -(action_mag * self.regularization_coef)
        reward += regularization_penalty
        info["regularization_penalty_factor"] = action_mag
        info["regularization_penalty"] = regularization_penalty

        # Penalize based on the magnitude of the action
        eef_pos = robot.get_eef_position(robot.default_arm)
        info["position_penalty_factor"] = 0.0
        info["position_penalty"] = 0.0
        if self.prev_eef_pos is not None:
            eef_pos_delta = T.l2_distance(self.prev_eef_pos, eef_pos)
            position_penalty = -eef_pos_delta * self.eef_position_penalty_coef
            reward += position_penalty
            info["position_penalty_factor"] = eef_pos_delta
            info["position_penalty"] = position_penalty
        self.prev_eef_pos = eef_pos

        eef_rot = R.from_quat(robot.get_eef_orientation(robot.default_arm))
        info["rotation_penalty_factor"] = 0.0
        info["rotation_penalty"] = 0.0
        if self.prev_eef_rot is not None:
            delta_rot = (eef_rot * self.prev_eef_rot.inv()).magnitude()
            rotation_penalty = -delta_rot * self.eef_orientation_penalty_coef
            reward += rotation_penalty
            info["rotation_penalty_factor"] = delta_rot
            info["rotation_penalty"] = rotation_penalty
        self.prev_eef_rot = eef_rot

        # Penalize robot for colliding with an object
        info["collision_penalty_factor"] = 0.0
        info["collision_penalty"] = 0.0
        if detect_robot_collision_in_sim(robot, filter_objs=[self.obj]):
            reward += -self.collision_penalty
            info["collision_penalty_factor"] = 1.0
            info["collision_penalty"] = -self.collision_penalty

        # If we're not currently grasping
        info["grasp_reward_factor"] = 0.0
        info["grasp_reward"] = 0.0
        info["pregrasp_dist"] = 0.0
        info["pregrasp_dist_reward_factor"] = 0.0
        info["pregrasp_dist_reward"] = 0.0
        info["postgrasp_dist"] = 0.0
        info["postgrasp_dist_reward_factor"] = 0.0
        info["postgrasp_dist_reward"] = 0.0
        if not current_grasping:
            # TODO: If we dropped the object recently, penalize for that
            obj_center = self.obj.get_position()
            dist = T.l2_distance(eef_pos, obj_center)
            dist_reward = math.exp(-self.dist_slope_coeff * dist) * self.dist_coeff
            reward += dist_reward
            info["pregrasp_dist"] = dist
            info["pregrasp_dist_reward_factor"] = math.exp(-dist)
            info["pregrasp_dist_reward"] = dist_reward
        else:
            # We are currently grasping - first apply a grasp reward
            reward += self.grasp_reward
            info["grasp_reward_factor"] = 1.0
            info["grasp_reward"] = self.grasp_reward

            # Then apply a distance reward to take us to a tucked position
            robot_center = robot.links["torso_lift_link"].get_position()
            obj_center = self.obj.get_position()
            dist = T.l2_distance(robot_center, obj_center)
            dist_reward = math.exp(-self.dist_slope_coeff * dist) * self.dist_coeff
            reward += dist_reward
            info["postgrasp_dist"] = dist
            info["postgrasp_dist_reward_factor"] = math.exp(-dist)
            info["postgrasp_dist_reward"] = dist_reward

        self.prev_grasping = current_grasping

        return reward, info

    def reset(self, task, env):
        """
        Reward function-specific reset

        Args:
            task (BaseTask): Task instance
            env (Environment): Environment instance
        """
        super().reset(task, env)
        self.prev_grasping = False
        self.prev_eef_pos = None
        self.prev_eef_rot = None
