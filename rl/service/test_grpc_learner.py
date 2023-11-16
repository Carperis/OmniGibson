import argparse
import os, time
import logging

log = logging.getLogger(__name__)

from grpc_vec_env import GRPCVecEnv

try:
    import gym
    import torch as th
    import torch.nn as nn
    import wandb
    from stable_baselines3 import PPO
    from stable_baselines3.common.evaluation import evaluate_policy
    from stable_baselines3.common.preprocessing import maybe_transpose
    from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
    from stable_baselines3.common.utils import set_random_seed
    from stable_baselines3.common.vec_env import VecVideoRecorder, VecMonitor
    from stable_baselines3.common.callbacks import CallbackList, CheckpointCallback, EvalCallback
    from wandb.integration.sb3 import WandbCallback 

except ModuleNotFoundError:
    log.error("torch, stable-baselines3, or wandb is not installed. "
                 "See which packages are missing, and then run the following for any missing packages:\n"
                 "pip install torch\n"
                 "pip install stable-baselines3==1.7.0\n"
                 "pip install wandb\n"
                 "Also, please update gym to >=0.26.1 after installing sb3: pip install gym>=0.26.1")
    exit(1)


class CustomCombinedExtractor(BaseFeaturesExtractor):
    def __init__(self, observation_space: gym.spaces.Dict):
        # We do not know features-dim here before going over all the items,
        # so put something dummy for now. PyTorch requires calling
        super().__init__(observation_space, features_dim=1)
        extractors = {}
        self.step_index = 0
        total_concat_size = 0
        feature_size = 128
        for key, subspace in observation_space.spaces.items():
            # For now, only keep RGB observations
            if "rgb" in key:
                log.info(f"obs {key} shape: {subspace.shape}")
                n_input_channels = subspace.shape[0]  # channel first
                cnn = nn.Sequential(
                    nn.Conv2d(n_input_channels, 4, kernel_size=8, stride=4, padding=0),
                    nn.ReLU(),
                    nn.MaxPool2d(2),
                    nn.Conv2d(4, 8, kernel_size=4, stride=2, padding=0),
                    nn.ReLU(),
                    nn.MaxPool2d(2),
                    nn.Conv2d(8, 4, kernel_size=3, stride=1, padding=0),
                    nn.ReLU(),
                    nn.Flatten(),
                )
                test_tensor = th.zeros(subspace.shape)
                with th.no_grad():
                    n_flatten = cnn(test_tensor[None]).shape[1]
                fc = nn.Sequential(nn.Linear(n_flatten, feature_size), nn.ReLU())
                extractors[key] = nn.Sequential(cnn, fc)
                total_concat_size += feature_size
        self.extractors = nn.ModuleDict(extractors)

        # Update the features dim manually
        self._features_dim = total_concat_size

    def forward(self, observations) -> th.Tensor:
        encoded_tensor_list = []
        self.step_index += 1

        # self.extractors contain nn.Modules that do all the processing.
        for key, extractor in self.extractors.items():
            encoded_tensor_list.append(extractor(observations[key]))

        feature = th.cat(encoded_tensor_list, dim=1)
        return feature


def main():
    # Parse args
    parser = argparse.ArgumentParser(description="Train or evaluate a PPO agent in BEHAVIOR")

    parser.add_argument(
        "--checkpoint",
        type=str,
        default=None,
        help="Absolute path to desired PPO checkpoint to load for evaluation",
    )

    parser.add_argument(
        "--eval",
        action="store_true",
        help="If set, will evaluate the PPO agent found from --checkpoint",
    )

    args = parser.parse_args()
    prefix = ''
    seed = 0

    env = GRPCVecEnv(["localhost:50051"])

    # TODO: None of this stuff works: make it work by running env locally and connecting to it.
    # If we're evaluating, hide the ceilings and enable camera teleoperation so the user can easily
    # visualize the rollouts dynamically
    # if args.eval:
    #     ceiling = env.scene.object_registry("name", "ceilings")
    #     ceiling.visible = False
    #     og.sim.enable_viewer_camera_teleoperation()

    # Set the set
    set_random_seed(seed)
    env.reset()

    policy_kwargs = dict(
        features_extractor_class=CustomCombinedExtractor,
    )

    if args.eval:
        raise ValueError("This does not currently work.")
    
        # TODO: Reenable once this all works
        # assert args.checkpoint is not None, "If evaluating a PPO policy, @checkpoint argument must be specified!"
        # model = PPO.load(args.checkpoint)
        # log.info("Starting evaluation...")
        # mean_reward, std_reward = evaluate_policy(model, env, n_eval_episodes=50)
        # log.info("Finished evaluation!")
        # log.info(f"Mean reward: {mean_reward} +/- {std_reward:.2f}")

    else:
        config = {
            "policy_type": "MultiInputPolicy",
            "n_steps": 20 * 10,
            "batch_size": 8,
            "total_timesteps": 10000000,
        }
        env = VecMonitor(env)
        env = VecVideoRecorder(
            env,
            f"videos/{run.id}",
            record_video_trigger=lambda x: x % 2000 == 0,
            video_length=200,
        )
        run = wandb.init(
            project="sb3",
            config=config,
            sync_tensorboard=True,  # auto-upload sb3's tensorboard metrics
            monitor_gym=True,  # auto-upload the videos of agents playing the game
            # save_code=True,  # optional
        )
        tensorboard_log_dir = f"runs/{run.id}"
        model = PPO(
            config["policy_type"],
            env,
            verbose=1,
            tensorboard_log=tensorboard_log_dir,
            policy_kwargs=policy_kwargs,
            n_steps=config["n_steps"],
            batch_size=config["batch_size"],
            device='cuda',
        )
        eval_callback = EvalCallback(eval_env=env, eval_freq=1000, n_eval_episodes=20)
        wandb_callback = WandbCallback(
            model_save_path=tensorboard_log_dir,
            verbose=2,
        ),
        callback = CallbackList([wandb_callback, eval_callback])

        log.debug(model.policy)
        log.info(f"model: {model}")

        log.info("Starting training...")
        model.learn(
            total_timesteps=10000000,
            callback=callback,
        )
        log.info("Finished training!")


if __name__ == "__main__":
    main()