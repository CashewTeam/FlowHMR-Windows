"""Character import and strict pose patches using the desktop library format."""
import struct
import json
import shutil
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

CharacterID = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
BoneID = Annotated[str, Field(pattern=r'^(?:[0-9]|[12][0-9]|3[0-6])$')]
Angle = Annotated[float, Field(ge=-180, le=180)]
Distance = Annotated[float, Field(ge=-5, le=5)]
Rotation = Annotated[list[Angle], Field(min_length=3, max_length=3)]
Translation = Annotated[list[Distance], Field(min_length=3, max_length=3)]


class PosePatch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    rotations: dict[BoneID, Rotation] = Field(default_factory=dict)
    root_rotation: Rotation = Field(default_factory=lambda: [0, 0, 0])
    root_translation: Translation = Field(default_factory=lambda: [0, 0, 0])
    mirror: bool = False
    ik_mode: Literal['fk', 'ik'] = 'fk'

    def patch(self, current):
        result = dict(current)
        supplied = self.model_dump(exclude_unset=True)
        if 'rotations' in supplied:
            result['rotations'] = {**current.get('rotations', {}), **supplied.pop('rotations')}
        for key, value in supplied.items():
            result[{'root_rotation': 'rootRotation', 'root_translation': 'rootTranslation',
                    'ik_mode': 'ikMode'}.get(key, key)] = value
        return result


BODY_NAMES = ['pelvis', 'left_hip', 'right_hip', 'waist', 'left_knee', 'right_knee',
              'spine', 'left_ankle', 'right_ankle', 'chest', 'left_toes', 'right_toes',
              'neck', 'left_clavicle', 'right_clavicle', 'head', 'left_upper_arm',
              'right_upper_arm', 'left_elbow', 'right_elbow', 'left_wrist', 'right_wrist']
BONES = [{'id': str(i), 'name': name} for i, name in enumerate(BODY_NAMES)] + [
    {'id': str(22 + finger * 3 + joint), 'name': f'{name}_{joint + 1}', 'shared_hands': True}
    for finger, name in enumerate(('index', 'middle', 'pinky', 'ring', 'thumb')) for joint in range(3)]


def texture_references(path):
    """Read declared texture references only; never collect unrelated nearby files."""
    if path.suffix.lower() == '.vrm':
        with path.open('rb') as file:
            header = file.read(20)
            if len(header) != 20:
                raise ValueError('Truncated VRM header')
            magic, version, total, size, chunk = struct.unpack('<4sIIII', header)
            if magic != b'glTF' or version != 2 or total != path.stat().st_size or chunk != 0x4e4f534a or size > total - 20:
                raise ValueError('VRM must be a valid binary glTF 2 file')
            document = json.loads(file.read(size).decode('utf-8'))
            extensions = document.get('extensions') if isinstance(document, dict) else None
            if not isinstance(extensions, dict) or not ({'VRM', 'VRMC_vrm'} & extensions.keys()):
                raise ValueError('glTF file does not declare a VRM extension')
            if any(item.get('uri') and not item['uri'].startswith('data:')
                   for key in ('images', 'buffers') for item in document.get(key, [])):
                raise ValueError('VRM character must embed its images and buffers')
        return []
    if path.suffix.lower() == '.fbx':
        import fbx
        scratch = TemporaryDirectory(prefix='flowhmr-fbx-inspect-')
        inspected = Path(scratch.name) / path.name
        shutil.copy2(path, inspected)
        manager = fbx.FbxManager.Create()
        try:
            settings = fbx.FbxIOSettings.Create(manager, fbx.IOSROOT)
            manager.SetIOSettings(settings)
            importer = fbx.FbxImporter.Create(manager, '')
            if not importer.Initialize(str(inspected), -1, settings):
                raise ValueError('Cannot read FBX character')
            scene = fbx.FbxScene.Create(manager, '')
            if not importer.Import(scene):
                raise ValueError('Cannot import FBX character')
            criterion = fbx.FbxCriteria.ObjectType(fbx.FbxFileTexture.ClassId)
            return [scene.GetSrcObject(criterion, i).GetRelativeFileName() or
                    scene.GetSrcObject(criterion, i).GetFileName()
                    for i in range(scene.GetSrcObjectCount(criterion))]
        finally:
            manager.Destroy()
            scratch.cleanup()
    with path.open('rb') as file:
        def read(size):
            data = file.read(size)
            if len(data) != size:
                raise ValueError('Truncated PMX character')
            return data

        def count():
            value = struct.unpack('<i', read(4))[0]
            if not 0 <= value <= 10000000:
                raise ValueError('Invalid PMX section size')
            return value

        if read(4) != b'PMX ':
            raise ValueError('Invalid PMX signature')
        version = struct.unpack('<f', read(4))[0]
        header = read(read(1)[0])
        if not 1.99 < version < 2.11 or len(header) < 8 or header[0] not in (0, 1) or header[1] > 4:
            raise ValueError('Unsupported PMX header')
        if any(size not in (1, 2, 4) for size in header[2:8]):
            raise ValueError('Invalid PMX index sizes')
        encoding = 'utf-16-le' if header[0] == 0 else 'utf-8'
        def text():
            size = count()
            return read(size).decode(encoding)
        for _ in range(4):
            text()
        for _ in range(count()):
            read(32 + 16 * header[1])
            deform = read(1)[0]
            sizes = {0: header[5], 1: header[5] * 2 + 4, 2: header[5] * 4 + 16,
                     3: header[5] * 2 + 40, 4: header[5] * 4 + 16}
            if deform not in sizes:
                raise ValueError('Invalid PMX skinning mode')
            read(sizes[deform] + 4)
        read(count() * header[2])
        return [text() for _ in range(count())]


def import_character(library, selections, file, name):
    selection = selections.register(file)
    path = selections.resource(selection['ticket'])
    missing = []
    for reference in dict.fromkeys(texture_references(path)):
        if not reference:
            continue
        relative = reference
        if Path(reference).is_absolute():
            try:
                relative = str(Path(reference).resolve().relative_to(path.parent))
            except ValueError:
                missing.append(reference)
                continue
        if selections.resource(selection['ticket'], relative) is None:
            missing.append(reference)
    record = library.save(selections, selection['ticket'], name)
    return {**record, 'missing_textures': missing}


def empty_pose(kind):
    pose = {'rotations': {}, 'rootRotation': [0, 0, 0], 'rootTranslation': [0, 0, 0],
            'mirror': False, 'ikMode': 'ik' if kind == 'pmx' else 'fk'}
    if kind in ('pmx', 'vrm'):
        pose[kind + 'BaselineVersion'] = 1
    return pose
