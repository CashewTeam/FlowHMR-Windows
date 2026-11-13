"""Avoid unused SAM outputs on the unmasked FlowHMR feature path."""

from contextlib import ExitStack, contextmanager

import torch


class _HandSkeleton(torch.nn.Module):
    def __init__(self, character, vertex_count):
        super().__init__()
        self.character = character
        self.vertex_count = vertex_count

    def forward(self, identity, params, _face):
        # These are the same two steps as the shipped MHRDemo's skeleton branch.
        full_params = torch.cat((params, torch.zeros_like(identity)), dim=1)
        joint_params = self.character.model_parameters_to_joint_parameters(full_params)
        state = self.character.joint_parameters_to_skeleton_state(joint_params)
        # MHRHead expects vertices, but its active hand keypoint rows give
        # every vertex zero weight. Keep its tensor layout and regression intact.
        return params.new_zeros((params.shape[0], self.vertex_count, 3)), state


def make_hand_skeleton(head):
    """Use skeleton-only MHR only with the verified TorchScript hand mapping."""
    core = getattr(head, "mhr", None)
    if not getattr(head, "enable_hand_model", False) or not isinstance(core, torch.jit.ScriptModule):
        return None
    character = getattr(core, "character_torch", None)
    if not (callable(getattr(character, "model_parameters_to_joint_parameters", None)) and
            callable(getattr(character, "joint_parameters_to_skeleton_state", None))):
        return None
    vertices = getattr(getattr(character, "mesh", None), "rest_vertices", None)
    mapping = getattr(head, "keypoint_mapping", None)
    if vertices is None or mapping is None or vertices.shape != (18439, 3) or mapping.shape != (308, 18439 + 127):
        return None
    count = vertices.shape[0]
    active = mapping[21:42].detach().cpu()
    if (torch.count_nonzero(active[:, :count]) or torch.count_nonzero(active[:, count:]) != 21 or
            not torch.all((active[:, count:] == 1).sum(dim=1) == 1)):
        return None
    return _HandSkeleton(character, count)


@contextmanager
def unmasked_features(model, hand_skeleton=None):
    """Keep decoder feedback and tokens; omit outputs not read by FlowHMR."""
    def no_mask(_batch, embeddings):
        _, unmasked = model.prompt_encoder.get_mask_embeddings(None, embeddings.shape[0], embeddings.shape[2:])
        return unmasked.to(embeddings)

    def omit_last_pose(decoder, args, kwargs):
        predict = kwargs.get("token_to_pose_output_fn")
        if predict is None:
            return None
        last = len(decoder.layers) - 1

        def predict_if_used(tokens, prev_pose_output, layer_idx):
            if layer_idx == last:
                return {}  # Body hand boxes are computed separately from the final tokens.
            return predict(tokens, prev_pose_output=prev_pose_output, layer_idx=layer_idx)

        return args, {**kwargs, "token_to_pose_output_fn": predict_if_used}

    def omit_surface_projection(_head, _args, outputs):
        # The decoder feeds back only skeletal keypoints. Keep mesh computation
        # inside MHR where the body keypoint regressor still depends on it.
        outputs["pred_vertices"] = None

    with ExitStack() as stack:
        # Restore instance state even if the hand-token hook or an OOM aborts a forward.
        if "_get_mask_prompt" in vars(model):
            stack.callback(setattr, model, "_get_mask_prompt", model._get_mask_prompt)
        else:
            stack.callback(delattr, model, "_get_mask_prompt")
        model._get_mask_prompt = no_mask
        for decoder in (model.decoder, model.decoder_hand):
            stack.callback(decoder.register_forward_pre_hook(omit_last_pose, with_kwargs=True).remove)
        for head in (model.head_pose, model.head_pose_hand):
            stack.callback(head.register_forward_hook(omit_surface_projection).remove)
        if hand_skeleton is not None:
            hand = model.head_pose_hand
            stack.callback(setattr, hand, "mhr", hand.mhr)
            hand.mhr = hand_skeleton
        yield
