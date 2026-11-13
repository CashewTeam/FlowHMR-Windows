"""Strict MCP inputs shared with the web parameter metadata endpoint."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

SERVICE_KEY_ENV = 'FLOWHMR_MCP_SERVICE_KEY'
SERVICE_KEY_HEADER = 'X-FlowHMR-MCP-Key'
INSTANCE_ENV = 'FLOWHMR_MCP_INSTANCE'
JobID = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')]
Index = Annotated[int, Field(strict=True, ge=0)]
People = Annotated[list[Index], Field(min_length=1)]
Policy = Literal['explicit', 'automatic']
TOOL_NAMES = ['flowhmr_get_status', 'flowhmr_get_parameter_schema', 'flowhmr_import_video',
              'flowhmr_list_jobs', 'flowhmr_get_job', 'flowhmr_select_people',
              'flowhmr_rerun_job', 'flowhmr_cancel_job', 'flowhmr_prepare_scene',
              'flowhmr_export_motion', 'flowhmr_get_export', 'flowhmr_import_character',
              'flowhmr_list_characters', 'flowhmr_get_character_pose',
              'flowhmr_select_character', 'flowhmr_update_character_pose',
              'flowhmr_get_viewer_snapshot', 'flowhmr_get_motion_keyframes', 'flowhmr_control_playback']


class Parameters(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    seed: int = Field(default=0, ge=0, le=2147483647)
    steps: int = Field(default=20, ge=1, le=100)
    cfg_scale: float = Field(default=1, ge=0, le=10)
    start_frame: int = Field(default=0, ge=0, description='Zero-based frame in the normalized 30 FPS video.')
    end_frame: int | None = Field(default=None, ge=0, description='Inclusive final frame in the normalized 30 FPS video.')
    max_frames: int | None = Field(default=None, ge=1)
    auto_frames: bool = True
    static_camera: bool = True
    vggt_interval: int = Field(default=0, ge=0, le=120, description='0: automatic sampling.')
    sam_chunk_size: int | None = Field(default=None, ge=1, description='null: adjust batches to available VRAM and system memory.')
    sam_hand_stride: Literal[1, 2, 4] = Field(default=1, description='SAM hand decoding interval in frames.')
    sam_backend: Literal['gpu', 'hybrid'] = Field(default='gpu', description='Hybrid moves the SAM image encoder to a compatible CPU on Windows AMD iGPUs.')
    smoothing_frames: float = Field(default=0.12, ge=0, description='Gaussian smoothing sigma, in frames.')
    postprocess: bool = True
    correction_floor_m: float | None = Field(default=None, description='Ground height in metres; null: estimate.')
    correction_preset: Literal['normal', 'strong'] = 'strong'
    sole_fit_strength: float = Field(default=0.6, ge=0, le=1)
    hamer: bool = False
    hamer_stride: Literal[1, 2, 3, 4] = 1

    @field_validator('sam_hand_stride', 'hamer_stride', mode='before')
    @classmethod
    def integer_stride(cls, value, info):
        if type(value) is not int:
            raise ValueError(f'{info.field_name} must be an integer')
        return value

    @model_validator(mode='after')
    def frame_order(self):
        if self.end_frame is not None and self.end_frame < self.start_frame:
            raise ValueError('end_frame must not precede start_frame')
        return self


def parameter_defaults(defaults):
    return {key: value for key, value in defaults.items() if key in Parameters.model_fields}


def single_result_person_id(job):
    selected = job.get('selected_person')
    return selected if selected is not None else 0


class ExportOptions(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    job_id: JobID
    format: Literal['fbx', 'vmd', 'vrma', 'npz']
    output_path: str
    overwrite: bool = False
    person_ids: People | None = None
    fbx_profile: Literal['flowhmr', 'mixamo', 'metahuman', 'maya_adv', 'mixamo_iclone'] = 'flowhmr'
    include_mesh: bool = True
    reference_pose: Literal['none', 't', 'a'] = 'none'
    template_path: str | None = None

    @model_validator(mode='after')
    def supported_options(self):
        if self.format != 'fbx' and self.model_fields_set & {
                'fbx_profile', 'include_mesh', 'reference_pose', 'template_path'}:
            raise ValueError('FBX options require format=fbx')
        if self.format == 'npz' and self.person_ids is not None:
            raise ValueError('NPZ exports the complete result; person_ids is not supported')
        if self.format == 'fbx':
            template = self.fbx_profile in ('metahuman', 'mixamo_iclone') or self.template_path is not None
            if self.template_path and self.fbx_profile not in ('mixamo', 'metahuman', 'mixamo_iclone'):
                raise ValueError('This FBX profile does not accept a template')
            if self.fbx_profile in ('metahuman', 'mixamo_iclone') and not self.template_path:
                raise ValueError('This FBX profile requires template_path')
            if template and (self.include_mesh or self.reference_pose != 'none'):
                raise ValueError('Template exports require include_mesh=false and reference_pose=none')
            if self.fbx_profile == 'maya_adv' and (self.include_mesh or self.reference_pose != 't'):
                raise ValueError('maya_adv requires include_mesh=false and reference_pose=t')
        return self
